# MinimaxH3 Front

A friendly, invite-only web frontend for generating video with the MiniMax H3
ComfyUI workflows (text-to-video, image-to-video, reference-to-video), plus
experimental image and audio generation (t2i/r2i/t2a/r2a — derived from the
video workflows by extracting a frame or the audio track, see "Updating the
ComfyUI workflows" below) — see
[`resources/features.md`](resources/features.md) for the full product brief.

Django (API backend) + React (SPA) + Django-Q2 (background job queue),
talking to an existing ComfyUI instance, all behind a single nginx entrypoint
via Docker Compose.

**Status:** end-to-end working, including a real render. The backend
(accounts/invites, the full `GenerationJob` create/list/detail/delete +
presets + queue-estimate API, ComfyUI wiring including image *and* audio
references, and an optional LLM prompt-assist API — refine + chat) and the
React frontend (login screen, and a single persistent Generate + Queue
layout — content-type/mode tabs, a compact resolution/length toolbar,
reference thumbnails, an always-visible queue sidebar, and a per-job modal
with download/delete/redo) are both built and verified in a real browser:
log in → queue a job → watch it update live in the sidebar → open its
modal. A real submission has also now actually reached ComfyUI and
rendered a real video, start to finish (see
[`FUNCTION_CHECK.md`](FUNCTION_CHECK.md) for the repeatable procedure this
came out of, and `ARCHITECTURE.md`'s "Verification" section for details).
What's still missing: a proper frontend for the not-yet-built parts (see
`ARCHITECTURE.md`'s "Deferred"), and a real `benchmark_render_times`
sweep — `RenderPreset.estimated_render_seconds` values are still mostly
unbenchmarked guesses, just no longer *unverified guesses about whether
rendering works at all*. See
[`ARCHITECTURE.md`](ARCHITECTURE.md) for the full design and exactly what's
built vs. deferred, [`FUNCTION_CHECK.md`](FUNCTION_CHECK.md) for how to
re-verify any of this yourself, and [`todo.md`](todo.md) for a
chronological log of this project's setup.

## Quick start

Requires Docker + Docker Compose, and a running ComfyUI instance reachable
from wherever Docker runs (see [`resources/COMFYUI_API_GUIDE.md`](resources/COMFYUI_API_GUIDE.md)
for how that API works; ComfyUI itself is **not** part of this stack).

```sh
cp .env.example .env
# edit .env -- at minimum set DJANGO_SECRET_KEY, POSTGRES_PASSWORD, and
# COMFYUI_BASE_URL (see "Configuration" below)

docker compose up -d --build
```

That builds and starts everything (Postgres, migrations, Django, the
Django-Q2 worker, and the nginx-fronted frontend) and serves the app at
**http://localhost:8080/** — the React SPA itself (log in, pick a mode,
queue a video, watch it in the queue). For interactive, auto-generated docs
of every endpoint instead, browse
**http://localhost:8080/api/schema/swagger-ui/** (log in via
`/accounts/login/` first, in another tab, since the endpoints themselves
require a session).

First-time setup, once the stack is up:

```sh
# create yourself an admin account
docker compose exec backend python manage.py createsuperuser

# then log into /admin/ with it to:
#  - review/adjust RenderPreset rows (per-mode megapixels/steps quality
#    tiers, e.g. Draft/Standard/High quality) and their RenderDuration rows
#    (per-tier selectable clip lengths, each with its own estimated render
#    time) -- a starter set per mode is already seeded by migration, with
#    rough unbenchmarked estimated_render_seconds guesses; tune these once
#    you've run manage.py benchmark_render_times for real
#  - create an Invite (its shareable URL is /invite/<token>/) for anyone
#    else who should get an account -- see "Accounts & invites" below
```

## Accounts & invites

There is no open signup. Two ways to get an account:

