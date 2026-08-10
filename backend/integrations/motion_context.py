"""Splices motion/audio continuity (ethanfel/ComfyUI-MiniMaxH3-Contex-Loop,
see extras.md#contex-loop) between two separately-submitted ComfyUI jobs --
the low-level, non-interactive half of that extension only
(MiniMaxH3MotionContext/LoopTrim/Save|LoadLatent), not its own
Plan/Loop/Review-Gate pipeline, which runs a whole multi-scene chain as one
ComfyUI graph submission and needs a live interactive browser session -- see
the Director Mode plan's "Extension research" section for why that pipeline
isn't used. This module drives the same job-at-a-time model as the rest of
this codebase's queue, called from generation/tasks.py's build_api_workflow()
exactly the way integrations/spectrum.py already splices Spectrum in -- see
that module for the same "find node(s), rewire references, insert" pattern
this follows.

Unlike spectrum.py, this needs no explicit per-mode node-id table: every
node this touches (the mode's sampler-prep node, BasicGuider,
SamplerCustomAdvanced, VAEDecode, VAEDecodeAudio) is unique-by-class_type in
every shipped template (t2v/i2v/r2v), confirmed by inspecting
resources/workflows_api/*.api.json directly -- so this discovers them the
same generic way spectrum.py discovers its sole UNETLoader, rather than
needing a hand-exported reference workflow to hardcode ids from.

Node input/output schemas below (MiniMaxH3MotionContext/LoopTrim/
Save|LoadLatent's exact INPUT_TYPES/RETURN_TYPES, and Save/LoadLatent's
actual path-resolution logic) were read from that extension's nodes.py
source. **KNOWN BROKEN as of this writing, confirmed against a real install**
(see extras.md#contex-loop's "Verified against a real install" section for
the full story): nodes.py defines MiniMaxH3MotionContext/
MiniMaxH3MotionContextSaveLatent/MiniMaxH3MotionContextLoadLatent as plain
Python classes, but the extension's __init__.py only registers
MiniMaxH3LoopTrim in NODE_CLASS_MAPPINGS -- the other three are internal
helpers only reachable through chain_nodes.py's own higher-level pipeline
(MiniMaxH3ChainContext/MiniMaxH3ChainSegmentSave/etc., a different,
larger integration effort -- see extras.md), not usable as standalone
splice targets the way this module assumes. In practice this means
apply_motion_context()'s full-context branch (source_filename given) can
never succeed against a real install: ComfyUI's /prompt validation
rejects MiniMaxH3MotionContext/SaveLatent/LoadLatent as unknown node
types every time (the same graceful-failure spectrum.py's docstring
describes) -- but is_available() below checks for exactly that same
unregistered class, so it correctly always reports unavailable and
director/services.py's fallback always engages instead. The graceful
fallback is doing real work here, not the full-continuity splice; don't
trust the latter until this module is rewritten against the actually-
registered chain_nodes.py API.

Graceful fallback when the extension isn't installed (or, right now,
always, per above): director/services.py checks is_available() before
ever calling apply_motion_context() here, and uses a much simpler
fallback instead (feeding the previous clip's last frame as an ordinary
image reference -- no special nodes needed at all) -- see that module's
_build_job_for_clip() and extras.md#contex-loop's "Graceful fallback"
section for the full picture. This module has no fallback logic of its
own; it's only ever called once the caller has already confirmed the
(currently unreachable) real nodes exist.
"""

from __future__ import annotations

from typing import Any

from . import comfyui, video_ref

_SAMPLER_PREP_CLASSES = ("MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo")

# class_type of the one node whose presence gates everything in this module
# -- see is_available(). The other three (LoopTrim, Save/LoadLatent) are
# part of the same extension and, per its own repo layout, always installed
# together, so checking just this one is representative without four round
# trips to ComfyUI.
MOTION_CONTEXT_NODE_CLASS = "MiniMaxH3MotionContext"

_AVAILABILITY_CACHE_KEY = "director:motion_context_available"
_AVAILABILITY_CACHE_SECONDS = 60

# Tested-default context lengths per H3_CHAIN_FORMAT_GUIDE.md ("use 22 for
# the tested balance of continuity and delivered footage"; audio likewise).
DEFAULT_CONTEXT_LENGTH = 22
DEFAULT_AUDIO_CONTEXT_LENGTH = 22


