# Working with the ComfyUI (Desktop) API from Python / Django

This document explains how to drive **ComfyUI Desktop** programmatically from Python (aimed at a
Django project) to run the MiniMax H3 video workflows in
[`resources/workflows/`](./workflows/) (`video_minimax_h3_t2v.json`, `_i2v.json`, `_r2v.json`),
supply reference images/audio/video and first/last frames where each workflow supports them,
retrieve the finished video, and avoid leaving a permanent copy sitting on the ComfyUI machine
after the app has downloaded it.

Written from web research + direct inspection of this repo's own workflow files, cross-checked
against live `/object_info` responses from this project's actual ComfyUI instance (Aug 2026). That
instance is a portable ComfyUI 0.30.0 install on a separate networked GPU machine (`gpusun`, RTX
3090), reachable at `http://gpusun:8188` — not literally "ComfyUI Desktop" despite this doc's
title, though the API itself is identical either way. Always sanity-check against the live
server's `/object_info/<NodeClass>` when wiring this up if you're pointed at a different ComfyUI
instance — node schemas do change between ComfyUI releases, and the MiniMax H3 nodes here are
custom nodes.

## 1. Connecting

ComfyUI (both Desktop and portable) exposes a plain HTTP + WebSocket API on localhost. There is
**no authentication** by default — anything that can reach the port can submit jobs and read every
file in the input/output/temp folders. Treat the base URL as trusted-local-only unless you've
specifically put an authenticating reverse proxy in front of it.

- **ComfyUI Desktop** listens on `http://127.0.0.1:8000` by default.
- **Portable / manual `python main.py`** installs default to `http://127.0.0.1:8188`.

Desktop's actual host/port is configurable under **Settings → Server Configuration** in the app,
persisted in `Documents\ComfyUI\user\default\comfy.settings.json`. Don't hardcode `8000` in
Django settings — make it a configurable value (env var / Django setting), since it changes if the
default port is already taken on the user's machine.

```python
# settings.py
COMFYUI_BASE_URL = env("COMFYUI_BASE_URL", default="http://127.0.0.1:8000")
```

## 2. Where this belongs in a Django app: not inside a request/response cycle

A single generation takes **minutes** (diffusion sampling + video encode). Never do the
submit-and-wait loop inside a Django view — it'll hit the WSGI/ASGI worker timeout. Treat it as a
background job:

- **Celery task** (most common in Django) that uploads assets, submits the prompt, polls for
  completion, downloads the result, and saves it to a model `FileField`. This is what the examples
  below assume.
- If you want live progress in the browser, have the Celery task forward ComfyUI's WebSocket
  `progress` messages onto a Django Channels group instead of making the browser talk to ComfyUI
  directly — don't expose the unauthenticated ComfyUI port to the browser/internet.
- Polling `GET /history/{prompt_id}` on an interval (e.g. every 2–5s) is simpler than holding a
  WebSocket open from inside a worker process and is fine for a task that just needs the end
  result. Use the WebSocket only when you actually want intermediate progress.

## 3. Workflow JSON has two formats — you need the API one

The files in [`resources/workflows/`](./workflows/) are saved in the **UI/editor format**
(`{id, revision, nodes, links, groups, definitions, ...}` — the same JSON ComfyUI writes when you
hit "Save" in the graph editor). This is **not** what `POST /prompt` accepts. All three workflows
also wrap the sampler chain in a reusable **subgraph** node (t2v/i2v use one called
"Image to Video (MiniMax H3)") — a purely visual/organizational feature.

The API only understands the flat **API format**: a JSON object keyed by node id, each entry
`{"class_type": "...", "inputs": {...}, "_meta": {"title": "..."}}`. Example shape (this project's
`SaveVideo` node):

```json
{
  "92": {
    "class_type": "SaveVideo",
    "inputs": { "video": ["91", 0], "filename_prefix": "video/MiniMax_H3", "format": "auto", "codec": "auto" },
    "_meta": { "title": "Save Video" }
  }
}
```

To get this for one of these workflows:

1. Open the `.json` in ComfyUI Desktop's graph editor (drag it in, or Workflow → Open).
2. Settings → enable **"Dev mode Options"** (or use **File → Export (API)** if already visible).
3. **Save (API Format)** / **Export (API)** — this flattens the subgraph into plain numbered nodes
   automatically, so the exported JSON has real `MiniMaxH3ImageToVideo`, `UNETLoader`, `SaveVideo`,
   etc. entries you can address directly.

