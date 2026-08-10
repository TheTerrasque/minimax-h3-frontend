"""Splices an uploaded video into `LoadVideo` -> `GetVideoComponents`, so its
frames/audio can feed a node that wants them separately (e.g.
MiniMaxH3ReferenceToVideo's ref_video_N/ref_video_audio_N -- see
resources/COMFYUI_API_GUIDE.md #4 -- or, later, MiniMaxH3MotionContext's
context_frames/context_audio for clip-chaining, see extras.md).

Both node types confirmed live against this deployment's own ComfyUI
(GET /object_info/LoadVideo, /object_info/GetVideoComponents) rather than
guessed: LoadVideo's only input is `file` (a COMBO, same filename-widget
shape as LoadImage.inputs.image/LoadAudio.inputs.audio), output type VIDEO.
GetVideoComponents takes that VIDEO and outputs, in order,
(IMAGE frames, AUDIO, FLOAT fps, INT bit_depth) -- named "images"/"audio"/
"fps"/"bit_depth" per its own schema.
"""

from __future__ import annotations

from typing import Any


def _next_node_id(workflow: dict[str, Any]) -> str:
    return str(max(int(nid) for nid in workflow) + 1)


def add_load_video_node(workflow: dict[str, Any], filename: str) -> str:
    """Inserts `LoadVideo(filename) -> GetVideoComponents` into `workflow`
    and returns the GetVideoComponents node id -- index it as [id, 0] for
    frames (IMAGE) or [id, 1] for audio (AUDIO) when wiring a downstream
    input.

    `filename` is whatever a Load*-style filename widget already accepts
    elsewhere in this codebase (see integrations/comfyui.py's
    upload_media()): a plain uploaded name, or "subfolder/name". ComfyUI's
    own folder_paths.get_annotated_filepath() convention additionally
    accepts a "name [output]" suffix to point at an existing file in
    ComfyUI's output/ directory instead of input/ -- used by clip-chaining
    to reference the previous clip's already-rendered video directly,
    without downloading it through Django and re-uploading it (see
    integrations/motion_context.py). Not yet exercised against a real
    render as of this writing -- verify on the first real chained render.
    """
    load_video_id = _next_node_id(workflow)
    workflow[load_video_id] = {
        "class_type": "LoadVideo",
        "inputs": {"file": filename},
        "_meta": {"title": "LoadVideo"},
    }

    components_id = _next_node_id(workflow)
    workflow[components_id] = {
        "class_type": "GetVideoComponents",
        "inputs": {"video": [load_video_id, 0]},
        "_meta": {"title": "GetVideoComponents"},
    }
    return components_id
