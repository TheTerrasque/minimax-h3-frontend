"""Dirty-cascade rule and render orchestration for Director Mode -- kept out
of models.py so the model definitions stay easy to read on their own, and
out of api.py so the rule is unit-testable/reusable without going through a
view. See the approved plan's "Data model" and "Backend rendering engine"
sections for the full design this implements.
"""

from __future__ import annotations

from django.core.files.base import ContentFile
from django_q.tasks import async_task

from generation.models import GenerationJob, ReferenceAsset
from integrations import media_post, motion_context

from .models import Clip, Project

_ACTIVE_JOB_STATUSES = {GenerationJob.Status.QUEUED, GenerationJob.Status.PROCESSING}


class RenderConflict(Exception):
    """Raised when a render is requested for a Clip whose dirty chain
    overlaps one already in flight -- see render_clip(). Translated to a
    409 by director/api.py, same shape as generation/api.py's own 409s."""


def _checkpoint_prefix(project_id: int) -> str:
    return f"director/project_{project_id}"


def _predecessor(clip: Clip) -> Clip | None:
    return Clip.objects.filter(project_id=clip.project_id, order__lt=clip.order).order_by("-order").first()


def _chain_head(clip: Clip) -> Clip:
    """Walks backward from `clip` while it's a still-dirty continuation of
    an *also*-dirty predecessor, returning the earliest Clip in that run --
    either the project's first Clip, a Clip that doesn't continue its own
    predecessor (a fresh scene), or one whose predecessor is already clean
    (its checkpoint is still valid, nothing upstream needs re-rendering).
    """
    current = clip
    while current.continues_previous and current.needs_render:
        predecessor = _predecessor(current)
        if predecessor is None or not predecessor.needs_render:
            break
        current = predecessor
    return current


def _build_job_for_clip(clip: Clip) -> GenerationJob:
    """Creates a fresh GenerationJob for `clip`'s current content (mirrors
    generation/api.py's jobs() POST handler, minus the HTTP layer), links
    it as the Clip's current_job, and enqueues it. Assumes the caller
    (render_clip()) has already confirmed this Clip is actually ready to
    render (if continues_previous, its predecessor must be clean).

    Graceful fallback when the Contex-Loop extension isn't installed --
    or wasn't installed yet when the *predecessor* rendered, so its
    ComfyUI-side output was never kept around to reference -- see
    extras.md#contex-loop's "Graceful fallback" section: instead of true
    motion/audio continuity, feeds the previous clip's last frame in as an
    ordinary image reference. Self-healing: as soon as both a clip and its
    predecessor have real checkpoints, later renders automatically switch
    back to full continuity without any user action.
    """
    available = motion_context.is_available()
    continuation_params: dict | None = None
    hack_last_frame: bytes | None = None

    if clip.continues_previous:
        predecessor = _predecessor(clip)
        if predecessor is None or predecessor.needs_render or predecessor.current_job_id is None:
            raise RenderConflict(f"Clip {clip.id}'s predecessor isn't rendered yet.")
        pred_job = predecessor.current_job

        if available and pred_job.keep_comfyui_output:
            continuation_params = {
                "source_filename": pred_job.comfyui_output_filename,
                "source_subfolder": pred_job.comfyui_output_subfolder,
                "save_prefix": _checkpoint_prefix(clip.project_id),
                "save_index": clip.id,
            }
            if predecessor.checkpoint_filename_prefix:
                continuation_params["load_prefix"] = predecessor.checkpoint_filename_prefix
                continuation_params["load_index"] = predecessor.checkpoint_clip_index
        else:
            pred_job.video_file.open("rb")
            try:
                video_bytes = pred_job.video_file.read()
            finally:
                pred_job.video_file.close()
            hack_last_frame = media_post.extract_last_frame(video_bytes)
    elif available:
        # Not itself a continuation, but save a checkpoint (nothing else --
        # see apply_motion_context()'s docstring on why a fresh scene can't
        # run the rest of the splice) in case a *later* clip continues from
        # this one.
        continuation_params = {"save_prefix": _checkpoint_prefix(clip.project_id), "save_index": clip.id}

    job = GenerationJob.objects.create(
        user=clip.project.user,
        mode=clip.mode,
        preset=clip.preset,
        duration=clip.duration,
        raw_prompt=clip.prompt,
        improved_prompt=clip.improved_prompt,
        megapixels=clip.preset.megapixels,
        steps=clip.preset.steps,
        aspect_ratio=clip.aspect_ratio,
        width=clip.width,
        height=clip.height,
        duration_seconds=clip.duration.duration_seconds,
        estimated_seconds=clip.duration.estimated_render_seconds,
        # Only worth keeping when the extension is actually available --
        # the fallback above reads Django's own already-downloaded
        # video_file instead of ComfyUI's copy, so there's no reason to
        # leave a stray file on the ComfyUI machine when it is unavailable.
        keep_comfyui_output=available,
        continuation_params=continuation_params,
    )

    references = list(clip.references.all())
    if hack_last_frame is not None:
        # Leads as the new job's first (order=0) image reference -- i2v's
        # convention is order=0 -> first_frame; r2v's is order=0 -> the
        # first <Picture N> token. Either way it's the strongest reference
        # slot, matching the fallback's intent (start this clip from
        # exactly where the previous one ended). Any image references
        # already on the clip itself shift down to make room.
        for ref in references:
            if ref.kind == ReferenceAsset.Kind.IMAGE:
                ref.order += 1
        hack_ref = ReferenceAsset(job=job, kind=ReferenceAsset.Kind.IMAGE, order=0)
        hack_ref.file.save("continuation_last_frame.png", ContentFile(hack_last_frame), save=True)

    for ref in references:
        ref.file.open("rb")
        try:
            content = ref.file.read()
        finally:
            ref.file.close()
        new_ref = ReferenceAsset(job=job, kind=ref.kind, order=ref.order)
        new_ref.file.save(ref.file.name.rsplit("/", 1)[-1], ContentFile(content), save=True)

    clip.current_job = job
    if available:
        clip.checkpoint_filename_prefix = _checkpoint_prefix(clip.project_id)
        clip.checkpoint_clip_index = clip.id
    else:
        # No real checkpoint was saved -- don't leave a stale pointer a
        # future continuation clip might trust.
        clip.checkpoint_filename_prefix = ""
        clip.checkpoint_clip_index = None
    clip.save(update_fields=["current_job", "checkpoint_filename_prefix", "checkpoint_clip_index"])

    # Same shared FIFO queue processor generation/api.py's own job creation
    # enqueues -- safe to enqueue redundantly (see that module's docstring).
    async_task("generation.tasks.process_queue")
    return job