Do this once per workflow and commit the exported API-format JSON so your Django code has a stable,
script-friendly template to load (`json.load`) and mutate at runtime (prompt text, seed,
`filename_prefix`, reference assets) before POSTing.

**This project does this differently:** instead of the manual UI export above,
`backend/scripts/export_workflow_api.py` reimplements ComfyUI's export mechanically from a UI-format
workflow + live `/object_info` responses (handles the subgraph flattening and dynamic
reference-list inputs described below). Its docstring documents exactly how each serialization rule
was verified. The three workflows' exported output already lives in
[`resources/workflows_api/`](./workflows_api/) — re-run the script if a workflow in
`resources/workflows/` changes. `generation/tasks.py` patches the resulting JSON's node ids
directly; see `ARCHITECTURE.md`'s "Getting the workflows working" section for the full picture.

## 4. What each workflow supports, and where reference media plugs in

All three route through a MiniMax H3 sampler node, but the node differs per workflow and that's
what determines which extra assets you can feed in.

| Workflow | Sampler node | Extra inputs available |
|---|---|---|
| `video_minimax_h3_t2v.json` | `MiniMaxH3ImageToVideo` | none connected — text only |
| `video_minimax_h3_i2v.json` | `MiniMaxH3ImageToVideo` | `first_frame`, optional `last_frame` (both `IMAGE`) |
| `video_minimax_h3_r2v.json` | `MiniMaxH3ReferenceToVideo` | up to 9 `ref_image_N`; up to 3 each of `ref_video_N`, `ref_video_audio_N`, `ref_audio_N` |

### t2v / i2v — `MiniMaxH3ImageToVideo`

Inputs (from the exported API JSON): `clip`, `vae`, `first_frame` (`IMAGE`, optional),
`last_frame` (`IMAGE`, optional), `prompt` (`STRING`), `width`, `height`, `length` (all `INT`).

- **`t2v`** simply leaves `first_frame`/`last_frame` unconnected — pure text-to-video.
- **`i2v`** connects a `LoadImage` node's output to `first_frame` only. **To do first→last-frame
  interpolation** (start on one image, end on another), also wire a second `LoadImage` into
  `last_frame` — the node graph already exposes the socket, this project's `i2v` workflow just
  doesn't use it by default.
- **There is no separate audio input on this node.** Audio is generated by the model itself from
  the text prompt — this project's own example prompt embeds an explicit audio description
  (`"Audio: lo-fi vaporwave score, slow drum machine..."`) as prose inside the `prompt` string, and
  the graph decodes model-generated audio via a dedicated `VAEDecodeAudio` node downstream. If you
  want narration/music/sfx in the output, describe it in the prompt text — don't look for an audio
  socket on this node.
- **Duration is set indirectly.** A `PrimitiveFloat` node ("duration", seconds) feeds a
  `ComfyMathExpression` (`max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17`) that
  converts seconds → a valid frame `length` for the model (it snaps to the frame-count increments
  MiniMax H3 requires). Easiest approach: after exporting to API format, find that `PrimitiveFloat`
  node and just change its value (seconds) — let the existing math node keep computing a valid
  `length` for you, rather than hand-computing frame counts.
- `width`/`height` come from a `ResolutionSelector` node (aspect-ratio preset picker). You can
  either change its preset string widget, or set `width`/`height` directly further downstream —
  check the exported JSON to see which is simpler for your case.

### r2v — `MiniMaxH3ReferenceToVideo`

