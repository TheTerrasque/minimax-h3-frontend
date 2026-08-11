"""Dirty-cascade rule and render orchestration for Director Mode -- kept out
of models.py so the model definitions stay easy to read on their own, and
out of api.py so the rule is unit-testable/reusable without going through a
view. See the approved plan's "Data model" and "Backend rendering engine"
sections for the full design this implements.
"""

from __future__ import annotations

import uuid

from django.core.files.base import ContentFile
from django.db import transaction
from django_q.tasks import async_task

from generation.models import GenerationJob, Mode, ReferenceAsset, RenderDuration, RenderPreset
from generation.resolution import compute_resolution
from integrations import media_post, motion_context

from .models import CONTINUATION_CAPABLE_MODES, Clip, Project, ProjectResource

# The only modes Director ever creates clips in -- shared by clips()/
# apply_planned_scenes()'s quality-tier lookups below (RenderPreset rows
# for image/audio modes are a separate, differently-labeled catalog, see
# generation/models.py's Mode docstring -- not relevant here).
_VIDEO_MODES = (Mode.TEXT_TO_VIDEO, Mode.IMAGE_TO_VIDEO, Mode.REFERENCE_TO_VIDEO)

_ACTIVE_JOB_STATUSES = {GenerationJob.Status.QUEUED, GenerationJob.Status.PROCESSING}

# Modes plan_scenes() (and a human editing its output) may propose -- r2v is
# deliberately excluded here even though it's in CONTINUATION_CAPABLE_MODES:
# it needs actual reference assets to be meaningful, which the planning
# guide never asks the LLM to invent (see DIRECTOR_PLAN_GUIDE_en.md), so
# there's no legitimate way for a planned scene to land on it.
_PLANNABLE_MODES = {Mode.TEXT_TO_VIDEO, Mode.IMAGE_TO_VIDEO}


class RenderConflict(Exception):
    """Raised when a render is requested for a Clip whose dirty chain
    overlaps one already in flight -- see render_clip(). Translated to a
    409 by director/api.py, same shape as generation/api.py's own 409s."""


class PlanError(Exception):
    """Raised by apply_planned_scenes() when the (possibly user-edited)
    scene list can't be turned into real Clips -- e.g. no active
    RenderPreset/RenderDuration exists for a proposed mode. Translated to a
    400 by director/api.py."""


def _predecessor(clip: Clip) -> Clip | None:
    return Clip.objects.filter(project_id=clip.project_id, order__lt=clip.order).order_by("-order").first()


def available_quality_labels() -> list[str]:
    """Every distinct RenderPreset.label active for at least one of
    Director's video modes, in catalog order -- the valid values for
    Project.quality_label (see director/api.py's projects()/project_detail()
    validation). Deduplicated in Python rather than via .distinct() +
    .order_by(): Postgres rejects SELECT DISTINCT <col> ordered by columns
    outside that same column list, which sort_order/mode/megapixels would be.
    """
    seen: list[str] = []
    labels = (
        RenderPreset.objects.filter(mode__in=_VIDEO_MODES, is_active=True)
        .order_by("sort_order", "mode", "megapixels")
        .values_list("label", flat=True)
    )
    for label in labels:
        if label not in seen:
            seen.append(label)
    return seen


def resolve_preset_for_mode(quality_label: str, mode: str) -> RenderPreset | None:
    """The RenderPreset `mode` should use for Project.quality_label. Quality
    tiers share a label across modes but each mode has its own row with its
    own megapixels/steps (see RenderPreset's own docstring), so this is a
    per-mode lookup rather than a single FK on Project. Falls back to the
    first active preset for the mode at all if the label doesn't have a row
    for it (e.g. an admin retired that combination after a project already
    picked it) -- consistent with this app's general soft-reroute-rather-
    than-hard-fail posture around is_active gating elsewhere.
    """
    preset = (
        RenderPreset.objects.filter(mode=mode, label=quality_label, is_active=True).first()
        if quality_label
        else None
    )
    return preset or RenderPreset.objects.filter(mode=mode, is_active=True).first()


def _nearest_duration(preset: RenderPreset, target_seconds: float) -> RenderDuration | None:
    """The active RenderDuration under `preset` closest to target_seconds --
    used when a Clip's preset changes (Project.quality_label edited) so its
    duration choice carries over as closely as possible instead of silently
    resetting."""
    durations = list(preset.durations.filter(is_active=True))
    return min(durations, key=lambda d: abs(d.duration_seconds - target_seconds)) if durations else None


