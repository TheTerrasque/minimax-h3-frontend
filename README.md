# MinimaxH3 Front

A friendly, invite-only web frontend for generating video with the MiniMax H3
ComfyUI workflows (text-to-video, image-to-video, reference-to-video) — see
[`resources/features.md`](resources/features.md) for the full product brief.

Django (API backend) + React (SPA) + Django-Q2 (background job queue),
talking to an existing ComfyUI instance, all behind a single nginx entrypoint
via Docker Compose.

**Status:** the backend (accounts/invites, job model, ComfyUI wiring, and an
optional LLM prompt-assist API — refine + chat, both working end to end) is
built and dry-run verified. There is no frontend UI yet, and no way to
actually create/list a `GenerationJob` over the API yet either — so the only
ways to use this today are Django admin and the API directly. See
[`ARCHITECTURE.md`](ARCHITECTURE.md) for the full design and exactly what's
built vs. deferred, and [`todo.md`](todo.md) for a chronological log of this
project's setup.

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
**http://localhost:8080/**. Since there's no frontend UI yet (see
"Status" above), the API is what you'd actually poke at — browse
**http://localhost:8080/api/schema/swagger-ui/** for interactive,
auto-generated docs of every endpoint (log in via `/accounts/login/` first,
in another tab, since the endpoints themselves require a session).

First-time setup, once the stack is up:

```sh
# create yourself an admin account
docker compose exec backend python manage.py createsuperuser

# then log into /admin/ with it to:
#  - create RenderPreset rows (resolution/duration/steps combos to offer)
#  - create an Invite (its shareable URL is /invite/<token>/) for anyone
#    else who should get an account -- see "Accounts & invites" below
```

## Accounts & invites

There is no open signup. Two ways to get an account:

- **A configured OIDC server is itself the trust gate** — if you set the
  `OIDC_*` variables below, anyone who can successfully log in through that
  identity provider gets an account automatically (you already control who
  has credentials there).
- **Everyone else needs an admin-issued invite** — create an `Invite` from
  `/admin/`, then send the person its `/invite/<token>/` URL. Opening that
  link and completing login is what actually creates their account; the
  token is single-use.

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
| `CSRF_TRUSTED_ORIGINS` | *(empty)* | Comma-separated full origins (scheme+host+port) allowed to make unsafe (POST/etc.) requests. Must include the origin you load the SPA from — e.g. `http://localhost:8080` for the default `frontend` port mapping. |

### ComfyUI

See [`resources/COMFYUI_API_GUIDE.md`](resources/COMFYUI_API_GUIDE.md) for how this integration works.

| Variable | Default | Description |
|---|---|---|
| `COMFYUI_BASE_URL` | `http://host.docker.internal:8000` | Base URL of the ComfyUI instance to submit jobs to. `host.docker.internal` reaches the Docker *host* machine (e.g. ComfyUI Desktop running alongside this stack); point it at any reachable host:port instead if ComfyUI runs elsewhere (as it does in this project's own deployment — a separate GPU machine on the LAN). |
| `COMFYUI_OUTPUT_ROOT` | *(empty)* | Absolute filesystem path to ComfyUI's `output/` folder, if reachable from this machine — used to delete a generated video from ComfyUI's disk right after downloading it, so it doesn't linger there. Leave blank to skip that cleanup step (ComfyUI just keeps every output forever on its own disk). |

### LLM prompt-assist (optional)

Entirely optional — leave all three blank and no AI features (the "AI
refine" button, the prompt chat) are offered at all; the app works fine
without an LLM configured.

| Variable | Default | Description |
|---|---|---|
| `LLM_API_BASE_URL` | *(empty)* | Base URL of any OpenAI-compatible `/chat/completions` endpoint. |
| `LLM_API_KEY` | *(empty)* | API key for that endpoint. |
| `LLM_MODEL` | *(empty)* | Model name to request. |

### OIDC login

Optional — leave `OIDC_CLIENT_ID` blank to run without OIDC configured yet (you can still create accounts manually via `createsuperuser` or invites, once another login method exists).

| Variable | Default | Description |
|---|---|---|
| `OIDC_PROVIDER_NAME` | `OIDC` | Human-readable label shown for this login option. |
| `OIDC_ISSUER_URL` | *(empty)* | The OIDC provider's issuer URL (its discovery document lives at `<issuer>/.well-known/openid-configuration`). |
| `OIDC_CLIENT_ID` | *(empty)* | OAuth client ID registered with that provider. |
| `OIDC_CLIENT_SECRET` | *(empty)* | OAuth client secret. |

### Background jobs

| Variable | Default | Description |
|---|---|---|
| `Q_CLUSTER_WORKERS` | `4` | Number of Django-Q2 worker processes handling generation jobs concurrently. ComfyUI itself only renders one job at a time regardless (it queues submissions on its own end), so this mostly affects how many jobs can be *submitted/polled* concurrently, not render throughput. |

## Useful commands

```sh
# tail logs
docker compose logs -f backend qcluster

# Django shell (inspect/create objects directly)
docker compose exec backend python manage.py shell

# re-run after changing a workflow in resources/workflows/*.json in the
# ComfyUI UI, to regenerate its API-format counterpart (paths are relative
# to /app inside the container, where both scripts/ and resources/ live):
docker compose exec backend python scripts/export_workflow_api.py \
  resources/workflows/video_minimax_h3_t2v.json \
  resources/workflows_api/video_minimax_h3_t2v.api.json

# sweep (resolution, duration) combinations against the real ComfyUI to
# find what's actually viable and how long it takes -- spends real GPU
# time and can crash ComfyUI on oversized combinations by design (that's
# the point); see ARCHITECTURE.md's "Benchmarking render times"
docker compose exec backend python manage.py benchmark_render_times --help
```

## Project structure

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full breakdown. Briefly:

```
backend/       Django API (uv-managed) -- accounts, generation, integrations
frontend/      React SPA (Vite + TS), served by nginx in front of everything
resources/     Product brief, ComfyUI workflows + API guide, prompt-writing guides
docker-compose.yml   The whole stack: db, migrate, backend, qcluster, frontend
```
