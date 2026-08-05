# Architecture

Friendly web frontend for the MiniMax H3 ComfyUI video workflows in
[`resources/workflows/`](resources/workflows/) — see
[`resources/features.md`](resources/features.md) for the product brief and
[`resources/COMFYUI_API_GUIDE.md`](resources/COMFYUI_API_GUIDE.md) for how the
backend talks to ComfyUI. This document describes the structure/architecture
scaffold built for it: what exists, why it's shaped this way, and what's
deliberately not built yet.

## Repo layout

```
/
  docker-compose.yml       # the whole stack: db, migrate, backend, qcluster, frontend
  .env.example              # copy to .env
  backend/                 # Django, managed with uv
    Dockerfile               # multi-stage: uv sync -> slim runtime, gunicorn
    entrypoint.sh             # collectstatic, then exec the container's CMD
    scripts/
      export_workflow_api.py    # UI-format workflow -> API-format JSON converter
      object_info_cache/          # cached live /object_info responses it used
    config/                  # settings / urls / wsgi
    accounts/                 # custom User, invite-gated OIDC login
    generation/                # domain models, admin, API views, job task
      management/commands/
        benchmark_render_times.py # sweeps ComfyUI real-run capacity/timing
    integrations/               # comfyui.py + llm.py service clients (no models)
  frontend/                # React (Vite + TS)
    Dockerfile                # multi-stage: node build -> nginx
    nginx.conf                 # serves the SPA + reverse-proxies to backend
    src/
      api/client.ts             # fetch wrapper: session cookie + CSRF header
      features/{auth,generate,queue}/  # placeholders -- screens not built yet
  resources/                # workflows, prompt-writing guides, ComfyUI API guide
    workflows_api/            # API-format JSON generated from workflows/ (see below)
```

## Why a single nginx entrypoint (and why that means no CORS/JWT)

`frontend`'s nginx container is the **only** service that publishes a host
port. It serves the built SPA at `/` and reverse-proxies `/api/`,
`/accounts/`, `/admin/`, `/static/`, `/media/` straight to the `backend`
container (see [`frontend/nginx.conf`](frontend/nginx.conf)). The browser
therefore only ever talks to one origin.

That's what makes plain **Django session-cookie auth** viable for the SPA:
allauth's OIDC callback sets a session cookie, and `src/api/client.ts` just
sends `credentials: 'include'` plus the CSRF header read from the
`csrftoken` cookie on unsafe methods — no CORS configuration, no JWT issuing
or storage. If a bare `manage.py runserver` + separate Vite dev server is
ever used outside Docker, that's the one setup where CORS would start
mattering — out of scope here since this pass is Docker-first.

**Gotcha hit and fixed during setup:** nginx resolves a plain
`proxy_pass http://backend:8000;` hostname once at startup and caches it —
so whenever the `backend` container gets recreated (any rebuild/redeploy),
its Docker-internal IP changes and nginx keeps proxying to the old, dead
address (502s) until nginx itself restarts. Fixed in `nginx.conf` by
resolving `backend` through Docker Compose's embedded DNS
(`resolver 127.0.0.11`) via a variable on every request instead, so
`frontend` no longer needs restarting after a `backend` rebuild — verified
by force-recreating `backend` alone and confirming `frontend` picked up the
new IP with zero manual intervention.

## Docker Compose service graph

- **`db`** — Postgres. Two separate processes (`backend`, `qcluster`) hit the
  same database concurrently, which is why it's Postgres and not SQLite.
- **`migrate`** — one-shot, runs `manage.py migrate`, then exits. `backend`
  and `qcluster` both wait on `db: service_healthy` and
  `migrate: service_completed_successfully`, so migrations always land
  before anything tries to use the schema.
- **`backend`** — Django via gunicorn. Not published to the host — only
  reachable through the nginx proxy and from `qcluster` on the compose
  network. Serves static files itself via whitenoise; serves `/media/`
  (uploaded reference assets + generated videos) directly too for now (see
  "Deferred" below).
- **`qcluster`** — same image as `backend`, running `manage.py qcluster` —
  the Django-Q2 worker process that executes `generation.tasks.run_generation_job`.
