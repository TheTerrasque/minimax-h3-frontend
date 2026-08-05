"""Django-Q2 task entry point for running a GenerationJob end to end.

Registered by calling async_task("generation.tasks.run_generation_job", job.id)
when a GenerationJob is created (see generation/api.py, not yet written --
this file focuses on the execution flow itself).

The three API-format workflows this patches live in
resources/workflows_api/*.api.json, generated from resources/workflows/*.json
by scripts/export_workflow_api.py (a from-scratch reimplementation of
ComfyUI's own "Export API" -- see that script's docstring for exactly how
each serialization rule was verified against real saved workflow JSON + live
/object_info responses; nothing about the node ids below is guessed). If a
workflow in resources/workflows/ is ever edited in the ComfyUI UI, re-run
that script to regenerate its .api.json counterpart before this will still
line up.
"""

from __future__ import annotations

import json
import random
import uuid
from typing import Any

from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

from integrations import comfyui, llm

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


def _load_api_workflow(mode: str) -> dict[str, Any]:
    path = settings.RESOURCES_DIR / "workflows_api" / _API_WORKFLOW_FILENAMES[mode]
    return json.loads(path.read_text(encoding="utf-8"))


def _next_node_id(workflow: dict[str, Any]) -> str:
    return str(max(int(nid) for nid in workflow) + 1)


def _upload_reference(ref: ReferenceAsset) -> str:
    ref.file.open("rb")
    try:
        return comfyui.upload_media(ref.file.read(), ref.file.name.rsplit("/", 1)[-1])
    finally:
        ref.file.close()


def _add_load_image_node(workflow: dict[str, Any], uploaded_name: str) -> str:
    node_id = _next_node_id(workflow)
    workflow[node_id] = {
        "class_type": "LoadImage",
        "inputs": {"image": uploaded_name},
        "_meta": {"title": "LoadImage"},
    }
    return node_id


def _patch_common(
    workflow: dict[str, Any], job: GenerationJob, nodes: dict[str, str]
) -> tuple[str, dict[str, Any]]:
    preset = job.preset
    prompt_text = job.improved_prompt or job.raw_prompt

    sampler = workflow[nodes["sampler"]]["inputs"]
    # Bypass ResolutionSelector entirely -- it only accepts an aspect-ratio
    # preset + megapixels, not arbitrary width/height, and RenderPreset
    # stores literal width/height. Overwriting the link with a plain int is
    # valid API-format JSON; ResolutionSelector is left in place but unused.
    sampler["width"] = preset.width
    sampler["height"] = preset.height

    workflow[nodes["duration_seconds"]]["inputs"]["value"] = preset.duration_seconds
    workflow[nodes["steps"]]["inputs"]["steps"] = preset.steps
    workflow[nodes["seed"]]["inputs"]["noise_seed"] = random.randint(0, 2**53 - 1)

    return prompt_text, sampler


def _patch_t2v(workflow: dict[str, Any], job: GenerationJob) -> dict[str, Any]:
    prompt_text, sampler = _patch_common(workflow, job, _T2V_I2V_NODES)
    sampler["prompt"] = prompt_text
    return workflow


def _patch_i2v(workflow: dict[str, Any], job: GenerationJob) -> dict[str, Any]:
    prompt_text, sampler = _patch_common(workflow, job, _T2V_I2V_NODES)
    sampler["prompt"] = prompt_text

    # Convention: the first (order=0) image reference is the first frame,
    # the second (order=1, if present) is the last frame -- ReferenceAsset
    # has no separate "role" field yet, see ARCHITECTURE.md.
    images = list(job.references.filter(kind=ReferenceAsset.Kind.IMAGE).order_by("order", "id"))
    if images:
        uploaded = _upload_reference(images[0])
        workflow[_I2V_FIRST_FRAME_LOADIMAGE]["inputs"]["image"] = uploaded
        sampler["first_frame"] = [_I2V_FIRST_FRAME_LOADIMAGE, 0]
    if len(images) > 1:
        uploaded = _upload_reference(images[1])
        last_frame_node_id = _add_load_image_node(workflow, uploaded)
        sampler["last_frame"] = [last_frame_node_id, 0]

    return workflow