def render_clip(clip: Clip) -> GenerationJob | None:
    """Renders `clip`, first (re-)rendering any dirty continuation
    predecessors it depends on -- see _chain_head(). Only the head's job is
    created now; the rest of the chain is created progressively as each
    predecessor finishes (see director/signals.py's _advance_chain()).
    Returns None if `clip` wasn't actually dirty (nothing to do). Raises
    RenderConflict if any Clip in the run already has a job in flight.
    """
    clip.refresh_from_db()
    if not clip.needs_render:
        return None

    head = _chain_head(clip)
    run = list(
        Clip.objects.filter(project_id=clip.project_id, order__gte=head.order, order__lte=clip.order)
        .select_related("current_job")
        .order_by("order")
    )
    for run_clip in run:
        if run_clip.current_job_id and run_clip.current_job.status in _ACTIVE_JOB_STATUSES:
            raise RenderConflict(f"Clip {run_clip.id} already has a job {run_clip.current_job.status}.")

    head.render_chain_target = clip
    head.save(update_fields=["render_chain_target"])
    return _build_job_for_clip(head)


def render_all_dirty(project: Project) -> list[GenerationJob]:
    """Renders every dirty Clip in the project -- one render_clip() call
    per maximal dirty run (see _chain_head()), so each run's head is
    enqueued immediately; the existing single-worker FIFO naturally
    serializes them, and each head's own chain advances independently as
    its predecessor jobs finish."""
    clips = list(project.clips.order_by("order"))
    jobs = []
    i = 0
    while i < len(clips):
        if not clips[i].needs_render:
            i += 1
            continue
        j = i
        while j + 1 < len(clips) and clips[j + 1].continues_previous and clips[j + 1].needs_render:
            j += 1
        job = render_clip(clips[j])
        if job is not None:
            jobs.append(job)
        i = j + 1
    return jobs


def mark_dirty_cascade(clip: Clip) -> None:
    """Marks `clip` needing re-render, then walks forward through
    subsequent Clips in `order` while each one has continues_previous=True,
    marking each dirty too -- stopping at (not including) the first Clip
    that starts a fresh scene. Matches the user's own spec exactly: editing
    [scene start] dirties it plus every continuation directly chained after
    it, up to but not including the next fresh scene.
    """
    clip.needs_render = True
    clip.save(update_fields=["needs_render"])

    following = Clip.objects.filter(project_id=clip.project_id, order__gt=clip.order).order_by("order")
    for next_clip in following:
        if not next_clip.continues_previous:
            break
        next_clip.needs_render = True
        next_clip.save(update_fields=["needs_render"])


def mark_project_dirty(project: Project) -> None:
    """Every Clip's render depends on Project.overarching_prompt/resources,
    so changing either invalidates the whole project, not just a cascade
    from one point."""
    project.clips.update(needs_render=True)