This node takes **dynamic lists** of reference assets (ComfyUI's `COMFY_AUTOGROW_V3` input type) —
confirmed directly against this project's live `GET /object_info/MiniMaxH3ReferenceToVideo`:

- `ref_images.ref_image_0` .. `ref_image_8` — up to **9** reference **images** (`IMAGE`, each
  downscaled to a 2048px short edge if larger, never upscaled). Used for subject/style/character
  references the video should be based on. This project's saved `r2v` workflow wires 2 of them as
  an example.
- `ref_videos.ref_video_0` .. `ref_video_2` — up to **3** reference **videos**. Confirmed typed
  `IMAGE` server-side (tooltip: "Reference video frames at 24 fps (2-15s)") — it wants a frame
  sequence, not a raw video file; feed it via a `LoadVideo` + frame-extraction node, not `LoadVideo`
  directly.
- `ref_video_audios.ref_video_audio_0` .. `ref_video_audio_2` — up to **3**, each the **audio
  track** (`AUDIO`) belonging to the same-numbered `ref_video_N`.
- `ref_audios.ref_audio_0` .. `ref_audio_2` — up to **3** standalone reference **audio** clips
  (`AUDIO`), independent of any reference video (e.g. a voice or music reference).
- `prompt`, `width`, `height`, `length` — same shape as the i2v node.
- `ref_image_size` (combo: `"match"` | `"max"`) — resize mode for how reference images relate to
  the generation's output dimensions. `"match"` scales each ref down (never up) to the generation's
  pixel area; `"max"` uses a fixed 2048px short edge for best identity fidelity but runs several
  times slower (reference tokens ride through every sampling step).

The API inputs dict key for each materialized list item is the literal dotted name shown above
(e.g. `"ref_images.ref_image_0"`, not a bare `"ref_image_0"`) — confirmed directly from saved
workflow JSON, where ComfyUI itself records that exact string as the input's `name`. The min/max
counts per group are enforced by the node's own schema (not something you can exceed in raw JSON) —
re-check `/object_info/MiniMaxH3ReferenceToVideo` if a future ComfyUI/node-pack version might have
changed them.

To feed `ref_video_N` / `ref_audio_N` / `ref_video_audio_N`, use ComfyUI's core `LoadVideo` /
`LoadAudio` nodes (outputting `VIDEO`/`AUDIO`) the same way `LoadImage` is used for `ref_image_N` —
upload the file first (see §5), then set that node's filename widget to the uploaded name. (Not yet
implemented in this project's `generation/tasks.py` — only `ref_image_N` is wired so far.)

## 5. Uploading media (images, audio, video) — all through one endpoint

Every `Load*` node (`LoadImage`, `LoadAudio`, `LoadVideo`) reads from ComfyUI's own `input/`
folder, addressed by filename. There is **only one generic upload route** in ComfyUI's server
(confirmed from `server.py`) — `POST /upload/image` — and despite the name it accepts **any file
type**, not just images; the browser upload widgets for audio/video reuse the same route with the
file under the same form field name (`image`). Don't be misled by the endpoint name when uploading
an `.mp3` or `.mp4`.

```python
import requests

def comfy_upload(base_url: str, file_bytes: bytes, filename: str, subfolder: str = "") -> str:
    """Uploads any media file (image/audio/video) into ComfyUI's input folder.
    Returns the name to use as a Load*/ref_* node's filename widget value."""
    resp = requests.post(
        f"{base_url}/upload/image",
        files={"image": (filename, file_bytes)},
        data={"type": "input", "subfolder": subfolder, "overwrite": "true"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()  # {"name": "...", "subfolder": "...", "type": "input"}
    return f"{data['subfolder']}/{data['name']}" if data["subfolder"] else data["name"]
```

Set the corresponding node's filename widget (`LoadImage.inputs.image`, `LoadAudio.inputs.audio`,
`LoadVideo.inputs.video` — check the exact widget name in your exported JSON) to the returned name
before submitting the prompt.

## 6. Queuing the job

```python
import uuid
import requests

client_id = str(uuid.uuid4())

resp = requests.post(
    f"{COMFYUI_BASE_URL}/prompt",
    json={"prompt": api_format_workflow, "client_id": client_id},
    timeout=30,
)
resp.raise_for_status()
prompt_id = resp.json()["prompt_id"]
```

Success (`200`): `{"prompt_id": "...", "number": 3, "node_errors": {}}`.
Validation failure (`400`): `{"error": {...}, "node_errors": {"<node id>": {"errors": [...]}}}` —
this is where a bad widget value or a missing model file shows up; surface `node_errors` in your
task's failure state rather than swallowing it.

## 7. Waiting for completion (polling — simplest from a Celery task)

```python
import time
import requests

def wait_for_result(base_url: str, prompt_id: str, poll_seconds: float = 3.0, timeout: float = 900.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        history = requests.get(f"{base_url}/history/{prompt_id}", timeout=15).json()
        if prompt_id in history:
            return history[prompt_id]
        time.sleep(poll_seconds)
    raise TimeoutError(f"ComfyUI prompt {prompt_id} did not finish within {timeout}s")
```

