"""Splices real motion/audio continuity (ethanfel/ComfyUI-MiniMaxH3-Contex-Loop,
see extras.md#contex-loop) into a Director clip's workflow, called from
generation/tasks.py's build_api_workflow() the same way integrations/
spectrum.py splices Spectrum in.

**Rewritten after live verification against a real install** -- an earlier
version of this module was built against nodes.py's MiniMaxH3MotionContext/
SaveLatent/LoadLatent classes, which turned out not to be registered as
usable ComfyUI nodes at all (see extras.md#contex-loop's "Verified against
a real install" section for that story). This version is built against the
extension's actual public API instead: the `chain_nodes.py` pipeline
(MiniMaxH3ChainPlan/LoopStart/Current/Context/SegmentSave), used one scene
at a time per job -- confirmed live, twice: a cheap Plan/LoopStart/Current
resolution-only submission (no GPU cost) to validate the plan_json/
scene-resolution contract, then two real renders (scene 1, then scene 2
resuming from scene 1's saved checkpoint) that both succeeded, with the
extracted first/last frames at the join visually near-identical -- genuine
continuity, not a guess.

Unlike the old design, this needs no "keep the previous job's ComfyUI
output around" mechanism at all: continuity is entirely mediated by
MiniMaxH3ChainSegmentSave's own checkpoint files on ComfyUI's disk
(addressed by `run_name` + scene number, validated internally against a
`generation_fingerprint` and each scene's own prompt/settings hash) --
Django never needs to reference a sibling job's raw output file. See
director/services.py for how `run_name`/scene numbering is tracked per
Clip and how the "shots" list (every scene from the start of the current
continuation run up to this one) is built fresh from current Clip data on
every render.

MiniMaxH3ChainLoopStart's `scene_range` set to a single scene number (e.g.
"3") renders exactly that one scene and terminates normally -- no
MiniMaxH3ChainLoopEnd (which would instead recursively render the rest of
the chain inside one ComfyUI submission via GraphBuilder) or
MiniMaxH3ChainReview (interactive, needs a live browser session) is wired
at all, keeping this a plain one-job-per-clip submission like every other
mode this app renders. This app's own SaveVideo/CreateVideo nodes are left
completely untouched -- MiniMaxH3ChainSegmentSave runs alongside purely for
its checkpoint side effect (confirmed live: it writes its own H.264
segment under output/h3_chains/<run_name>/, but this app's existing
download path never reads that file, only its own SaveVideo output).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from . import comfyui

_SAMPLER_PREP_CLASSES = ("MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo")

# Presence of this class is what actually gates everything in this module --
# confirmed live to be part of every real install (unlike the old
# MiniMaxH3MotionContext check, see this module's docstring).
CHAIN_CORE_NODE_CLASS = "MiniMaxH3ChainLoopStart"

_AVAILABILITY_CACHE_KEY = "director:motion_context_available"
_AVAILABILITY_CACHE_SECONDS = 60

# Passed as MiniMaxH3ChainPlan's generation_fingerprint -- per its own
# tooltip ("change this tag whenever the model, VAE, global references,
# CFG, scheduler, or other external generation settings change... enforced
# when resuming checkpoints"), confirmed live to gate resume validation.
# Bump this if this app's Director rendering path ever changes in a way
# that would make an old run's saved checkpoints unsafe to resume from
# (e.g. a different base model/VAE) -- resuming under an unchanged
# fingerprint after such a change is exactly what this exists to prevent.
GENERATION_FINGERPRINT = "director-v1"

DEFAULT_CONTEXT_LENGTH = 22  # H3_CHAIN_FORMAT_GUIDE.md's tested default.
DEFAULT_AUDIO_CONTEXT_LENGTH = 22
DEFAULT_SEGMENT_CRF = 28  # Checkpoint segment quality -- these aren't the delivered output, so bias toward smaller files over SegmentSave's own default(18).


def is_available() -> bool:
    """Whether the Contex-Loop extension is actually installed on the
    configured ComfyUI instance right now -- a cached live /object_info
    check, same shape as check_extras.py's manual diagnostic for Spectrum
    but called from the render path itself. Short TTL means installing the
    extension takes effect within a minute, not a process restart.
    """
    from django.core.cache import cache

    cached = cache.get(_AVAILABILITY_CACHE_KEY)
    if cached is not None:
        return cached
    available = comfyui.get_object_info(CHAIN_CORE_NODE_CLASS) is not None
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
    old_ref (a [node_id, output_index] pair) to new_ref instead -- see
    integrations/spectrum.py's apply_spectrum() for the same pattern."""
    for node in workflow.values():
        for key, value in node.get("inputs", {}).items():
            if isinstance(value, list) and len(value) == 2 and value == old_ref:
                node["inputs"][key] = list(new_ref)