- **`frontend`** — nginx; the stack's one published port (`8080:80` by
  default).

`backend`/`qcluster` get `extra_hosts: host.docker.internal:host-gateway` so
a `COMFYUI_BASE_URL` pointed at the Docker *host* itself (e.g. ComfyUI
Desktop on the same machine, default `http://host.docker.internal:8000`)
resolves the same way on Linux Docker as it does out-of-the-box on Docker
Desktop. **ComfyUI itself is not containerized** either way. In this
deployment specifically, ComfyUI runs on a separate networked GPU machine
(`gpusun`, an RTX 3090 box on the LAN, portable ComfyUI 0.30.0 — not
literally "Desktop") reachable at `http://gpusun:8188`, which `.env`'s
`COMFYUI_BASE_URL` is set to; `host.docker.internal` isn't actually used
here but is kept as the documented default for the common same-machine case.

Config is env-driven (`django-environ`), via `.env` → `env_file:` for every
backend-image-based service. See `.env.example` for the full list
(Postgres creds, `DJANGO_SECRET_KEY`/`DEBUG`/`ALLOWED_HOSTS`,
`CSRF_TRUSTED_ORIGINS`, ComfyUI/LLM endpoints, OIDC settings,
Django-Q2 worker count) — this is also how features.md item 12
("Endpoints for comfyui and llm should be configured in django settings")
is satisfied.

## Backend apps

