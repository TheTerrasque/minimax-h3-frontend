"""Thin client for ComfyUI Desktop's HTTP API.

Implements the flow documented in resources/COMFYUI_API_GUIDE.md (upload,
queue, poll, download, cleanup). Deliberately has no knowledge of any
specific workflow's node ids -- callers (generation.tasks) own the
API-format workflow JSON and patch it before calling queue_prompt().
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests
import websocket
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


def stream_execution_progress(
    prompt_id: str,
    client_id: str,
    sampler_node_id: str,
    on_update: Callable[[str, int | None, int | None], None],
    timeout: float = 900.0,
) -> None:
    """Connects to ComfyUI's `/ws?clientId=...` and calls
    on_update(phase, current, max) as prompt_id's execution moves through
    ComfyUI's three real phases (see resources/COMFYUI_API_GUIDE.md #7's
    "if you want live progress" note): preparing (model loading, pre-nodes),
    rendering (the sampler's steps), finishing (VAE decode/encode, disk
    write). phase is one of "preparing"/"rendering"/"finishing"; current/max
    are only non-None during "rendering" (the sampler's own `progress`
    messages -- step reached / total steps).

    Phase is inferred purely from node-execution order relative to
    sampler_node_id, the only node id whose semantic meaning we actually
    know here: every node ComfyUI executes before we've seen the sampler
    node counts as "preparing", the sampler node itself is "rendering",
    anything executed after it is "finishing".

    This is a best-effort side channel purely for progress display -- NOT
    the source of truth for success/failure or for the actual output.
    Callers must always separately call wait_for_result() + check_for_error()
    regardless of how this returns; this function returns (without raising)
    as soon as the prompt reports fully done (`executing` with node=None),
    reports an execution_error (letting the caller's own /history check
    produce the real error, so there's only one place that formats it), or
    on any connection problem/timeout -- swallowing its own exceptions
    rather than propagating them, since losing live progress is fine but
    failing the whole job over a WebSocket hiccup would not be.
    """
    ws = None
    try:
        ws_url = _base_url().replace("http://", "ws://", 1).replace("https://", "wss://", 1)
        ws = websocket.create_connection(f"{ws_url}/ws?clientId={client_id}", timeout=10)
        seen_sampler = False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ws.settimeout(max(1.0, deadline - time.monotonic()))
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not isinstance(raw, str):
                continue  # binary frames are preview-image bytes, not JSON events
            try:
                message = json.loads(raw)
            except ValueError:
                continue

            msg_type = message.get("type")
            data = message.get("data") or {}
            this_prompt = data.get("prompt_id")
            if this_prompt is not None and this_prompt != prompt_id:
                continue  # shouldn't happen (client_id is per-job), but be defensive

            if msg_type == "executing":
                node = data.get("node")
                if node is None:
                    return  # ComfyUI's own signal that this prompt is fully done
                if node == sampler_node_id:
                    seen_sampler = True
                    on_update("rendering", None, None)
                else:
                    on_update("finishing" if seen_sampler else "preparing", None, None)
            elif msg_type == "progress" and data.get("node") == sampler_node_id:
                on_update("rendering", data.get("value"), data.get("max"))
            elif msg_type == "execution_error":
                return
    except Exception:
        return
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


def get_history(prompt_id: str) -> dict[str, Any] | None:
    """One-shot check of GET /history/{prompt_id} -- the record if ComfyUI
    has it (finished, successfully or not), None if it doesn't (still
    running, never existed, or evicted from history). Unlike
    wait_for_result(), this never polls/blocks -- used by
    generation.tasks.recover_orphaned_processing_jobs() to check whether a
    job that was PROCESSING when the server restarted actually finished
    while nothing was watching, without re-waiting for something that may
    already be done.
    """
    resp = requests.get(f"{_base_url()}/history/{prompt_id}", timeout=15)
    resp.raise_for_status()
    return resp.json().get(prompt_id)


def is_prompt_queued(prompt_id: str) -> bool:
    """Whether prompt_id is still sitting in ComfyUI's own queue (running
    or pending) right now. Used alongside get_history() during orphaned-job
    recovery to tell "still genuinely rendering, pick the wait back up"
    apart from "ComfyUI has no record of this at all anymore" -- those need
    very different recovery actions.
    """
    resp = requests.get(f"{_base_url()}/queue", timeout=15)
    resp.raise_for_status()
    data = resp.json()
    queued_ids = {entry[1] for entry in data.get("queue_running", [])}
    queued_ids |= {entry[1] for entry in data.get("queue_pending", [])}
    return prompt_id in queued_ids


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


def get_object_info(class_type: str) -> dict[str, Any] | None:
    """GET /object_info/{class_type} -- ComfyUI's own registry of installed
    node types. Returns that node's schema dict if it's registered, None if
    not. Confirmed live against a real instance: ComfyUI answers 200 with an
    empty {} for an unknown class_type -- it never 404s here, so an empty
    body (not the HTTP status) is the actual "not installed" signal.

    Purely a diagnostic (see generation/management/commands/check_extras.py,
    extras.md) -- never called from the actual render path (tasks.py), which
    finds out whether a node exists the same way it always has: ComfyUI's
    own /prompt validation rejects an unknown node type with a clear error.
    """
    resp = requests.get(f"{_base_url()}/object_info/{class_type}", timeout=15)
    resp.raise_for_status()
    return resp.json().get(class_type)


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