Video generation is slow (minutes) — pick a generous `timeout` and a Celery `soft_time_limit` to
match.

**Check for a server-side failure before touching `outputs`.** A prompt ComfyUI accepted at
`POST /prompt` time can still fail during execution (an OOM it caught itself, a bad reference
asset, etc.) — `wait_for_result()` above returns as soon as *anything* lands in `/history` for
that `prompt_id`, success or failure alike. Skipping this check turns a clear failure into a
confusing `KeyError` reaching into an `outputs` dict that was never populated:

```python
def check_for_error(history_record: dict) -> None:
    status = history_record.get("status", {})
    if status.get("status_str") != "error":
        return
    error_messages = [m[1] for m in status.get("messages", []) if m[0] == "execution_error"]
    detail = error_messages[-1] if error_messages else status
    raise RuntimeError(f"ComfyUI execution failed: {detail}")

history = wait_for_result(base_url, prompt_id)
check_for_error(history)  # raises before you ever look at history["outputs"]
```

**Telling "still rendering" apart from "the whole ComfyUI process died."** A `requests.get`
inside the poll loop naturally raises `requests.exceptions.ConnectionError`/`Timeout` if the
server itself goes away mid-poll (crashed, not just slow) — that propagates straight out of
`wait_for_result()` already. If you're doing something that might push ComfyUI into a hard crash
rather than a caught OOM (e.g. sweeping unknown resolution/duration combinations to find the
limit), check reachability before each attempt too, so you fail fast with a clear message instead
of retrying against a dead server:

```python
def is_alive(base_url: str, timeout: float = 5.0) -> bool:
    try:
        return requests.get(f"{base_url}/system_stats", timeout=timeout).status_code == 200
    except requests.exceptions.RequestException:
        return False
```

If you do want live progress (e.g. to drive a progress bar via Django Channels), connect
`ws://<host>:<port>/ws?clientId=<client_id>` with `websocket-client` instead and read JSON frames;
key message types: `status` (queue depth), `executing` (`data.node is None` ⇒ prompt fully done),
`progress` (`data.value`/`data.max` — sampler step), `execution_error`.

## 8. Finding the output in `/history`

```python
outputs = history_record["outputs"]["92"]  # "92" = this project's SaveVideo node id
```

Non-obvious quirk: `SaveVideo`'s UI payload reuses the **same `"images"` key** that image-save
nodes use (a leftover convention from how animated outputs have always been reported), plus an
`"animated"` flag:

```json
{
  "images": [
    { "filename": "MiniMax_H3_00001_.mp4", "subfolder": "video", "type": "output" }
  ],
  "animated": [true]
}
```

Don't be thrown by the key name — for this node, entries under `"images"` are the video file(s).
`type` will be `"output"` (ComfyUI always writes `SaveVideo` results to the permanent output
folder — there's no built-in "preview only" video node that writes to the auto-cleaned `temp`
folder the way `PreviewImage` does for stills).

## 9. Downloading the bytes

```python
def download_output(base_url: str, filename: str, subfolder: str, type_: str) -> bytes:
    resp = requests.get(
        f"{base_url}/view",
        params={"filename": filename, "subfolder": subfolder, "type": type_},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.content
```

Save straight into your model's `FileField`/`FileSystemStorage`/S3 backend, e.g.:

```python
from django.core.files.base import ContentFile

video_bytes = download_output(COMFYUI_BASE_URL, filename, subfolder, type_)
generation.video_file.save(filename, ContentFile(video_bytes), save=True)
```

## 10. Not leaving the file on the ComfyUI machine after download

**There is no core API endpoint to delete an output file.** `POST /history` only clears the
*history record* (`{"clear": true}` or `{"delete": ["<prompt_id>", ...]}`) — it never touches
anything on disk. This is a known, deliberate gap (no destructive file API), so plan around it
rather than hoping for one:

- **Best option if ComfyUI Desktop runs on the same machine as your Django app/worker (the normal
  desktop-app case):** after the download above succeeds, delete the file yourself via the
  filesystem:

  ```python
  import os

  def delete_comfy_output(comfy_output_root: str, subfolder: str, filename: str):
      path = os.path.join(comfy_output_root, subfolder, filename)
      if os.path.isfile(path):
          os.remove(path)
  ```

  Locate `comfy_output_root` once via **Settings → Server Configuration** in the Desktop app (or
  read it out of `comfy.settings.json`) and put it in Django settings/env — the exact path varies
  by install (`ComfyUI-Installs\<name>\ComfyUI\output` vs. a shared `ComfyUI-Shared\output`,
  depending on how the install was set up). This is the only fully reliable option — everything
  below is a fallback for when the Celery worker *can't* reach ComfyUI's disk directly (e.g.
  ComfyUI runs on a different host from the worker).
