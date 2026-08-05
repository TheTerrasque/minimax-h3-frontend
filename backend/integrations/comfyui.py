"""Thin client for ComfyUI Desktop's HTTP API.

Implements the flow documented in resources/COMFYUI_API_GUIDE.md (upload,
queue, poll, download, cleanup). Deliberately has no knowledge of any
specific workflow's node ids -- callers (generation.tasks) own the
API-format workflow JSON and patch it before calling queue_prompt().
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import requests
from django.conf import settings


class ComfyUIError(RuntimeError):
    """Raised for ComfyUI API failures, including per-node validation errors."""


class ComfyUIExecutionError(ComfyUIError):
    """Raised when a queued prompt reached /history but finished with an
    error status (e.g. a CUDA OOM caught server-side) rather than crashing
    the connection outright. See check_for_error()."""


@dataclass
class ComfyUIOutput:
    filename: str
    subfolder: str
    type: str


def _base_url() -> str:
    return settings.COMFYUI_BASE_URL.rstrip("/")


def upload_media(file_bytes: bytes, filename: str, subfolder: str = "") -> str:
    """Uploads any media file (image/audio/video) into ComfyUI's input folder.

    There is only one generic upload route in ComfyUI -- POST /upload/image --
    and despite the name it accepts any file type; the form field is always
    named "image". Returns the name to set as a Load*/ref_* node's filename
    widget value (see resources/COMFYUI_API_GUIDE.md #5).
    """
    resp = requests.post(
        f"{_base_url()}/upload/image",
        files={"image": (filename, file_bytes)},
        data={"type": "input", "subfolder": subfolder, "overwrite": "true"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return f"{data['subfolder']}/{data['name']}" if data["subfolder"] else data["name"]


def queue_prompt(api_workflow: dict[str, Any], client_id: str) -> str:
    """POSTs an API-format workflow. Returns the prompt_id."""
    resp = requests.post(
        f"{_base_url()}/prompt",
        json={"prompt": api_workflow, "client_id": client_id},
        timeout=30,
    )
    if resp.status_code >= 400:
        raise ComfyUIError(f"ComfyUI rejected the prompt: {resp.text}")
    resp.raise_for_status()
    return resp.json()["prompt_id"]


def wait_for_result(
    prompt_id: str, poll_seconds: float = 3.0, timeout: float = 900.0
) -> dict[str, Any]:
    """Polls GET /history/{prompt_id} until it's populated (or times out)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = requests.get(f"{_base_url()}/history/{prompt_id}", timeout=15)
        resp.raise_for_status()
        history = resp.json()
        if prompt_id in history:
            return history[prompt_id]
        time.sleep(poll_seconds)
    raise TimeoutError(f"ComfyUI prompt {prompt_id} did not finish within {timeout}s")


def check_for_error(history_record: dict[str, Any]) -> None:
    """Raises ComfyUIExecutionError if the prompt finished with an error
    status (e.g. an out-of-memory error caught by ComfyUI itself rather than
    crashing the process/connection -- see benchmark_render_times).

    Call this right after wait_for_result() and before extract_video_output()
    -- a failed prompt has no populated outputs, so skipping this check turns
    into a confusing KeyError instead of a clear error message.
    """
    status = history_record.get("status", {})
    if status.get("status_str") != "error":
        return
    error_messages = [m[1] for m in status.get("messages", []) if m[0] == "execution_error"]
    detail = error_messages[-1] if error_messages else status
    raise ComfyUIExecutionError(f"ComfyUI execution failed: {detail}")


def is_alive(timeout: float = 5.0) -> bool:
    """Cheap reachability check (GET /system_stats) -- used to tell a
    genuinely crashed/unreachable ComfyUI process apart from a prompt that's
    just still running. See benchmark_render_times."""
    try:
        resp = requests.get(f"{_base_url()}/system_stats", timeout=timeout)
        return resp.status_code == 200
    except requests.exceptions.RequestException:
        return False


def extract_video_output(history_record: dict[str, Any], node_id: str) -> ComfyUIOutput:
    """Reads the SaveVideo node's output out of a /history record.

    Non-obvious: SaveVideo's UI payload reuses the "images" key that
    image-save nodes use (see resources/COMFYUI_API_GUIDE.md #8).
    """
    node_output = history_record["outputs"][node_id]
    entry = node_output["images"][0]
    return ComfyUIOutput(filename=entry["filename"], subfolder=entry["subfolder"], type=entry["type"])


def download_output(output: ComfyUIOutput) -> bytes:
    resp = requests.get(
        f"{_base_url()}/view",
        params={"filename": output.filename, "subfolder": output.subfolder, "type": output.type},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.content


def delete_output_file(output: ComfyUIOutput) -> None:
    """Deletes the on-disk output file directly, so it doesn't linger on the
    ComfyUI machine after we've downloaded it. Only works when the caller can
    reach ComfyUI's filesystem (settings.COMFYUI_OUTPUT_ROOT) -- see
    resources/COMFYUI_API_GUIDE.md #10 for the remote-instance fallback.
    """
    output_root = getattr(settings, "COMFYUI_OUTPUT_ROOT", "")
    if not output_root:
        return
    path = os.path.join(output_root, output.subfolder, output.filename)
    if os.path.isfile(path):
        os.remove(path)


def clear_history(prompt_id: str) -> None:
    requests.post(f"{_base_url()}/history", json={"delete": [prompt_id]}, timeout=15)