def recompute_project_resolutions(project: Project) -> None:
    """Re-derives every Clip's preset/duration/width/height from the
    project's current aspect_ratio/quality_label -- call after either
    changes (see director/api.py's project_detail() PATCH). Both are
    project-wide settings now (MiniMax H3's continuity model requires
    consistent resolution across a chain, and a shared quality tier keeps
    every clip's render comparable); a Clip's own preset/width/height are
    just cached derivations of them, not independently chosen.
    """
    predecessor: Clip | None = None
    for clip in project.clips.order_by("order").select_related("preset", "duration"):
        preset = resolve_preset_for_mode(project.quality_label, clip.mode)
        if preset is not None and preset.id != clip.preset_id:
            duration = _nearest_duration(preset, clip.duration.duration_seconds)
            if duration is not None:
                clip.preset = preset
                clip.duration = duration
        elif preset is not None:
            clip.preset = preset

        if clip.continues_previous and predecessor is not None:
            clip.width, clip.height = predecessor.width, predecessor.height
        else:
            clip.width, clip.height = compute_resolution(clip.preset.megapixels, project.aspect_ratio)

        clip.save(update_fields=["preset", "duration", "width", "height"])
        predecessor = clip


def resolve_clip_width_height(clip: Clip) -> tuple[int, int]:
    """Width/height for `clip` right now, given its current preset,
    continues_previous, and its project's aspect_ratio -- locked to the
    immediate predecessor's own width/height when continuing (guarantees an
    exact pixel match across a continuation run even if the predecessor's
    mode resolves to different megapixels under the same quality tier),
    otherwise computed fresh. Used when continues_previous is toggled after
    creation (director/api.py's clip_detail() PATCH) -- unlike
    recompute_project_resolutions(), this only ever touches one Clip.
    """
    if clip.continues_previous:
        predecessor = _predecessor(clip)
        if predecessor is not None:
            return predecessor.width, predecessor.height
    return compute_resolution(clip.preset.megapixels, clip.project.aspect_ratio)


def project_requires_reference_mode(project: Project) -> bool:
    """True once a Project has any shared resource -- see director/api.py's
    project_resources()/clips() POST handlers: while true, every Clip in
    the project must be mode=r2v, since only MiniMaxH3ReferenceToVideo's job
    actually wires ref_image_N/etc (see _combined_references() below) -- a
    t2v/i2v clip would have no way to honor a project resource's <Picture N>
    token even though the resource is meant to be usable from any clip in
    the project.
    """
    return project.resources.exists()


def _combined_references(clip: Clip) -> list[ProjectResource | ReferenceAsset]:
    """Every reference this Clip's render should feed into its new
    GenerationJob, in the exact order _build_job_for_clip() copies them in
    -- this Clip's project's shared resources first (so a "<Picture N>"
    token means the same thing in every Clip's prompt, matching what
    ProjectResourcesPanel shows the user -- see ClipReferenceAsset.label's
    matching offset), then this Clip's own references appended after, per
    kind. Mirrors generation/tasks.py's own
    job.references.filter(kind=...).order_by("order", "id") consumption
    order (see that module's _build_workflow_for_job()).
    """
    combined = []
    for kind in (ReferenceAsset.Kind.IMAGE, ReferenceAsset.Kind.AUDIO, ReferenceAsset.Kind.VIDEO):
        project_items = list(clip.project.resources.filter(kind=kind).order_by("order", "id"))
        combined.extend(project_items)
        for offset, item in enumerate(clip.references.filter(kind=kind).order_by("order", "id")):
            item.order = len(project_items) + offset
            combined.append(item)
    return combined


def _chain_run_prefix_clips(clip: Clip) -> list[Clip]:
    """Every Clip from the start of `clip`'s current continuation run up to
    and including `clip` itself, in order -- just `[clip]` if it isn't
    itself a continuation. Used both to size a fresh run (len == 1) and to
    build the "shots" list a resumed run's MiniMaxH3ChainPlan submission
    needs (see _resolve_chain_params()) -- every scene's prompt, not just
    the new one, since the plan is validated/hashed as a whole (confirmed
    live, see extras.md#contex-loop)."""
    chain = [clip]
    current = clip
    while current.continues_previous:
        predecessor = _predecessor(current)
        if predecessor is None:
            break
        chain.append(predecessor)
        current = predecessor
    chain.reverse()
    return chain


