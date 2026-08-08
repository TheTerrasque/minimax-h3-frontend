"""Splices the Spectrum step-forecasting accelerator
(xmarre/ComfyUI-Spectrum-MiniMax-H3, see extras.md#spectrum) into an
already-loaded MiniMax H3 API-format workflow, when settings.SPECTRUM_LEVEL
enables it for a job (generation/api.py::_resolve_use_spectrum,
GenerationJob.use_spectrum) -- see generation/tasks.py::build_api_workflow().

The Spectrum node is a MODEL -> MODEL wrapper meant to sit right after the
workflow's model loader ("model loader -> LoRA -> Spectrum -> guider/
sampler" per its own README). Every resources/workflows_api/*.api.json was
verified to have exactly one UNETLoader node, so inserting Spectrum is a
generic graph operation: find that node, rewire every existing reference to
its output to the new node instead, then wire the new node's own model
input back to the loader.

Default parameters below are the extension's own "preliminary default
preset" (see extras.md#spectrum for the version this was checked against)
-- not tuned by this project. The literal class_type
("SpectrumApplyMiniMaxH3") comes from that same README; if a future
extension release renames it, this function still succeeds (it's just
building a dict), but ComfyUI's own /prompt validation will reject the job
with a clear unknown-node-type error, surfaced via the existing
job.error_message path the same as any other bad workflow -- no new error
handling needed.
"""

from __future__ import annotations

from typing import Any

_UNET_LOADER_CLASS = "UNETLoader"
# Public (not _-prefixed): manage.py check_extras imports this to check
# comfyui.get_object_info() for it, rather than duplicating the literal
# class name in two places.
SPECTRUM_NODE_CLASS = "SpectrumApplyMiniMaxH3"

_DEFAULT_SPECTRUM_INPUTS: dict[str, Any] = {
    "enabled": True,
    "blend_weight": 0.5,
    "degree": 1,
    "ridge_lambda": 0.10,
    "window_size": 2.0,
    "flex_window": 0.75,
    "warmup_steps": 1,
    "tail_actual_steps": 1,
    "max_history": 8,
    "history_storage": "system_ram",
    "bootstrap_first_forecast": True,
}


def _next_node_id(workflow: dict[str, Any]) -> str:
    return str(max(int(nid) for nid in workflow) + 1)


def apply_spectrum(workflow: dict[str, Any]) -> dict[str, Any]:
    """Mutates and returns `workflow` with a Spectrum node spliced in right
    after its sole UNETLoader. Raises RuntimeError if the workflow doesn't
    have exactly one -- every shipped template does (see module docstring);
    a mismatch means the template's shape changed and this needs
    revisiting, not a silently wrong render.
    """
    loader_ids = [nid for nid, node in workflow.items() if node.get("class_type") == _UNET_LOADER_CLASS]
    if len(loader_ids) != 1:
        raise RuntimeError(
            f"apply_spectrum: expected exactly one {_UNET_LOADER_CLASS} node, found {len(loader_ids)}"
        )
    loader_id = loader_ids[0]

    node_id = _next_node_id(workflow)
    for node in workflow.values():
        for value in node.get("inputs", {}).values():
            if isinstance(value, list) and len(value) == 2 and value[0] == loader_id:
                value[0] = node_id

    workflow[node_id] = {
        "class_type": SPECTRUM_NODE_CLASS,
        "inputs": {"model": [loader_id, 0], **_DEFAULT_SPECTRUM_INPUTS},
        "_meta": {"title": "Spectrum Apply MiniMax H3"},
    }
    return workflow