- **A configured OIDC server is itself the trust gate** — if you set the
  `OIDC_*` variables below, anyone who can successfully log in through that
  identity provider gets an account automatically (you already control who
  has credentials there). Set `OIDC_AUTO_SIGNUP=false` if you'd rather
  require an invite even for OIDC logins (e.g. the IdP isn't a closed set of
  pre-approved people).
- **Everyone else needs an admin-issued invite** — create one from the
  in-app admin page at `/manage` (visible in the nav to any staff user) or
  from Django admin at `/admin/`, then send the person its
  `/invite/<token>/` URL. Opening that link sends them to a local
  email/password signup form (`/accounts/signup/`); completing it is what
  actually creates their account, and the token is single-use (locked to a
  specific email too, if the invite was created with one). `/manage` also
  lists existing invites (active/redeemed/expired) with copy-link and
  revoke actions.

See `ARCHITECTURE.md`'s "Backend apps" section for the full rationale.

## Configuration

All configuration is environment variables, set in `.env` (copy
`.env.example` to start) and consumed by every backend-image-based service
(`backend`, `qcluster`, `migrate`) via Docker Compose's `env_file:`.

### Postgres

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_DB` | `mm_h3` | Database name. |
| `POSTGRES_USER` | `mm_h3` | Database user. |
| `POSTGRES_PASSWORD` | `mm_h3` | Database password — **change this**, the default is only for local/throwaway use. |
| `DB_HOST` | `db` | Database hostname. Leave as `db` (the Compose service name) unless pointing at an external Postgres. |
| `DB_PORT` | `5432` | Database port. |

### Django core

| Variable | Default | Description |
|---|---|---|
| `DJANGO_SECRET_KEY` | *(insecure placeholder)* | Django's cryptographic signing key. Generate a real one: `python -c "import secrets; print(secrets.token_urlsafe(50))"`. Treat it as a secret. |
| `DJANGO_DEBUG` | `false` | Verbose error pages when `true`. Keep `false` except while actively debugging — it leaks internals. |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated hostnames Django will accept requests for. Must include whatever host you actually browse to. |
| `CSRF_TRUSTED_ORIGINS` | *(empty)* | Comma-separated full origins (scheme+host+port) allowed to make unsafe (POST/etc.) requests. Must include **every** origin you actually load the SPA from — e.g. both `http://localhost:8080` and `http://127.0.0.1:8080` if you (or anyone else) might use either; Django checks the browser's `Origin` header against this list exactly, so one hostname doesn't cover another that resolves to the same machine. Missing one here 403s with "CSRF verification failed" on every POST, including login. |
| `DJANGO_SECURE_SSL_REDIRECT` | `false` | Redirect plain HTTP to HTTPS. Only turn on once this is actually served over HTTPS (e.g. behind a Kubernetes Ingress with a real cert) — see [`k8s/README.md`](k8s/README.md). |
| `DJANGO_SESSION_COOKIE_SECURE` | `false` | Only send the session cookie over HTTPS. Same "only once HTTPS is real" caveat as above. |
| `DJANGO_CSRF_COOKIE_SECURE` | `false` | Only send the CSRF cookie over HTTPS. Same caveat. |
| `DJANGO_SECURE_HSTS_SECONDS` | `0` | Seconds browsers should refuse plain HTTP for this host after one HTTPS response. `0` = off. Only set once every subdomain you'd ever serve here is HTTPS-only too, and only after `DJANGO_SECURE_SSL_REDIRECT=true` has been running fine for a while — it's a browser-cached promise that's hard to walk back early. |

### ComfyUI

See [`resources/COMFYUI_API_GUIDE.md`](resources/COMFYUI_API_GUIDE.md) for how this integration works.