- **Clear the history entry too**, once you're done, so `/history` doesn't grow forever (doesn't
  free disk space, just tidies bookkeeping):

  ```python
  requests.post(f"{COMFYUI_BASE_URL}/history", json={"delete": [prompt_id]}, timeout=15)
  ```
- **If the worker truly can't reach the filesystem** (remote ComfyUI instance): there's no
  first-party way to force a delete over HTTP. Community custom nodes like
  `ComfyUI-TempFileDeleter` or `ComfyUI-fileCleaner` can be dropped into the graph to remove a file
  as an explicit workflow step, wired off `SaveVideo`'s pass-through output (ComfyUI's "output
  sockets for save nodes" change means `SaveVideo` now forwards the `VIDEO` it just saved, so you
  can chain a delete-node after it). Treat this as a workaround requiring the custom node to be
  installed on that remote server and tested against your ComfyUI version, not a guarantee.

## 11. Putting it together — a Celery task sketch

(This project actually uses Django-Q2, not Celery — see `generation/tasks.py`'s
`run_generation_job` for the real, current implementation with confirmed node ids for all three
modes. The sketch below is left as generic illustrative reference for the request/response shape;
the concrete task-queue mechanics differ.)

```python
import json
import os
import uuid

import requests
from celery import shared_task
from django.conf import settings
from django.core.files.base import ContentFile

API_WORKFLOW_PATH = os.path.join(
    settings.BASE_DIR, "resources", "workflows_api", "video_minimax_h3_i2v.api.json"
)
SAVE_VIDEO_NODE_ID = "92"


@shared_task(soft_time_limit=1000, time_limit=1030)
def generate_i2v_video(generation_id: int, first_frame_bytes: bytes, first_frame_name: str, prompt_text: str):
    base_url = settings.COMFYUI_BASE_URL
    client_id = str(uuid.uuid4())

    with open(API_WORKFLOW_PATH) as f:
        workflow = json.load(f)

    uploaded_name = comfy_upload(base_url, first_frame_bytes, first_frame_name)
    # node ids below must match whatever your exported API JSON actually uses
    workflow["114"]["inputs"]["image"] = uploaded_name
    workflow["104"]["inputs"]["prompt"] = prompt_text  # if `prompt` isn't wired to a separate primitive

    resp = requests.post(f"{base_url}/prompt", json={"prompt": workflow, "client_id": client_id}, timeout=30)
    resp.raise_for_status()
    prompt_id = resp.json()["prompt_id"]

    record = wait_for_result(base_url, prompt_id, timeout=900)
    out = record["outputs"][SAVE_VIDEO_NODE_ID]["images"][0]

    video_bytes = download_output(base_url, out["filename"], out["subfolder"], out["type"])

    from myapp.models import Generation
    gen = Generation.objects.get(pk=generation_id)
    gen.video_file.save(out["filename"], ContentFile(video_bytes), save=True)

    delete_comfy_output(settings.COMFYUI_OUTPUT_ROOT, out["subfolder"], out["filename"])
    requests.post(f"{base_url}/history", json={"delete": [prompt_id]}, timeout=15)
```

## References

- [ComfyUI docs — Server overview](https://docs.comfy.org/development/comfyui-server/comms_overview)
- [ComfyUI docs — Workflow API format](https://docs.comfy.org/development/api-development/workflow-api-format)
- [ComfyUI docs — SaveVideo node](https://docs.comfy.org/built-in-nodes/SaveVideo)
- [ComfyUI Desktop — Windows install docs](https://docs.comfy.org/installation/desktop/windows)
- [ComfyUI Wiki — Server configuration (host/port defaults)](https://comfyui-wiki.com/en/interface/settings/server-config)
- [Official example scripts (`basic_api_example.py`, `websockets_api_example.py`)](https://github.com/Comfy-Org/ComfyUI/tree/master/script_examples)
- [Runflow — ComfyUI API Endpoints Reference](https://www.runflow.io/blog/comfyui-api-endpoints)
