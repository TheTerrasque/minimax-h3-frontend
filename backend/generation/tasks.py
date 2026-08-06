"""Django-Q2 task entry point for working through the GenerationJob queue.

Registered by calling async_task("generation.tasks.process_queue") (no job
id -- it's a shared queue processor, not a per-job task) whenever a job is
created (see generation/api.py). process_queue() claims and runs jobs
strictly one at a time, FIFO (oldest queued first), looping until the queue
is empty -- this is what actually makes rendering serialized and ordered,
*not* Django-Q2 itself: its ORM broker's dequeue query has no ORDER BY, so
task pickup order isn't guaranteed, and multiple workers would happily run
several jobs in parallel. FIFO/serialization here comes entirely from
_claim_next_job()'s explicit `order_by("created_at", "id")` plus a DB row
lock, combined with Q_CLUSTER_WORKERS=1 (config/settings.py) so only one
process_queue loop -- and therefore only one _execute_job() call -- is ever
running at a time. (The row lock alone only stops the same job being claimed
twice; it does NOT stop two *different* jobs running in parallel if workers
were ever bumped above 1 -- don't, without redesigning this.)

No LLM call happens here -- prompt refinement is an explicit pre-job user
action (the "AI refine" button or the interactive chat, both in
generation/api.py); by the time a job reaches _execute_job(), its
improved_prompt already holds whatever the user ended up with (or is blank,
meaning they didn't use either).

The three API-format workflows this patches live in
resources/workflows_api/*.api.json, generated from resources/workflows/*.json
by scripts/export_workflow_api.py (a from-scratch reimplementation of
ComfyUI's own "Export API" -- see that script's docstring for exactly how
each serialization rule was verified against real saved workflow JSON + live
/object_info responses; nothing about the node ids below is guessed). If a
workflow in resources/workflows/ is ever edited in the ComfyUI UI, re-run
that script to regenerate its .api.json counterpart before this will still
line up.

build_api_workflow() below is deliberately a pure function (given already-
uploaded ComfyUI filenames, no DB/network I/O of its own) so it's reusable
by both this module's job-backed _execute_job() and
generation/management/commands/benchmark_render_times.py, which needs the
same patching without any GenerationJob/RenderPreset DB rows to back it.
"""

from __future__ import annotations

import json
import random
import uuid
from typing import Any

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from integrations import comfyui

from .models import GenerationJob, Mode, ReferenceAsset

SAVE_VIDEO_NODE_ID = "92"

_API_WORKFLOW_FILENAMES = {
    Mode.TEXT_TO_VIDEO: "video_minimax_h3_t2v.api.json",
    Mode.IMAGE_TO_VIDEO: "video_minimax_h3_i2v.api.json",
    Mode.REFERENCE_TO_VIDEO: "video_minimax_h3_r2v.api.json",
}

# Node ids inside each mode's .api.json -- see
# backend/scripts/export_workflow_api.py's cross-checked output and
# resources/COMFYUI_API_GUIDE.md #4 for what each node is.
_T2V_I2V_NODES = {
    "sampler": "104",  # MiniMaxH3ImageToVideo: prompt/width/height/length/first_frame/last_frame
    "duration_seconds": "111",  # PrimitiveFloat feeding the seconds->frame-length math node
    "steps": "9",  # BasicScheduler
    "seed": "15",  # RandomNoise
}
_I2V_FIRST_FRAME_LOADIMAGE = "114"  # only present in the i2v template

_R2V_NODES = {
    "sampler": "136",  # MiniMaxH3ReferenceToVideo
    "prompt": "138",  # PrimitiveStringMultiline, linked into sampler.prompt
    "duration_seconds": "132",
    "steps": "124",
    "seed": "129",
}
_R2V_MAX_REF_IMAGES = 9  # per live /object_info: ref_images autogrow max
_R2V_MAX_REF_AUDIO = 3  # per live /object_info: ref_audios autogrow max (prefix "ref_audio_")