**`accounts`** — custom `User(AbstractUser)` (`AUTH_USER_MODEL`), plus
**invite-only login** (features.md: "no random people on the page, just the
ones I invite"):

- Local email/password self-signup is disabled outright
  (`NoSelfSignupAccountAdapter.is_open_for_signup` → `False`).
- OIDC login is **auto-accepted** for any configured OIDC provider app
  (`InviteGatedSocialAccountAdapter`, `AUTO_ACCEPTED_PROVIDER_IDS`) — an
  admin only ever wires up an OIDC server they already trust to authenticate
  the right people, so a successful login there already proves the person
  was let in on that server's side.
- Anything else (a more open social provider added later, or local accounts
  re-enabled for one person) is gated by `Invite`: an admin-issued, one-time
  token redeemed at `/invite/<token>/`, which stashes the token in the
  session; `is_open_for_signup` then requires it, and it's marked redeemed
  in `save_user` once signup actually completes. Currently unused by the
  OIDC path (which auto-accepts) but is the mechanism the moment a
  non-trusted provider exists — see `accounts/adapters.py`.
- Invites are created/managed from Django admin (`accounts/admin.py`).

**`generation`** — the domain:

- `RenderPreset` — admin-editable `(mode, width, height, duration_seconds,
  steps) → estimated_render_seconds`, backing features.md item 4. A row with
  `is_draft=True` is a fast/low-res/low-step "preview this prompt" pass
  (seeded presets: ~608x320, 3s, 8 steps) rather than a separate
  model/pipeline — draft mode is just another preset. Also leaves room for
  the later image/audio-only modes (item 6 — same pipeline, tiny-res/5-frame
  presets) without new models. Seeded with a starter set of presets per mode
  in migration `generation/migrations/0003_seed_render_presets.py`
  (`estimated_render_seconds` values there are rough unbenchmarked guesses,
  meant to be tuned via admin once real render times are observed).
- `GenerationJob` — one user's request: mode, raw/improved prompt, chosen
  preset (estimate snapshotted at creation so later preset edits don't
  retroactively change an ETA already shown), status, ComfyUI prompt id,
  output `video_file`, timestamps.
- `ReferenceAsset` — image/video/audio attachments on a job, with a computed
  `label` (`"Picture 1"`, `"Video 1"`, `"Audio 1"`) matching the
  `<Picture N>`/`<Video N>`/`<Audio N>` convention in
  `resources/prompt instructions/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md`, so
  the frontend can offer "insert reference" buttons producing tokens the LLM
  prompt-assist step already understands.
- `PromptChatSession`/`PromptChatMessage` — a persisted, multi-turn
  conversation helping a user draft/refine a prompt (features.md: "a way to
  interactively chat with the llm"). Only ever created when
  `settings.LLM_ENABLED`; `resulting_job` optionally links a finished
  session to whatever `GenerationJob` its prompt was actually used for.
  Persisted (rather than kept client-side) so a session survives a page
  refresh and gives an audit trail of LLM usage.
- `BenchmarkResult` — one `(mode, width, height, duration_seconds, steps) →
  status/render_seconds` data point from `manage.py benchmark_render_times`
  (see "Benchmarking render times" below). Deliberately separate from
  `RenderPreset`: this holds the full raw sweep (including combinations that
  failed), not the small curated set actually offered to users.
- `queue.py` — `estimated_seconds_ahead()`: sum of `estimated_seconds` over
  every job still queued/running, **system-wide** — features.md item 5 wants
  a combined ETA without exposing other users' individual jobs, so this is
  the only cross-user read the API is meant to expose.
- `tasks.py` — `run_generation_job(job_id)`, the Django-Q2 entry point: mark
  running → build the mode's API-format workflow via `build_api_workflow()`
  (prompt, resolution, steps, duration, seed, reference images) → ComfyUI
  upload/submit/poll/download → save `video_file` → mark done/failed →
  delete the ComfyUI-side file + clear its history entry. No LLM call
  happens here — prompt refinement is an explicit pre-job user action (see
  below), and `job.improved_prompt` already holds its result (or is blank)
  by the time this task runs. `build_api_workflow()` is a pure function
  (given already-uploaded ComfyUI filenames, no DB/network I/O) so it's
  shared between this and the benchmark command without needing fake DB
  rows. Fully wired for all three modes and dry-run validated against the
  live containerized DB (mocked ComfyUI upload calls, no real network/GPU
  cost) — see "Getting the workflows working" below.
- `api.py`/`urls.py` — `GET /api/health/`, `GET /api/config/` (feature
  flags, currently just `llm_enabled`), `POST /api/prompt/refine/`
  (one-shot "AI refine"), and the chat endpoints
  (`POST /api/prompt/chat/sessions/`, `GET .../sessions/{id}/`,
  `POST .../sessions/{id}/messages/`). Full CRUD for presets/jobs/references
  still doesn't exist — see "Deferred" below.

**`integrations`** (plain packages, no models/migrations):

- `comfyui.py` — real implementation of upload / queue / poll / download /
  extract-output / delete-output / clear-history / error-status-detection /
  reachability-check, following `resources/COMFYUI_API_GUIDE.md` §5–§10.
  Doesn't know about any specific workflow's node ids — callers own the
  API-format JSON. `check_for_error()` and `is_alive()` exist specifically
  so a prompt that ComfyUI accepted but then failed server-side (e.g. an
  OOM it caught itself) surfaces as a clear error instead of a confusing
  `KeyError` reaching into an unpopulated `outputs` dict, and so the
  benchmark command can tell a slow-but-alive server apart from a crashed one.
- `llm.py` — OpenAI-compatible chat-completion client; loads the right guide
  from `resources/prompt instructions/` per mode (base guide for t2v/i2v, the
  reference guide for r2v) as system context. `is_configured()` backs
  `settings.LLM_ENABLED`. Two entry points: `improve_prompt()` (one-shot
  rewrite) and `chat_reply()` (multi-turn, given the full prior history).

## LLM integration: entirely optional, two features when present

Per features.md: **no LLM configured → no AI UI at all.**
`settings.LLM_ENABLED` (`config/settings.py`) is `True` only when
`LLM_API_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL` are all set; the frontend is
meant to check `GET /api/config/`'s `llm_enabled` once at boot and hide the
refine button/chat entirely when it's `False`. Even hit directly, the
refine/chat endpoints fail cleanly with `503` rather than crashing when
unconfigured (verified). There is deliberately no automatic/implicit LLM
call anywhere in the job-execution path — refinement only happens when a
user explicitly asks for it:

- **"AI refine" button** → `POST /api/prompt/refine/` → `llm.improve_prompt()`
  → one-shot rewrite, same behavior as before this pass.
- **Interactive chat** → `POST /api/prompt/chat/sessions/` to start,
  `POST .../messages/` per turn → `llm.chat_reply()` with the full
  conversation history, system-prompted to converse and help draft a
  prompt rather than immediately rewriting one. Persisted via
  `PromptChatSession`/`PromptChatMessage` (see above) — the "persisted vs.
  stateless" call made for this pass.

Both were dry-run tested against the live containerized DB and Postgres
(mocked only the outbound LLM HTTP call): config correctly reports
`llm_enabled: false` when unset; refine/chat correctly `503` when unset;
with a mocked LLM configured, refine returns a rewritten prompt, a chat
session round-trips a user message → assistant reply → full history
fetch, and cross-user access to another user's session correctly `404`s
rather than leaking it.

## Benchmarking render times

`RenderPreset.estimated_render_seconds` is currently a rough, unbenchmarked
guess (see its seed migration). `manage.py benchmark_render_times` sweeps
`(resolution, duration)` combinations per mode against the **real** ComfyUI
instance and records what happened into `BenchmarkResult` — the matrix you'd
actually curate `RenderPreset` rows from. It:

- Submits real jobs via the same `build_api_workflow()` `tasks.py` uses (no
  fake DB rows needed, since that function is pure).
- Measures wall-clock render time on success.
- Distinguishes a **clean server-side failure** (ComfyUI caught an error,
  e.g. OOM, and reported it via `/history` — `comfyui.check_for_error()`)
  from ComfyUI's **whole process dying**, which the user has observed happen
  in practice on large combinations, not just a graceful per-job error. The
  former records `oom_error` and moves to the next combination; the latter
  (detected via `is_alive()` before each attempt, and via a connection
  error mid-request) records `crashed` and **stops the command immediately**
  rather than continuing to hammer a dead server.
- Is resumable: already-recorded combinations are skipped on a re-run
  (`--retest` to force) — so after a crash, restart ComfyUI and re-run the
  same command to pick up where it left off.
- Sorts combinations cheapest-first (by `width × height × duration`) and
  defaults to a small built-in spread; both are overridable via
  `--resolution`/`--duration`/`--modes`/`--steps`.

**This is never run automatically by anything in this project** — it spends
real GPU time and, per the above, can crash the ComfyUI process. It's a
tool to run deliberately; its `--help` output and argument parsing were
verified this pass, but it has **not** been run for real yet (same reason
as the live ComfyUI test below — the GPU server was busy).

## Getting the workflows working: UI-format → API-format

`resources/workflows/*.json` are ComfyUI's **UI/editor format** — `POST
/prompt` needs the flat **API format** instead (see
`resources/COMFYUI_API_GUIDE.md` §3). Rather than requiring a manual
"Export (API)" click in ComfyUI's UI for each of the 3 workflows,
`backend/scripts/export_workflow_api.py` reimplements that export
mechanically: it reads a UI-format workflow plus live `/object_info`
responses from a running ComfyUI (cached in `scripts/object_info_cache/`),
and reconstructs the exact API-format JSON, including subgraph flattening
(t2v/i2v wrap their sampler chain in a "Image to Video (MiniMax H3)"
subgraph) and ComfyUI's dynamic reference-list inputs (r2v's
`ref_images.ref_image_N`, up to 9). The script's docstring documents each
serialization rule and exactly how it was verified against real saved
workflow JSON + live object_info from the actual ComfyUI instance this
project targets (`gpusun:8188`) — nothing about it is guessed. Its output
was cross-checked byte-for-byte reproducible and passed a full dry run
(patch every mode, confirm valid/well-wired JSON) against the live stack.

Run it again any time a workflow in `resources/workflows/` is edited in the
ComfyUI UI, to regenerate its `resources/workflows_api/*.api.json`
counterpart:

```
cd backend
uv run python scripts/export_workflow_api.py \
  ../resources/workflows/video_minimax_h3_t2v.json \
  ../resources/workflows_api/video_minimax_h3_t2v.api.json
```

`generation/tasks.py` then patches the resulting JSON's known node ids
directly (documented in `_T2V_I2V_NODES`/`_R2V_NODES` in that file) —
prompt, width/height (bypassing the workflow's own `ResolutionSelector`
node, which only accepts an aspect-ratio preset rather than arbitrary
dimensions), steps, duration (feeding the workflow's existing
seconds→frame-count snapping math rather than reimplementing it), a fresh
random seed per job, and reference images (dynamically adding/wiring
`LoadImage` nodes per `ReferenceAsset`, replacing the template's example
wiring). **Not yet wired**: r2v's `ref_video_N`/`ref_audio_N`/
`ref_video_audio_N` (needs `LoadVideo`/`LoadAudio` node schemas fetched and
wired the same way `ref_image_N` already is — same pattern, just not done
yet); i2v's first/last-frame assignment currently uses a plain convention
(reference `order=0` → first frame, `order=1` → last frame) since
`ReferenceAsset` has no explicit role field yet.

## Request/job flow (once the DRF/React pieces land)

1. Browser hits `/invite/<token>/` (first-time users) or the OIDC login
   directly (existing users / trusted-provider auto-accept) → session
   cookie set on success.
2. SPA calls `GET /api/config/` to decide whether to show any AI UI at all.
3. SPA calls `GET /api/presets/` to show mode/resolution/duration options
   with estimated render time, and `GET /api/queue-estimate/` before the
   user confirms — both not yet implemented (see below).
4. While drafting a prompt, the user may click "AI refine"
   (`POST /api/prompt/refine/`, one-shot) or open the chat
   (`POST /api/prompt/chat/sessions/` + `.../messages/`, multi-turn) — both
   already implemented, just not wired to any UI yet.
5. `POST /api/jobs/` creates a `GenerationJob` (raw + optionally the
   AI-refined prompt from step 4), snapshotting `estimated_seconds` from the
   chosen `RenderPreset`, and enqueues `generation.tasks.run_generation_job`
   via Django-Q2 — not yet implemented, see "Deferred" below.
6. The task runs the ComfyUI round trip (§ above) and updates job status;
   the SPA polls `GET /api/jobs/{id}/` for progress.

## Verification done so far vs. still outstanding

`generation/tasks.py` has been dry-run tested end-to-end for all three modes
against the live containerized Postgres DB: real `RenderPreset`/
`GenerationJob`/`ReferenceAsset` rows, `_build_workflow_for_job()` (which
calls the pure `build_api_workflow()`) run for real, only
`integrations.comfyui.upload_media` mocked (so no network/GPU cost) —
confirmed correct node wiring and valid, well-formed JSON for t2v (plain
text), i2v (first *and* last frame, which requires dynamically adding a
node the template doesn't have), and r2v (three dynamically-added reference
images replacing the template's example wiring, prompt correctly landing on
its separate `PrimitiveStringMultiline` node) — re-confirmed after the
`build_api_workflow()` extraction refactor. Also dry-run tested: `/api/config/`,
`/api/prompt/refine/`, and the full chat session flow (see "LLM integration"
above). **Not yet done**: an actual live submission to ComfyUI (queue →
render → download) or a real `benchmark_render_times` run — both cost real
GPU time, deliberately not spent without asking first; do that before
trusting this in front of real users.

## Deferred to the next pass

Intentionally not built in this pass:

- **A real live ComfyUI test, and a real `benchmark_render_times` run** —
  see above; both deliberately not done without asking first (real GPU
  time), and the GPU server was busy this pass.
- **Full DRF viewsets/serializers** for presets, jobs, references, and
  queue-estimate — `config`/`prompt/refine`/`prompt/chat/*` exist (see
  "LLM integration" above), but there's still no way to actually trigger a
  `GenerationJob` except from a Django shell.
- **React screens** — `src/features/{auth,generate,queue}/` are placeholder
  files marking where they go; no UI is built yet, including for the LLM
  refine/chat endpoints above.
- **r2v's `ref_video_N`/`ref_audio_N`/`ref_video_audio_N`** — only
  `ref_image_N` is wired; see "Getting the workflows working" above.
- **i2v's first/last-frame role** — inferred from `ReferenceAsset.order`
  (0 = first, 1 = last) rather than an explicit field; fine for now, worth
  revisiting once the frontend needs to let a user pick which is which.
- **Tests.**
- **TLS / production hardening** — the compose stack is plain HTTP on
  `localhost:8080`; no cert/reverse-TLS-termination is set up.
- **Media/static serving at scale** — `/media/` is served directly by
  Django for now; moving it to nginx-volume serving or object storage is a
  follow-up once upload volume matters.