| Variable | Default | Description |
|---|---|---|
| `COMFYUI_BASE_URL` | `http://host.docker.internal:8000` | Base URL of the ComfyUI instance to submit jobs to. `host.docker.internal` reaches the Docker *host* machine (e.g. ComfyUI Desktop running alongside this stack); point it at any reachable host:port instead if ComfyUI runs elsewhere (as it does in this project's own deployment — a separate GPU machine on the LAN). |
| `COMFYUI_OUTPUT_ROOT` | *(empty)* | Absolute filesystem path to ComfyUI's `output/` folder, if reachable from this machine — used to delete a generated video from ComfyUI's disk right after downloading it, so it doesn't linger there. Leave blank to skip that cleanup step (ComfyUI just keeps every output forever on its own disk). |

### LLM prompt-assist (optional)

Entirely optional — leave `LLM_API_BASE_URL`/`LLM_MODEL` blank and no AI
features (the "AI refine" button, the prompt chat) are offered at all; the
app works fine without an LLM configured.

| Variable | Default | Description |
|---|---|---|
| `LLM_API_BASE_URL` | *(empty)* | Base URL of any OpenAI-compatible `/chat/completions` endpoint. Required (with `LLM_MODEL`) to enable AI features. |
| `LLM_API_KEY` | *(empty)* | API key for that endpoint, if it needs one. **Optional** — many self-hosted servers (llama.cpp server, LM Studio, text-generation-webui, vLLM in permissive mode) don't require one; leave blank and no `Authorization` header is sent at all. |
| `LLM_MODEL` | *(empty)* | Model name to request. Required (with `LLM_API_BASE_URL`) to enable AI features. |
| `LLM_VISION_ENABLED` | `false` | When `true`, the chat feature sends actual reference image bytes to the LLM as vision content (not just their `<Picture N>` labels). Only turn this on if `LLM_MODEL` is genuinely vision-capable *and* the server actually has vision support loaded — a text/vision-capable model architecture running on a server without its vision-projector loaded will accept the request but the model won't actually see the images (confirmed hitting exactly this with a real test image against this project's own configured model — it works, but doesn't have vision loaded, so this stays `false` here). |

### OIDC login

Optional — leave `OIDC_CLIENT_ID` blank to run without OIDC configured yet (you can still create accounts manually via `createsuperuser` or invites, once another login method exists).

| Variable | Default | Description |
|---|---|---|
| `OIDC_PROVIDER_NAME` | `OIDC` | Human-readable label shown for this login option. |
| `OIDC_ISSUER_URL` | *(empty)* | The OIDC provider's issuer URL (its discovery document lives at `<issuer>/.well-known/openid-configuration`). |
| `OIDC_CLIENT_ID` | *(empty)* | OAuth client ID registered with that provider. |
| `OIDC_CLIENT_SECRET` | *(empty)* | OAuth client secret. |
| `OIDC_AUTO_SIGNUP` | `true` | `true`: completing OIDC login alone creates an account, no invite needed. `false`: OIDC logins need a valid invite too, same as any other new signup. |

### Background jobs

| Variable | Default | Description |
|---|---|---|
| `Q_CLUSTER_WORKERS` | `1` | Number of Django-Q2 worker processes. **Keep at 1** — jobs are processed strictly one at a time, FIFO (see `ARCHITECTURE.md`'s `tasks.py` bullet); raising this would let multiple jobs render in parallel, breaking that guarantee (and ComfyUI itself only renders one job at a time regardless, so there's no throughput to gain). |
| `Q_CLUSTER_TIMEOUT` | `3600` | Hard wall-clock kill (seconds) of the worker process if a single render runs longer than this. Raise it if renders on your hardware/models routinely run long — the old 1200s default was already too tight for a genuine ~20 minute render (see `settings.py`'s `Q_CLUSTER` comment). |

## Useful commands

```sh
# tail logs
docker compose logs -f backend qcluster

# Django shell (inspect/create objects directly)
docker compose exec backend python manage.py shell

# sweep (resolution, duration) combinations against the real ComfyUI to
# find what's actually viable and how long it takes -- spends real GPU
# time and can crash ComfyUI on oversized combinations by design (that's
# the point); see ARCHITECTURE.md's "Benchmarking render times"
docker compose exec backend python manage.py benchmark_render_times --help
```

## Updating the ComfyUI workflows

There are only **three** underlying ComfyUI graphs, all in
[`resources/workflows/`](resources/workflows/):

| File | Mode(s) it drives |
|---|---|
| `video_minimax_h3_t2v.json` | Text-to-video, **and** the experimental text-to-image / text-to-audio modes |
| `video_minimax_h3_i2v.json` | Image-to-video |
| `video_minimax_h3_r2v.json` | Reference-to-video, **and** the experimental reference-to-image / reference-to-audio modes |

The image/audio modes don't have workflows of their own — they submit the
*same* t2v/r2v graph, then [`integrations/media_post.py`](backend/integrations/media_post.py)
uses ffmpeg to pull a still frame or the audio track out of the rendered
video. So editing `video_minimax_h3_t2v.json` also changes what text-to-image
and text-to-audio produce, and likewise for `video_minimax_h3_r2v.json` /
reference-to-image / reference-to-audio.

To change a workflow (swap a model, tweak default sampler settings, rewire
nodes, etc.):

1. Open the relevant `resources/workflows/*.json` file in ComfyUI's own UI
   (drag it in, or File → Open) and make your changes there. Save it back
   over the same file.
2. Regenerate its API-format counterpart — what `POST /prompt` actually
   consumes — by re-running the exporter **on your host machine** (not
   `docker compose exec`: `resources/` isn't bind-mounted into the
   containers, it's baked into the image at build time, so anything written
   inside a running container is invisible to the host and gone the next
   time that container is rebuilt/recreated). Needs the `backend/.venv` set
   up (`uv sync`, from `backend/`) and a **reachable ComfyUI instance** (it
   needs live `/object_info` for any node type not already cached under
   `backend/scripts/object_info_cache/`):

   ```sh
   cd backend
   # COMFYUI_BASE_URL defaults to http://comfyui:8188 if unset -- override it
   # to wherever ComfyUI is reachable *from your host* (not
   # host.docker.internal -- that name only resolves from inside a container)
   COMFYUI_BASE_URL=http://localhost:8188 uv run python scripts/export_workflow_api.py \
     ../resources/workflows/video_minimax_h3_t2v.json \
     ../resources/workflows_api/video_minimax_h3_t2v.api.json
   ```

   Repeat for `_i2v_`/`_r2v_` as needed. This overwrites the matching file
   in `resources/workflows_api/` on your host — that's the file actually
   read at render time (`generation/tasks.py`). If a node's inputs changed
   shape (new params, renamed sockets), delete the stale entry (or the whole
   folder) under `backend/scripts/object_info_cache/` first so the exporter
   re-fetches it.
3. Rebuild and recreate the backend + qcluster containers so the updated
   `.api.json` actually gets baked into their image (a plain `restart`
   does **not** pick it up — same reason as step 2, nothing is bind-mounted):
   `docker compose build backend qcluster && docker compose up -d`.

**One caveat:** `generation/tasks.py` patches a handful of *specific node
IDs* in each `.api.json` after loading it (prompt, resolution, steps,
duration, seed, reference images/audio — see `_T2V_I2V_NODES`/`_R2V_NODES`
near the top of that file). Tweaking existing nodes' settings/values is
always safe — the exporter and `tasks.py` don't care what a node's
*defaults* are, only where the ones it patches live. But if you delete,
replace, or rewire one of those specific nodes (or otherwise change the
graph structure around them), you'll need to update the matching node ID
constants in `generation/tasks.py` to match, or job submission will patch
the wrong node (or crash). See `ARCHITECTURE.md`'s "Getting the workflows
working" section for the full technical rationale (including how the
exporter itself works) and `resources/COMFYUI_API_GUIDE.md` for the ComfyUI
API this all targets.

## Project structure

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full breakdown. Briefly:

```
backend/       Django API (uv-managed) -- accounts, generation, integrations
frontend/      React SPA (Vite + TS), served by nginx in front of everything
resources/     Product brief, ComfyUI workflows + API guide, prompt-writing guides
docker-compose.yml   The whole stack: db, migrate, backend, qcluster, frontend
```