# The node ComfyUI actually reports step-by-step `progress` events for --
# NOT _T2V_I2V_NODES/_R2V_NODES's "sampler" entry above. That "sampler" key
# is the MiniMaxH3*ToVideo node itself, which only encodes the prompt/image
# into conditioning + an initial latent (see its outputs feeding
# BasicGuider/SamplerCustomAdvanced below) -- it executes and returns
# quickly, *before* the real K-sampler loop, so treating it as "the
# sampler" for progress purposes made the UI show "rendering" during what
# was still model loading/conditioning ("preparing"), then "finishing"
# during the real sampling steps once the actual SamplerCustomAdvanced node
# started (misattributed as "after the sampler"), and no progress bar ever
# appeared (its `progress` messages carry this node id, which never matched
# the wrong one being watched for). Confirmed against each mode's .api.json
# (grep '"class_type": "SamplerCustomAdvanced"'). Used only by
# _execute_job's live progress streaming (see integrations/comfyui.py's
# stream_execution_progress) -- has no bearing on build_api_workflow's
# patching, which still keys off "sampler" above.
_PROGRESS_SAMPLER_NODES = {
    Mode.TEXT_TO_VIDEO: "14",
    Mode.IMAGE_TO_VIDEO: "14",
    Mode.REFERENCE_TO_VIDEO: "125",
}


def _load_api_workflow(mode: str) -> dict[str, Any]:
    path = settings.RESOURCES_DIR / "workflows_api" / _API_WORKFLOW_FILENAMES[mode]
    return json.loads(path.read_text(encoding="utf-8"))


def _next_node_id(workflow: dict[str, Any]) -> str:
    return str(max(int(nid) for nid in workflow) + 1)


def _add_load_image_node(workflow: dict[str, Any], uploaded_name: str) -> str:
    node_id = _next_node_id(workflow)
    workflow[node_id] = {
        "class_type": "LoadImage",
        "inputs": {"image": uploaded_name},
        "_meta": {"title": "LoadImage"},
    }
    return node_id


def _add_load_audio_node(workflow: dict[str, Any], uploaded_name: str) -> str:
    # LoadAudio's only input is "audio" (COMBO, filename-based) -- confirmed
    # against live /object_info/LoadAudio, same shape as LoadImage.inputs.image.
    node_id = _next_node_id(workflow)
    workflow[node_id] = {
        "class_type": "LoadAudio",
        "inputs": {"audio": uploaded_name},
        "_meta": {"title": "LoadAudio"},
    }
    return node_id


def build_api_workflow(
    mode: str,
    *,
    width: int,
    height: int,
    duration_seconds: float,
    steps: int,
    prompt_text: str,
    first_frame_upload: str | None = None,
    last_frame_upload: str | None = None,
    ref_image_uploads: list[str] | None = None,
    ref_audio_uploads: list[str] | None = None,
) -> dict[str, Any]:
    """Loads the mode's API-format template and patches in the given values.

    Takes already-uploaded ComfyUI input filenames (via
    integrations.comfyui.upload_media) for any reference images/audio --
    doesn't upload anything itself, so it has no DB/network dependency beyond
    reading the template file.
    """
    workflow = _load_api_workflow(mode)
    nodes = _R2V_NODES if mode == Mode.REFERENCE_TO_VIDEO else _T2V_I2V_NODES

    sampler = workflow[nodes["sampler"]]["inputs"]
    # Bypass ResolutionSelector entirely -- it only accepts an aspect-ratio
    # preset + megapixels, not arbitrary width/height, and callers here deal
    # in literal width/height. Overwriting the link with a plain int is
    # valid API-format JSON; ResolutionSelector is left in place but unused.
    sampler["width"] = width
    sampler["height"] = height
    workflow[nodes["duration_seconds"]]["inputs"]["value"] = duration_seconds
    workflow[nodes["steps"]]["inputs"]["steps"] = steps
    workflow[nodes["seed"]]["inputs"]["noise_seed"] = random.randint(0, 2**53 - 1)

    if mode == Mode.REFERENCE_TO_VIDEO:
        # r2v's prompt is a separate PrimitiveStringMultiline node linked
        # into the sampler, not a literal on the sampler itself.
        workflow[_R2V_NODES["prompt"]]["inputs"]["value"] = prompt_text

        # Drop the template's example ref_image wiring/nodes and rebuild
        # from the given uploads.
        for key in [k for k in sampler if k.startswith("ref_images.ref_image_")]:
            del sampler[key]
        for node_id in [nid for nid, n in workflow.items() if n["class_type"] == "LoadImage"]:
            del workflow[node_id]
        for i, uploaded in enumerate((ref_image_uploads or [])[:_R2V_MAX_REF_IMAGES]):
            node_id = _add_load_image_node(workflow, uploaded)
            sampler[f"ref_images.ref_image_{i}"] = [node_id, 0]

        # Same pattern for standalone reference audio -- the template has no
        # example ref_audio wiring to clean up (unlike ref_image above), but
        # the cleanup loop is harmless/defensive if that ever changes.
        for key in [k for k in sampler if k.startswith("ref_audios.ref_audio_")]:
            del sampler[key]
        for node_id in [nid for nid, n in workflow.items() if n["class_type"] == "LoadAudio"]:
            del workflow[node_id]
        for i, uploaded in enumerate((ref_audio_uploads or [])[:_R2V_MAX_REF_AUDIO]):
            node_id = _add_load_audio_node(workflow, uploaded)
            sampler[f"ref_audios.ref_audio_{i}"] = [node_id, 0]

        # TODO: ref_videos.ref_video_N / ref_video_audios.ref_video_audio_N --
        # needs LoadVideo + frame-extraction wiring (see
        # resources/COMFYUI_API_GUIDE.md #4's open question on ref_video_N's
        # exact expected upstream shape); not yet implemented. ref_audio_N
        # above is done.
    else:
        sampler["prompt"] = prompt_text
        if first_frame_upload:
            workflow[_I2V_FIRST_FRAME_LOADIMAGE]["inputs"]["image"] = first_frame_upload
            sampler["first_frame"] = [_I2V_FIRST_FRAME_LOADIMAGE, 0]
        if last_frame_upload:
            node_id = _add_load_image_node(workflow, last_frame_upload)
            sampler["last_frame"] = [node_id, 0]

    return workflow