def base_seed_for_run(run_name: str) -> int:
    """Deterministic per-run base seed, derived from `run_name` so every
    separate job submission for the same continuation run resolves
    identical per-scene seeds. MiniMaxH3ChainPlan hashes each scene's own
    derived seed into its checkpoint-compatibility validation (confirmed
    live -- the dumped plan state includes a per-shot "seed" field feeding
    a plan_hash) -- a caller that generated a fresh random base_seed on
    every submission would make every resume after the first fail
    validation. Exposed (not just internal) so director/services.py can
    keep using the *same* run_name -> base_seed mapping without duplicating
    the hash logic.
    """
    digest = hashlib.sha256(run_name.encode("utf-8")).hexdigest()
    return int(digest[:15], 16)  # comfortably within the node's UINT64-ish INT range


def apply_motion_context(
    workflow: dict[str, Any],
    *,
    shots: list[dict[str, str]],
    prompt_prefix: str,
    run_name: str,
    scene_number: int,
    width: int,
    height: int,
    default_duration_seconds: float,
    default_steps: int,
    context_length: int = DEFAULT_CONTEXT_LENGTH,
    audio_context_length: int = DEFAULT_AUDIO_CONTEXT_LENGTH,
    audio_mode: str = "generated_audio",
    segment_crf: int = DEFAULT_SEGMENT_CRF,
) -> dict[str, Any]:
    """Mutates and returns `workflow` (an already-loaded/patched mode
    template, see generation/tasks.py::build_api_workflow) with this
    scene's chain-driven prompt/length/seed/steps and real continuity.

    `shots` is every scene from the start of the current continuation run
    up to and including this one, in order -- `[{"id": ..., "prompt": ...},
    ...]`, `len(shots) == scene_number` (director/services.py builds this
    fresh from current Clip data every render, walking back through
    `continues_previous` predecessors). `run_name` must be identical across
    every submission for the same run (it addresses the checkpoint files
    on ComfyUI's disk); `scene_number` (1-based) is this clip's position
    within it -- 1 means a fresh run (nothing to resume), >1 means "load
    and validate the preceding scene's checkpoint," both handled entirely
    by MiniMaxH3ChainLoopStart itself, not anything this function does
    directly.
    """
    sampler_id = _find_one(workflow, _SAMPLER_PREP_CLASSES)
    random_noise_id = _find_one(workflow, ("RandomNoise",))
    scheduler_id = _find_one(workflow, ("BasicScheduler",))
    vae_decode_id = _find_one(workflow, ("VAEDecode",))
    vae_decode_audio_id = _find_one(workflow, ("VAEDecodeAudio",))
    sampler_advanced_id = _find_one(workflow, ("SamplerCustomAdvanced",))

    # Reuse whichever VAE loaders the template already wired up.
    video_vae_ref = workflow[sampler_id]["inputs"]["vae"]
    audio_vae_ref = workflow[vae_decode_audio_id]["inputs"]["vae"]

    plan_id = _next_node_id(workflow)
    workflow[plan_id] = {
        "class_type": "MiniMaxH3ChainPlan",
        "inputs": {
            "plan_json": json.dumps({"prompt_prefix": prompt_prefix, "shots": shots}),
            "run_name": run_name,
            "generation_fingerprint": GENERATION_FINGERPRINT,
            "width": width,
            "height": height,
            "context_length": context_length,
            "encode_mode": "video",
            "anchor_mode": "head",
            "crop": "disabled",
            "audio_mode": audio_mode,
            "audio_context_length": audio_context_length,
            "default_duration_seconds": default_duration_seconds,
            "default_steps": default_steps,
            "base_seed": base_seed_for_run(run_name),
            "segment_crf": segment_crf,
        },
        "_meta": {"title": "MiniMaxH3ChainPlan"},
    }

    loop_start_id = _next_node_id(workflow)
    workflow[loop_start_id] = {
        "class_type": "MiniMaxH3ChainLoopStart",
        "inputs": {"plan": [plan_id, 0], "start_clip": scene_number, "scene_range": str(scene_number)},
        "_meta": {"title": "MiniMaxH3ChainLoopStart"},
    }

    current_id = _next_node_id(workflow)
    workflow[current_id] = {
        "class_type": "MiniMaxH3ChainCurrent",
        "inputs": {"state": [loop_start_id, 1]},
        "_meta": {"title": "MiniMaxH3ChainCurrent"},
    }

    # Redirect the sampler-prep node's prompt/width/height/length, the
    # sampler's own seed, and the scheduler's step count to Current's
    # resolved values (output indices per MiniMaxH3ChainCurrent's schema:
    # 4=prompt, 5=noise_seed, 6=length, 7=steps, 8=width, 9=height) instead
    # of whatever build_api_workflow() already set from this job's own raw
    # fields -- Current computes the shared-prompt + scene-prompt
    # concatenation, the H3-valid frame count (including any continuation
    # overlap), and a run-consistent seed/step count from the plan, which
    # must be what's actually used for this to be a real continuation.
    sampler_inputs = workflow[sampler_id]["inputs"]
    prompt_ref = sampler_inputs.get("prompt")
    if isinstance(prompt_ref, list):
        # r2v's prompt is a separate PrimitiveStringMultiline node linked
        # into the sampler (see tasks.py's _R2V_NODES), not a literal on
        # the sampler itself -- redirect that node's value instead.
        workflow[prompt_ref[0]]["inputs"]["value"] = [current_id, 4]
    else:
        sampler_inputs["prompt"] = [current_id, 4]
    sampler_inputs["width"] = [current_id, 8]
    sampler_inputs["height"] = [current_id, 9]
    sampler_inputs["length"] = [current_id, 6]
    workflow[random_noise_id]["inputs"]["noise_seed"] = [current_id, 5]
    workflow[scheduler_id]["inputs"]["steps"] = [current_id, 7]

    # Reserve+rewire before inserting the node's own dict -- otherwise
    # rewiring every reference to [sampler_id, 0] would also catch this
    # node's own "conditioning": [sampler_id, 0] input, added below, and
    # redirect it to point at itself. Same reasoning as spectrum.py's
    # apply_spectrum(), which reserves its one new node's id before
    # rewiring for the same reason.
    context_id = _next_node_id(workflow)
    _rewire(workflow, [sampler_id, 0], [context_id, 0])
    workflow[context_id] = {
        "class_type": "MiniMaxH3ChainContext",
        "inputs": {
            "state": [current_id, 0],
            "conditioning": [sampler_id, 0],
            "vae": video_vae_ref,
            "latent": [sampler_id, 1],
            "audio_vae": audio_vae_ref,
        },
        "_meta": {"title": "MiniMaxH3ChainContext"},
    }

    trim_id = _next_node_id(workflow)
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

    # Output node -- no downstream rewiring needed. This app's own
    # SaveVideo/CreateVideo (unmodified, still fed from trim_id's outputs
    # via the _rewire calls above) remains the actual download source;
    # this exists purely for its checkpoint side effect.
    segment_save_id = _next_node_id(workflow)
    workflow[segment_save_id] = {
        "class_type": "MiniMaxH3ChainSegmentSave",
        "inputs": {
            "state": [current_id, 0],
            "images": [trim_id, 0],
            "sampled_latent": [sampler_advanced_id, 0],
            "audio": [trim_id, 1],
        },
        "_meta": {"title": "MiniMaxH3ChainSegmentSave"},
    }

    return workflow