def is_available() -> bool:
    """Whether MOTION_CONTEXT_NODE_CLASS is actually installed on the
    configured ComfyUI instance right now -- same live /object_info check
    check_extras.py already does for Spectrum, but called from the render
    path itself (not just a manual diagnostic), so it's cached briefly
    rather than hitting ComfyUI on every clip render/edit. A short TTL
    means installing the extension takes effect within a minute, not a
    process restart.

    Per this module's own docstring: MOTION_CONTEXT_NODE_CLASS is never
    actually registered by a real install of the extension (confirmed live
    -- only MiniMaxH3LoopTrim and the chain_nodes.py pipeline are), so this
    currently always returns False in practice regardless of whether the
    extension itself is installed. Kept as-is (rather than hardcoded to
    False) so this starts working automatically the moment either a future
    extension release registers that class directly, or this module gets
    rewritten against chain_nodes.py's actual API and this constant is
    repointed at one of *those* class names instead.
    """
    from django.core.cache import cache

    cached = cache.get(_AVAILABILITY_CACHE_KEY)
    if cached is not None:
        return cached
    available = comfyui.get_object_info(MOTION_CONTEXT_NODE_CLASS) is not None
    cache.set(_AVAILABILITY_CACHE_KEY, available, timeout=_AVAILABILITY_CACHE_SECONDS)
    return available


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
    save_prefix: str,
    save_index: int,
    source_filename: str | None = None,
    source_subfolder: str = "",
    load_prefix: str | None = None,
    load_index: int | None = None,
    context_length: int = DEFAULT_CONTEXT_LENGTH,
    audio_context_length: int = DEFAULT_AUDIO_CONTEXT_LENGTH,
) -> dict[str, Any]:
    """Mutates and returns `workflow` (an already-loaded/patched mode
    template, see generation/tasks.py::build_api_workflow).

    `save_prefix`/`save_index` are always given -- every director-rendered
    clip saves its own checkpoint (a plain MiniMaxH3MotionContextSaveLatent
    tapping the sampler's own raw output, nothing else) whenever the
    extension is available, *regardless* of whether this clip itself
    continues another one, purely so a *later* clip has something to
    continue *from* if it turns out to want to (see director/models.py's
    Clip.checkpoint_filename_prefix/_clip_index -- set unconditionally at
    job-creation time in director/services.py).

    `source_filename`/`source_subfolder` (the previous clip's own rendered
    video, still sitting on ComfyUI's machine -- see GenerationJob.
    keep_comfyui_output/comfyui_output_filename) are only given when this
    clip actually continues a predecessor. When given, splices the full
    MiniMaxH3MotionContext -> ... -> MiniMaxH3LoopTrim chain in too (fed
    through LoadVideo/GetVideoComponents via video_ref.add_load_video_node(),
    using ComfyUI's own folder_paths "name [output]" annotation convention
    to point at output/ instead of re-uploading into input/). When omitted
    (a fresh scene, or the first clip of a chain), only the SaveLatent node
    is added -- MiniMaxH3MotionContext's own context_frames input is
    required (not optional) per its schema, so there's no way to run it at
    all without a real source video, and no need to: a clip that isn't
    continuing anything has nothing else for it to do anyway.

    `load_prefix`/`load_index` are the *previous* clip's own saved
    checkpoint (None if it was rendered before the extension was installed,
    in which case MiniMaxH3MotionContext runs without context_latent -- see
    its schema, that input is optional -- continuity still comes from
    context_frames/context_audio alone).
    """
    sampler_advanced_id = _find_one(workflow, ("SamplerCustomAdvanced",))

    if source_filename is not None:
        sampler_id = _find_one(workflow, _SAMPLER_PREP_CLASSES)
        # Not otherwise referenced -- _rewire() below finds BasicGuider's
        # conditioning input generically, this call is purely the same
        # exactly-one structural assertion _find_one() makes for every
        # other node type here (fails loudly if a future template changes
        # shape).
        _find_one(workflow, ("BasicGuider",))
        vae_decode_id = _find_one(workflow, ("VAEDecode",))
        vae_decode_audio_id = _find_one(workflow, ("VAEDecodeAudio",))

        # Reuse whichever VAE loaders the template already wired up, rather
        # than adding duplicates -- the sampler-prep node's own "vae" input
        # is the video VAE; VAEDecodeAudio's "vae" input is the audio VAE.
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
        # references to, say, [sampler_id, 0] would also catch the new
        # context node's own "conditioning": [sampler_id, 0] input (added
        # below) and rewrite it to point at itself. Same reasoning as the
        # trim node further down. Mirrors spectrum.py's apply_spectrum(),
        # which reserves its one new node's id before rewiring for the same
        # reason.
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
        # Everything that consumed the raw decode output (CreateVideo, in
        # every shipped template) now consumes the trimmed one instead --
        # reserved/rewired before insertion, same reasoning as context_id
        # above.
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