def _upload_reference(ref: ReferenceAsset) -> str:
    ref.file.open("rb")
    try:
        return comfyui.upload_media(ref.file.read(), ref.file.name.rsplit("/", 1)[-1])
    finally:
        ref.file.close()


def _build_workflow_for_job(job: GenerationJob) -> dict[str, Any]:
    preset = job.preset
    prompt_text = job.improved_prompt or job.raw_prompt

    first_frame_upload = last_frame_upload = None
    ref_image_uploads = ref_audio_uploads = None

    if job.mode == Mode.IMAGE_TO_VIDEO:
        # Convention: the first (order=0) image reference is the first
        # frame, the second (order=1, if present) is the last frame --
        # ReferenceAsset has no separate "role" field yet, see
        # ARCHITECTURE.md.
        images = list(job.references.filter(kind=ReferenceAsset.Kind.IMAGE).order_by("order", "id"))
        if images:
            first_frame_upload = _upload_reference(images[0])
        if len(images) > 1:
            last_frame_upload = _upload_reference(images[1])
    elif job.mode == Mode.REFERENCE_TO_VIDEO:
        images = list(
            job.references.filter(kind=ReferenceAsset.Kind.IMAGE).order_by("order", "id")[
                :_R2V_MAX_REF_IMAGES
            ]
        )
        ref_image_uploads = [_upload_reference(ref) for ref in images]
        audio = list(
            job.references.filter(kind=ReferenceAsset.Kind.AUDIO).order_by("order", "id")[
                :_R2V_MAX_REF_AUDIO
            ]
        )
        ref_audio_uploads = [_upload_reference(ref) for ref in audio]

    return build_api_workflow(
        job.mode,
        width=job.width,
        height=job.height,
        duration_seconds=job.duration_seconds,
        steps=preset.steps,
        prompt_text=prompt_text,
        first_frame_upload=first_frame_upload,
        last_frame_upload=last_frame_upload,
        ref_image_uploads=ref_image_uploads,
        ref_audio_uploads=ref_audio_uploads,
    )


def _claim_next_job() -> GenerationJob | None:
    """Atomically claims the oldest still-QUEUED job, system-wide, and marks
    it PROCESSING -- the one place FIFO order and one-at-a-time-ness are
    actually enforced (see module docstring). select_for_update(skip_locked)
    means a concurrent claim attempt (only possible if Q_CLUSTER_WORKERS is
    ever misconfigured above 1) skips this row rather than blocking on it --
    it would then go claim a *different* row instead, which is exactly the
    scenario that setting is what actually prevents, not this lock.
    """
    with transaction.atomic():
        job = (
            GenerationJob.objects.select_for_update(skip_locked=True)
            .select_related("preset")
            .filter(status=GenerationJob.Status.QUEUED)
            .order_by("created_at", "id")
            .first()
        )
        if job is None:
            return None
        job.status = GenerationJob.Status.PROCESSING
        job.started_at = timezone.now()
        job.phase = GenerationJob.Phase.PREPARING
        job.save(update_fields=["status", "started_at", "phase"])
    return job


