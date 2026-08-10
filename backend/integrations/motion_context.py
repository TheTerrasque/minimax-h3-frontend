"""Splices motion/audio continuity (ethanfel/ComfyUI-MiniMaxH3-Contex-Loop,
see extras.md) between two separately-submitted ComfyUI jobs -- the low-
level, non-interactive half of that extension only (MiniMaxH3MotionContext /
MiniMaxH3LoopTrim / MiniMaxH3MotionContextSaveLatent/LoadLatent), not its
own Plan/Loop/Review-Gate pipeline, which runs a whole multi-scene chain as
one ComfyUI graph submission and needs a live interactive browser session --
see the Director Mode plan's "Extension research" section for why that
pipeline isn't used. This module drives the same job-at-a-time model as the
rest of this codebase's queue, called from generation/tasks.py's
build_api_workflow() exactly the way integrations/spectrum.py already
splices Spectrum in -- see that module for the same "find node(s), rewire
references, insert" pattern this follows.

Unlike spectrum.py, this needs no explicit per-mode node-id table: every
node this touches (the mode's sampler-prep node, BasicGuider,
SamplerCustomAdvanced, VAEDecode, VAEDecodeAudio) is unique-by-class_type in
every shipped template (t2v/i2v/r2v), confirmed by inspecting
resources/workflows_api/*.api.json directly -- so this discovers them the
same generic way spectrum.py discovers its sole UNETLoader, rather than
needing a hand-exported reference workflow to hardcode ids from.

Node input/output schemas below (MiniMaxH3MotionContext/LoopTrim/
Save|LoadLatent's exact INPUT_TYPES/RETURN_TYPES, and MiniMaxH3MotionContext
SaveLatent/LoadLatent's actual path-resolution logic -- folder_paths.
get_save_image_path() plus a "%s_%05d.safetensors" % (filename, clip_index)
suffix when clip_index > 0, and LoadLatent resolving a directory + clip_index
by suffix match rather than needing the exact filename back) were fetched
directly from that extension's nodes.py source, not guessed. **Not yet
verified against a real render** -- the extension isn't installed on this
deployment's ComfyUI yet (confirmed via a live /object_info/
MiniMaxH3MotionContext returning {}), so wiring correctness is only as good
as that source reading until someone actually runs a chained render; if a
future extension release changes these nodes' shapes, ComfyUI's own /prompt
validation will reject the job with a clear error (same graceful-failure
note as spectrum.py's).
"""

from __future__ import annotations

from typing import Any

from . import video_ref

_SAMPLER_PREP_CLASSES = ("MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo")

# Tested-default context lengths per H3_CHAIN_FORMAT_GUIDE.md ("use 22 for
# the tested balance of continuity and delivered footage"; audio likewise).
DEFAULT_CONTEXT_LENGTH = 22
DEFAULT_AUDIO_CONTEXT_LENGTH = 22


def _next_node_id(workflow: dict[str, Any]) -> str:
    return str(max(int(nid) for nid in workflow) + 1)


def _find_one(workflow: dict[str, Any], class_types: tuple[str, ...]) -> str:
    matches = [nid for nid, node in workflow.items() if node.get("class_type") in class_types]
    if len(matches) != 1:
        raise RuntimeError(f"apply_motion_context: expected exactly one of {class_types}, found {len(matches)}")
    return matches[0]


def _rewire(workflow: dict[str, Any], old_ref: list, new_ref: list) -> None:
    """Redirects every input across `workflow` currently pointing at
    old_ref (a [node_id, output_index] pair) to new_ref instead -- same
    generic reference-rewrite spectrum.py's apply_spectrum() does for its
    single UNETLoader rewire, generalized to an arbitrary [id, index] pair
    since motion-context touches more than one output socket."""
    for node in workflow.values():
        for key, value in node.get("inputs", {}).items():
            if isinstance(value, list) and len(value) == 2 and value == old_ref:
                node["inputs"][key] = list(new_ref)