def _patch_r2v(workflow: dict[str, Any], job: GenerationJob) -> dict[str, Any]:
    prompt_text, sampler = _patch_common(workflow, job, _R2V_NODES)
    # r2v's prompt is a separate PrimitiveStringMultiline node linked into
    # the sampler, not a literal on the sampler itself like t2v/i2v.
    workflow[_R2V_NODES["prompt"]]["inputs"]["value"] = prompt_text

    # Drop the template's example ref_image wiring/nodes and rebuild from
    # this job's actual reference assets.
    for key in [k for k in sampler if k.startswith("ref_images.ref_image_")]:
        del sampler[key]
    template_ref_node_ids = [
        node_id for node_id, node in workflow.items() if node["class_type"] == "LoadImage"
    ]
    for node_id in template_ref_node_ids:
        del workflow[node_id]

    images = list(
        job.references.filter(kind=ReferenceAsset.Kind.IMAGE).order_by("order", "id")[
            :_R2V_MAX_REF_IMAGES
        ]
    )
    for i, ref in enumerate(images):
        uploaded = _upload_reference(ref)
        node_id = _add_load_image_node(workflow, uploaded)
        sampler[f"ref_images.ref_image_{i}"] = [node_id, 0]

    # TODO: ref_videos.ref_video_N / ref_video_audios.ref_video_audio_N /
    # ref_audios.ref_audio_N -- needs LoadVideo/LoadAudio wiring following
    # the identical pattern above; not yet implemented (see
    # resources/COMFYUI_API_GUIDE.md #4's open question on ref_video_N's
    # exact expected upstream shape).

    return workflow


_PATCHERS = {
    Mode.TEXT_TO_VIDEO: _patch_t2v,
    Mode.IMAGE_TO_VIDEO: _patch_i2v,
    Mode.REFERENCE_TO_VIDEO: _patch_r2v,
}


def _patch_workflow(workflow: dict[str, Any], job: GenerationJob) -> dict[str, Any]:
    return _PATCHERS[job.mode](workflow, job)


def run_generation_job(job_id: int) -> None:
    job = GenerationJob.objects.select_related("preset").get(id=job_id)
    job.status = GenerationJob.Status.RUNNING
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at"])

    try:
        if not job.improved_prompt:
            reference_labels = [ref.label for ref in job.references.all()]
            job.improved_prompt = llm.improve_prompt(job.mode, job.raw_prompt, reference_labels)
            job.save(update_fields=["improved_prompt"])

        workflow = _load_api_workflow(job.mode)
        workflow = _patch_workflow(workflow, job)

        client_id = str(uuid.uuid4())
        prompt_id = comfyui.queue_prompt(workflow, client_id)
        job.comfyui_prompt_id = prompt_id
        job.save(update_fields=["comfyui_prompt_id"])

        history_record = comfyui.wait_for_result(prompt_id, timeout=job.estimated_seconds * 3 + 300)
        output = comfyui.extract_video_output(history_record, SAVE_VIDEO_NODE_ID)
        video_bytes = comfyui.download_output(output)

        job.video_file.save(output.filename, ContentFile(video_bytes), save=False)
        job.status = GenerationJob.Status.COMPLETED
        job.finished_at = timezone.now()
        job.save(update_fields=["video_file", "status", "finished_at"])

        # Don't leave a copy on the ComfyUI machine now that we have it, and
        # tidy the history entry -- see resources/COMFYUI_API_GUIDE.md #10.
        comfyui.delete_output_file(output)
        comfyui.clear_history(prompt_id)

    except Exception as exc:  # noqa: BLE001 -- surfaced to the user via job.error_message
        job.status = GenerationJob.Status.FAILED
        job.error_message = str(exc)
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error_message", "finished_at"])
        raise