def _progress_callback(job_id: int) -> Any:
    """Returns a callback for comfyui.stream_execution_progress() that
    writes straight to the DB via an UPDATE (not job.save()) -- there's no
    in-memory GenerationJob instance worth keeping in sync here, this is
    purely so QueueSidebar/JobModal's polling picks up live phase/progress.
    """

    def on_update(phase: str, current: int | None, total: int | None) -> None:
        GenerationJob.objects.filter(pk=job_id).update(
            phase=phase, progress_current=current, progress_total=total
        )

    return on_update


def _finish_job_from_history(job: GenerationJob, history_record: dict[str, Any]) -> None:
    """Finalizes an already-DONE-on-ComfyUI's-side prompt: checks for a
    server-side execution error, downloads the video if there wasn't one,
    saves it, marks the job DONE, and cleans up ComfyUI's own copy. Shared
    by the normal execute path and orphaned-job recovery -- both end up
    holding a populated /history record at this point, the rest is
    identical either way.
    """
    comfyui.check_for_error(history_record)
    output = comfyui.extract_video_output(history_record, SAVE_VIDEO_NODE_ID)
    video_bytes = comfyui.download_output(output)

    job.video_file.save(output.filename, ContentFile(video_bytes), save=False)
    job.status = GenerationJob.Status.DONE
    job.finished_at = timezone.now()
    job.phase = ""
    job.progress_current = None
    job.progress_total = None
    job.save(
        update_fields=[
            "video_file",
            "status",
            "finished_at",
            "phase",
            "progress_current",
            "progress_total",
        ]
    )

    # Don't leave a copy on the ComfyUI machine now that we have it, and
    # tidy the history entry -- see resources/COMFYUI_API_GUIDE.md #10.
    comfyui.delete_output_file(output)
    comfyui.clear_history(job.comfyui_prompt_id)


def _mark_job_failed(job: GenerationJob, error_message: str) -> None:
    job.status = GenerationJob.Status.DONE
    job.error_message = error_message
    job.finished_at = timezone.now()
    job.phase = ""
    job.progress_current = None
    job.progress_total = None
    job.save(
        update_fields=[
            "status",
            "error_message",
            "finished_at",
            "phase",
            "progress_current",
            "progress_total",
        ]
    )


def _execute_job(job: GenerationJob) -> None:
    """Runs one already-PROCESSING job's ComfyUI round trip to completion.

    Always ends in DONE, success or failure -- failure is distinguished by
    error_message being set and video_file being blank, not by a separate
    status value (see Status's docstring in models.py). Swallows its own
    exceptions (rather than re-raising, as the old per-job task did) so
    process_queue()'s loop keeps working through the rest of the queue
    instead of aborting on the first failure.
    """
    try:
        workflow = _build_workflow_for_job(job)

        client_id = str(uuid.uuid4())
        prompt_id = comfyui.queue_prompt(workflow, client_id)
        job.comfyui_prompt_id = prompt_id
        job.save(update_fields=["comfyui_prompt_id"])

        timeout = job.estimated_seconds * 3 + 300
        # Best-effort live phase/progress (see comfyui.stream_execution_progress's
        # own docstring) -- swallows its own errors and simply returns early if
        # anything goes wrong, so a WebSocket hiccup never fails the job itself;
        # the actual result always still comes from wait_for_result()+
        # check_for_error() below, exactly as before this was added.
        comfyui.stream_execution_progress(
            prompt_id, client_id, _PROGRESS_SAMPLER_NODES[job.mode], _progress_callback(job.id), timeout=timeout
        )

        history_record = comfyui.wait_for_result(prompt_id, timeout=timeout)
        _finish_job_from_history(job, history_record)

    except Exception as exc:  # noqa: BLE001 -- surfaced to the user via job.error_message
        _mark_job_failed(job, str(exc))