def _resolve_chain_params(clip: Clip) -> dict | None:
    """Returns integrations.motion_context.apply_motion_context()'s kwargs
    for `clip`, or None if it should use the last-frame fallback instead
    (extension unavailable, or -- for a continuation Clip -- its immediate
    predecessor has no real chain checkpoint of its own to resume from,
    see Clip.chain_run_name's docstring on why that's checked on the
    predecessor specifically, not walked further back).
    """
    if not motion_context.is_available():
        return None

    if clip.continues_previous:
        predecessor = _predecessor(clip)
        if predecessor is None or predecessor.needs_render or not predecessor.chain_run_name:
            return None
        run_name = predecessor.chain_run_name
        scene_number = predecessor.chain_scene_number + 1
    else:
        # Fresh run -- uuid4 (not e.g. the Clip id alone) so re-rendering
        # the same Clip repeatedly never resumes a stale prior attempt's
        # checkpoint under the same run_name.
        run_name = f"director_c{clip.id}_{uuid.uuid4().hex[:8]}"
        scene_number = 1

    chain_clips = _chain_run_prefix_clips(clip)
    assert len(chain_clips) == scene_number, "chain_run_prefix_clips length must match scene_number"
    shots = [{"id": f"clip{c.id}", "prompt": c.prompt.strip() or " "} for c in chain_clips]

    return {
        "shots": shots,
        "prompt_prefix": clip.project.overarching_prompt,
        "run_name": run_name,
        "scene_number": scene_number,
        "width": clip.width,
        "height": clip.height,
        "default_duration_seconds": clip.duration.duration_seconds,
        "default_steps": clip.preset.steps,
    }


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
    or a continuation Clip's immediate predecessor has no real checkpoint
    of its own (see _resolve_chain_params()) -- see extras.md#contex-loop's
    "Graceful fallback" section: instead of true motion/audio continuity,
    feeds the previous clip's last frame in as an ordinary image reference.
    Self-healing at scene-start boundaries: the next *fresh* (non-
    continuation) Clip rendered always starts a brand new real-continuity
    run if the extension is available then, regardless of how any earlier
    part of the project rendered.
    """
    chain_params = _resolve_chain_params(clip)
    anchor_frame: bytes | None = None

    if clip.continues_previous:
        predecessor = _predecessor(clip)
        if predecessor is None or predecessor.needs_render or predecessor.current_job_id is None:
            raise RenderConflict(f"Clip {clip.id}'s predecessor isn't rendered yet.")

        has_own_image_ref = any(r.kind == ReferenceAsset.Kind.IMAGE for r in clip.references.all())
        # An anchor frame from the predecessor's last frame is needed
        # whenever real continuity ISN'T active (that *is* the fallback,
        # see extras.md#contex-loop), and *also* whenever it IS active but
        # this is an i2v clip with no image reference of its own:
        # MiniMaxH3ImageToVideo's first_frame is a real input on the
        # underlying sampler-prep node regardless of Director's own
        # continuity mechanism (MiniMaxH3ChainContext only wraps its
        # output, see integrations/motion_context.py) -- leaving it
        # unset means the template's own placeholder-example LoadImage
        # wiring stays in place and fails ComfyUI's validation (confirmed
        # live: this exact failure, independent of chain_params).
        needs_anchor_frame = chain_params is None or (clip.mode == Mode.IMAGE_TO_VIDEO and not has_own_image_ref)
        if needs_anchor_frame:
            pred_job = predecessor.current_job
            pred_job.video_file.open("rb")
            try:
                video_bytes = pred_job.video_file.read()
            finally:
                pred_job.video_file.close()
            anchor_frame = media_post.extract_last_frame(video_bytes)

    job = GenerationJob.objects.create(
        user=clip.project.user,
        mode=clip.mode,
        preset=clip.preset,
        duration=clip.duration,
        raw_prompt=clip.prompt,
        improved_prompt=clip.improved_prompt,
        megapixels=clip.preset.megapixels,
        steps=clip.preset.steps,
        aspect_ratio=clip.project.aspect_ratio,
        width=clip.width,
        height=clip.height,
        duration_seconds=clip.duration.duration_seconds,
        estimated_seconds=clip.duration.estimated_render_seconds,
        continuation_params=chain_params,
    )

    references = _combined_references(clip)
    if anchor_frame is not None:
        # Leads as the new job's first (order=0) image reference -- i2v's
        # convention is order=0 -> first_frame; r2v's is order=0 -> the
        # first <Picture N> token. Either way it's the strongest reference
        # slot, matching the intent (start this clip from exactly where
        # the previous one ended). Any image references already on the
        # clip itself shift down to make room.
        for ref in references:
            if ref.kind == ReferenceAsset.Kind.IMAGE:
                ref.order += 1
        anchor_ref = ReferenceAsset(job=job, kind=ReferenceAsset.Kind.IMAGE, order=0)
        anchor_ref.file.save("continuation_last_frame.png", ContentFile(anchor_frame), save=True)

    for ref in references:
        ref.file.open("rb")
        try:
            content = ref.file.read()
        finally:
            ref.file.close()
        new_ref = ReferenceAsset(job=job, kind=ref.kind, order=ref.order)
        new_ref.file.save(ref.file.name.rsplit("/", 1)[-1], ContentFile(content), save=True)

    # chain_run_name/chain_scene_number are deliberately NOT set here --
    # only once this job actually finishes successfully (see signals.py's
    # on_job_finished()). Setting them optimistically at creation time was
    # a real bug caught by testing a failed render for real: nothing else
    # here guarantees MiniMaxH3ChainSegmentSave ever actually ran (a
    # rejected/failed job never produces the checkpoint it names), so a
    # later continuation Clip could otherwise "resume" from one that was
    # never saved.
    clip.current_job = job
    clip.save(update_fields=["current_job"])

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


def normalize_planned_scenes(raw_scenes) -> list[dict]:
    """Coerces integrations.llm.plan_scenes()'s raw reply -- or a scene list
    a user has since hand-edited in the preview step -- into the exact
    shape apply_planned_scenes() and the API's response both expect:
    [{"mode": str, "continues_previous": bool, "prompt": str, "notes": str}, ...].

    The LLM's JSON is untrusted input, not a contract: silently drops
    entries with no usable prompt and repairs everything else (unknown/
    missing mode falls back to t2v, continues_previous is coerced to False
    whenever the mode can't actually support it) rather than failing the
    whole plan over one malformed scene. Positional continuity (can't
    continue when this is the sequence's very first scene) is enforced by
    apply_planned_scenes() instead, since that depends on whether this is
    appended after existing clips.
    """
    scenes = []
    for raw in raw_scenes if isinstance(raw_scenes, list) else []:
        if not isinstance(raw, dict):
            continue
        prompt = str(raw.get("prompt", "")).strip()
        if not prompt:
            continue
        mode = raw.get("mode") if raw.get("mode") in _PLANNABLE_MODES else Mode.TEXT_TO_VIDEO
        scenes.append(
            {
                "mode": mode,
                "continues_previous": bool(raw.get("continues_previous")) and mode in CONTINUATION_CAPABLE_MODES,
                "prompt": prompt,
                "notes": str(raw.get("notes", "")).strip(),
            }
        )
    return scenes


def apply_planned_scenes(project: Project, scenes, *, replace: bool) -> list[Clip]:
    """Turns a (possibly user-edited) scene list into real Clip rows,
    appended after the project's existing clips by default, or replacing
    them entirely when replace=True. Preset/width/height are resolved from
    the project's own quality_label/aspect_ratio (see
    director/api.py's clips() POST handler, which resolves the same way for
    a manually-created clip). Doesn't itself trigger any render.
    """
    if project_requires_reference_mode(project):
        raise PlanError(
            "This project has shared references -- every clip must be a reference clip, which "
            '"Generate from script" doesn\'t support yet. Add clips manually instead.'
        )

    normalized = normalize_planned_scenes(scenes)
    if not normalized:
        raise PlanError("No usable scenes to apply.")

    with transaction.atomic():
        if replace:
            for clip in project.clips.all():
                for ref in clip.references.all():
                    ref.file.delete(save=False)
            project.clips.all().delete()
            predecessor: Clip | None = None
            next_order = 0
        else:
            predecessor = project.clips.order_by("-order").first()
            next_order = 0 if predecessor is None else predecessor.order + 1

        created: list[Clip] = []
        for scene in normalized:
            mode = scene["mode"]
            preset = resolve_preset_for_mode(project.quality_label, mode)
            duration = preset.durations.filter(is_active=True).first() if preset else None
            if preset is None or duration is None:
                raise PlanError(f"No active render preset/duration is configured for mode {mode!r}.")

            continues_previous = scene["continues_previous"] and predecessor is not None
            if continues_previous:
                width, height = predecessor.width, predecessor.height
            else:
                width, height = compute_resolution(preset.megapixels, project.aspect_ratio)

            clip = Clip.objects.create(
                project=project,
                order=next_order,
                continues_previous=continues_previous,
                mode=mode,
                prompt=scene["prompt"],
                preset=preset,
                duration=duration,
                width=width,
                height=height,
            )
            created.append(clip)
            predecessor = clip
            next_order += 1

    return created