def apply_motion_context(
    workflow: dict[str, Any],
    *,
    mode: str,
    source_filename: str,
    source_subfolder: str,
    save_prefix: str,
    save_index: int,
    load_prefix: str | None = None,
    load_index: int | None = None,
    context_length: int = DEFAULT_CONTEXT_LENGTH,
    audio_context_length: int = DEFAULT_AUDIO_CONTEXT_LENGTH,
) -> dict[str, Any]:
    """Mutates and returns `workflow` (an already-loaded/patched mode
    template, see generation/tasks.py::build_api_workflow) with continuity
    from a previous clip spliced in.

    `source_filename`/`source_subfolder` address the *previous clip's own
    rendered video* still sitting on ComfyUI's machine (see GenerationJob.
    keep_comfyui_output/comfyui_output_filename) -- fed through LoadVideo
    (video_ref.add_load_video_node(), using ComfyUI's own
    folder_paths "name [output]" annotation convention to point at output/
    instead of re-uploading into input/) into MiniMaxH3MotionContext's
    context_frames/context_audio.

    `save_prefix`/`save_index` are always given (this clip's own checkpoint,
    for whatever continuation clip comes *after* it, if any -- see
    director/models.py's Clip.checkpoint_filename_prefix/_clip_index).
    `load_prefix`/`load_index` are the *previous* clip's own saved
    checkpoint (None only if this is somehow the first clip of a chain with
    no saved latent to load, in which case MiniMaxH3MotionContext runs
    without context_latent -- see its schema, that input is optional).
    """
    sampler_id = _find_one(workflow, _SAMPLER_PREP_CLASSES)
    # Not otherwise referenced -- _rewire() below finds BasicGuider's
    # conditioning input generically, this call is purely the same
    # exactly-one structural assertion _find_one() makes for every other
    # node type here (fails loudly if a future template changes shape).
    _find_one(workflow, ("BasicGuider",))
    sampler_advanced_id = _find_one(workflow, ("SamplerCustomAdvanced",))
    vae_decode_id = _find_one(workflow, ("VAEDecode",))
    vae_decode_audio_id = _find_one(workflow, ("VAEDecodeAudio",))

    # Reuse whichever VAE loaders the template already wired up, rather than
    # adding duplicates -- the sampler-prep node's own "vae" input is the
    # video VAE; VAEDecodeAudio's "vae" input is the audio VAE.
    video_vae_ref = workflow[sampler_id]["inputs"]["vae"]
    audio_vae_ref = workflow[vae_decode_audio_id]["inputs"]["vae"]

    source_path = f"{source_subfolder}/{source_filename}" if source_subfolder else source_filename
    components_id = video_ref.add_load_video_node(workflow, f"{source_path} [output]")

    load_latent_ref: list | None = None
    if load_prefix is not None:
        load_latent_id = _next_node_id(workflow)
        workflow[load_latent_id] = {
            "class_type": "MiniMaxH3MotionContextLoadLatent",
            "inputs": {"latent_path": load_prefix, "clip_index": load_index},
            "_meta": {"title": "MiniMaxH3MotionContextLoadLatent"},
        }
        load_latent_ref = [load_latent_id, 0]

    # Reserve both new node ids and do every rewrite *before* inserting
    # either node's own dict -- otherwise a rewrite that redirects
    # references to, say, [sampler_id, 0] would also catch the new context
    # node's own "conditioning": [sampler_id, 0] input (added below) and
    # rewrite it to point at itself. Same reasoning as the trim node further
    # down. Mirrors spectrum.py's apply_spectrum(), which reserves its one
    # new node's id before rewiring for the same reason.
    context_id = _next_node_id(workflow)
    _rewire(workflow, [sampler_id, 0], [context_id, 0])

    context_inputs: dict[str, Any] = {
        "conditioning": [sampler_id, 0],
        "latent": [sampler_id, 1],
        "vae": video_vae_ref,
        "context_frames": [components_id, 0],
        "context_length": context_length,
        "encode_mode": "video",
        "anchor_mode": "head",
        "crop": "disabled",
        "audio_context_length": audio_context_length,
        "audio_mode": "timeline",
        "audio_vae": audio_vae_ref,
        "context_audio": [components_id, 1],
    }
    if load_latent_ref is not None:
        context_inputs["context_latent"] = load_latent_ref
    workflow[context_id] = {
        "class_type": "MiniMaxH3MotionContext",
        "inputs": context_inputs,
        "_meta": {"title": "MiniMaxH3MotionContext"},
    }

    trim_id = _next_node_id(workflow)
    # Everything that consumed the raw decode output (CreateVideo, in every
    # shipped template) now consumes the trimmed one instead -- reserved/
    # rewired before insertion, same reasoning as context_id above.
    _rewire(workflow, [vae_decode_id, 0], [trim_id, 0])
    _rewire(workflow, [vae_decode_audio_id, 0], [trim_id, 1])
    workflow[trim_id] = {
        "class_type": "MiniMaxH3LoopTrim",
        "inputs": {
            "images": [vae_decode_id, 0],
            "trim_frames": [context_id, 1],
            "audio": [vae_decode_audio_id, 0],
            "fps": 24.0,
            "match_tail": True,
        },
        "_meta": {"title": "MiniMaxH3LoopTrim"},
    }

    save_id = _next_node_id(workflow)
    workflow[save_id] = {
        "class_type": "MiniMaxH3MotionContextSaveLatent",
        "inputs": {
            "latent": [sampler_advanced_id, 0],
            "filename_prefix": save_prefix,
            "clip_index": save_index,
        },
        "_meta": {"title": "MiniMaxH3MotionContextSaveLatent"},
    }

    return workflow