def recover_orphaned_processing_jobs() -> None:
    """Recovers any GenerationJob left PROCESSING with nothing actually
    working on it anymore -- the signature of a qcluster/backend restart
    (container recreate, crash, `docker compose up` after a rebuild, etc.)
    landing mid-render: _claim_next_job() only ever claims QUEUED jobs, so
    without this, a PROCESSING job orphaned this way would sit stuck
    forever, showing "Processing…" to its owner indefinitely (see
    ARCHITECTURE.md's Verification for the real report this came from).

    Q_CLUSTER_WORKERS=1 (config/settings.py) means at most one job is ever
    genuinely in flight, so any job still marked PROCESSING when this runs
    is *necessarily* orphaned -- **but only at this function's two actual
    call sites**: the top of process_queue() (before that same call's own
    claim loop starts) and the recover_stale_jobs management command run
    once at qcluster container startup (before its own task loop starts
    consuming anything). Both are naturally serialized against a real
    _execute_job() by the same Q_CLUSTER_WORKERS=1 mechanism that makes the
    rest of this module's FIFO guarantee hold.

    DO NOT call this (or _recover_one_orphaned_job) ad hoc against a live
    stack's real database -- e.g. from `manage.py shell` while qcluster is
    running -- outside those two call sites. Nothing stops it from racing
    a genuinely in-flight _execute_job() for some *other* job than the one
    you're looking at: it queries every PROCESSING row with no locking, so
    it will happily "recover" (and, if the ComfyUI client is mocked for a
    test, incorrectly mark failed) a job a live worker is still actively
    rendering. Hit this for real: an ad hoc test run with `comfyui` mocked
    swept up a real, genuinely-still-rendering job alongside the intended
    synthetic test rows and marked it lost mid-render -- see
    ARCHITECTURE.md's Verification for the full incident and recovery.

    Tries to actually recover the result rather than just discarding
    progress: checks ComfyUI's /history first (it may have finished while
    nothing was watching), then /queue (it may genuinely still be
    rendering, in which case this picks the wait back up rather than
    abandoning it), and only gives up -- marking the job DONE with an
    explanatory error, freeing it from blocking anything -- once ComfyUI
    has no record of it at all.
    """
    for job in GenerationJob.objects.filter(status=GenerationJob.Status.PROCESSING):
        _recover_one_orphaned_job(job)


def _recover_one_orphaned_job(job: GenerationJob) -> None:
    if not job.comfyui_prompt_id:
        # Never even got as far as submitting to ComfyUI before the restart.
        _mark_job_failed(job, "Interrupted before reaching ComfyUI (server restarted mid-job).")
        return

    try:
        if not comfyui.is_alive():
            _mark_job_failed(job, "ComfyUI was unreachable while recovering this job after a restart.")
            return

        history_record = comfyui.get_history(job.comfyui_prompt_id)
        if history_record is not None:
            # It finished (successfully or not) while nothing was watching.
            _finish_job_from_history(job, history_record)
            return

        if comfyui.is_prompt_queued(job.comfyui_prompt_id):
            # Still genuinely rendering -- pick the wait back up rather
            # than abandoning real in-progress work.
            history_record = comfyui.wait_for_result(
                job.comfyui_prompt_id, timeout=job.estimated_seconds * 3 + 300
            )
            _finish_job_from_history(job, history_record)
            return

        # Not in history, not in ComfyUI's queue -- lost for good (e.g.
        # ComfyUI itself also restarted and forgot about it).
        _mark_job_failed(
            job,
            "Lost track of this job after a server restart -- ComfyUI has no record of it "
            "in its history or queue anymore.",
        )

    except Exception as exc:  # noqa: BLE001 -- surfaced to the user via job.error_message
        _mark_job_failed(job, f"Recovery after a server restart failed: {exc}")


def process_queue() -> None:
    """Django-Q2 entry point (see module docstring). Recovers any job
    orphaned by a previous restart (see recover_orphaned_processing_jobs),
    then works through every currently-QUEUED job in FIFO order, one at a
    time, until none remain. Enqueued redundantly -- once per job creation
    -- which is fine: a call that finds nothing QUEUED (because an
    already-running loop already claimed everything) just returns
    immediately, and recovery itself is a no-op once nothing's orphaned.
    """
    recover_orphaned_processing_jobs()
    while True:
        job = _claim_next_job()
        if job is None:
            return
        _execute_job(job)
