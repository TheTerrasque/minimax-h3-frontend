# Setup TODO

Tracking the initial structure/architecture scaffold described in
`ARCHITECTURE.md` (once written) and the approved plan. Docker-based deployment,
Django backend + React frontend, Django-Q2 for background jobs, session-cookie
auth via django-allauth OIDC, single nginx entrypoint.

- [x] Scaffold backend: `uv init`, `pyproject.toml` deps, Django project (`config`) +
      apps (`accounts`, `generation`, `integrations`)
- [x] `accounts` app: custom `User` model, allauth OIDC settings wiring
- [x] `generation` app: models (`RenderPreset`, `GenerationJob`, `ReferenceAsset`),
      admin registration, `GET /api/health/`, `tasks.py` stub
- [x] `integrations/comfyui.py` and `integrations/llm.py` service clients
- [x] `config/settings.py` + `urls.py`: DRF, allauth, django-q2, whitenoise, env vars
      (verified locally with `uv run manage.py check` + `makemigrations` — clean)
- [x] Backend `Dockerfile` (multi-stage, uv) + `entrypoint.sh`
- [x] Invite-only account gating: `Invite` model, `NoSelfSignupAccountAdapter` /
      `InviteGatedSocialAccountAdapter`, `/invite/<token>/` redemption view,
      admin. OIDC logins are auto-accepted (a configured OIDC server is
      itself the trust gate); invite tokens are the fallback gate for any
      other social provider added later. `resources/features.md` updated.
- [x] Frontend: Vite React TS scaffold, `src/api/client.ts`, feature folders,
      `Dockerfile` + `nginx.conf` (SPA + reverse proxy to backend). Builds
      clean locally (`npm run build`).
- [x] `docker-compose.yml` (db/migrate/backend/qcluster/frontend), root
      `.env.example` + generated local `.env`, root `.gitignore` +
      `.dockerignore` (+ `frontend/.dockerignore`)
- [x] `ARCHITECTURE.md` summarizing the setup (repo layout, proxy/auth
      rationale, service graph, app responsibilities, invite-only login,
      request/job flow, deferred list)
- [x] Build & verify: `docker compose build`/`up` all green, `db` healthy,
      `migrate` exited 0, `curl /api/health/` → `{"status":"ok"}`, SPA `index.html`
      served, `qcluster` running with 4 workers, `/admin/login/` → 200. Stack
      left running on http://localhost:8080/.

## Second pass: getting the workflows actually working

- [x] `RenderPreset` gained `steps` + `is_draft` (draft = fast/low-res/low-step
      preview preset, not a separate pipeline)
- [x] Invite-only accounts refined: OIDC logins from any *configured* provider
      are auto-accepted (the admin only wires up servers they already trust);
      `Invite` tokens remain the fallback gate for anything else
- [x] Confirmed live `/object_info` schemas for every node type across all 3
      workflows against this project's actual ComfyUI (`http://gpusun:8188`,
      portable 0.30.0, RTX 3090) — resolved every ambiguity flagged in
      `COMFYUI_API_GUIDE.md` (ref_video_N really is IMAGE/frames-typed;
      ref_image max is 9 not 3; ref_video/ref_audio/ref_video_audio max 3)
- [x] Derived and verified (against real saved workflow JSON) ComfyUI's exact
      UI-format → API-format serialization rules — subgraph flattening,
      widgets_values positional rules, dynamic autogrow list inputs
- [x] Wrote `backend/scripts/export_workflow_api.py`, a from-scratch
      reimplementation of ComfyUI's "Export API" — generated
      `resources/workflows_api/*.api.json` for all 3 modes, cross-checked
      byte-for-byte reproducible
- [x] Wired `generation/tasks.py` fully: real node ids for prompt/width/
      height/steps/duration/seed for all 3 modes, dynamic reference-image
      wiring for i2v (first+last frame) and r2v (up to 9 ref images)
- [x] Dry-run validated all 3 modes end-to-end against the live containerized
      Postgres DB (mocked only `comfyui.upload_media` — no real network/GPU
      cost): correct node wiring, valid JSON, for every distinct code path
- [x] Seeded real `RenderPreset` rows (normal + draft, all 3 modes) via
      migration `0003_seed_render_presets`
- [x] Found and fixed a real nginx+Docker gotcha hit during verification:
      `backend` upstream DNS was cached at nginx startup, causing 502s after
      any backend container recreate — fixed via Docker embedded DNS
      resolver + variable-based `proxy_pass`, verified backend can now be
      force-recreated with zero manual intervention
- [x] Updated `ARCHITECTURE.md` and `resources/COMFYUI_API_GUIDE.md` with all
      of the above

**Status:** workflow wiring is done and dry-run verified; a real live
ComfyUI submission (actual render, real GPU time) has **not** been run yet —
skipped this session because the GPU server was busy. Do that before
trusting this in front of real users.

## Still outstanding (next pass)

- A real live end-to-end ComfyUI test (queue → render → download → cleanup)
- Full DRF viewsets/serializers (presets, jobs, references, queue-estimate,
  prompt-improve) — only `GET /api/health/` exists; no way to trigger a
  `GenerationJob` yet except from a Django shell
- React screens — `src/features/{auth,generate,queue}/` are placeholders
- r2v's `ref_video_N`/`ref_audio_N`/`ref_video_audio_N` (only `ref_image_N`
  is wired)
- i2v's first/last-frame role is inferred from `ReferenceAsset.order`
  (convention, not an explicit field)
- Tests
- TLS / production hardening
