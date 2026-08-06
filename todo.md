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

## Third pass: benchmark matrix + optional LLM (refine button + chat)

- [x] `BenchmarkResult` model (separate from `RenderPreset` — raw sweep data,
      not curated user-facing offerings) + `manage.py benchmark_render_times`:
      sweeps (resolution, duration) per mode against real ComfyUI, records
      render time or failure, tells a clean per-job OOM apart from ComfyUI's
      whole process crashing (observed in practice) and stops immediately on
      the latter rather than hammering a dead server, resumable
      (already-tested combos skipped unless `--retest`). **Not run for
      real yet** — same GPU-time caution as the live test above; verified
      via `--help`/argparse only.
- [x] `integrations/comfyui.py` gained `check_for_error()` (a prompt that
      reached `/history` but failed server-side, e.g. OOM, now surfaces as a
      clear error instead of a confusing `KeyError`) and `is_alive()`
      (reachability check) — used by both the benchmark command and now also
      `generation/tasks.py`'s real job path (same bug existed there too).
- [x] `settings.LLM_ENABLED` (true only when `LLM_API_BASE_URL`/`LLM_API_KEY`/
      `LLM_MODEL` are all set) + `GET /api/config/` exposing it, so the
      frontend can hide all AI UI when no LLM is configured. Endpoints
      degrade gracefully (503, not a crash) even if hit directly while unset.
- [x] Removed the automatic LLM call from `run_generation_job` — refinement
      is now purely an explicit pre-job action (see below), never implicit.
- [x] `POST /api/prompt/refine/` (one-shot "AI refine" button, existing
      `llm.improve_prompt()` wired to an endpoint).
- [x] Interactive chat, persisted (per the "persisted vs. stateless"
      decision): `PromptChatSession`/`PromptChatMessage` models,
      `llm.chat_reply()` (multi-turn, conversational system prompt distinct
      from the one-shot rewrite prompt), `POST /api/prompt/chat/sessions/`,
      `GET .../sessions/{id}/`, `POST .../sessions/{id}/messages/`.
- [x] `generation/tasks.py` refactored: `build_api_workflow()` pulled out as
      a pure function (given already-uploaded filenames, no DB/network I/O)
      so both `run_generation_job` and the benchmark command share the exact
      same patching logic — re-verified against real DB after the refactor.
- [x] Dry-run tested against the live containerized DB + Postgres (only the
      outbound LLM HTTP call and ComfyUI upload mocked): config flag
      correct in both states, refine/chat correctly 503 when unset, full
      chat round-trip (create session → message → assistant reply → history
      fetch) works when mocked-enabled, cross-user session access correctly
      404s instead of leaking, refactored workflow-builder still produces
      valid wiring for all 3 modes.
- [x] Updated `ARCHITECTURE.md` with all of the above.

## Fourth pass: docs pass + auto-generated API documentation

- [x] Added a root `README.md`: quick start, accounts/invites explainer,
      full config option reference table, verified command examples
      (caught and fixed a wrong relative-path example in the process),
      project structure pointer.
- [x] Fixed doc drift in `ARCHITECTURE.md` (a duplicated bullet, a stale
      reference to the refactored-away `_patch_workflow`) and added the
      `check_for_error()`/`is_alive()` patterns to
      `resources/COMFYUI_API_GUIDE.md` as general guidance.
- [x] Added `drf-spectacular`: auto-generated OpenAPI schema (`/api/schema/`)
      + Swagger UI (`/api/schema/swagger-ui/`) + Redoc
      (`/api/schema/redoc/`), all browsable without login. Every view in
      `generation/api.py` now carries an `@extend_schema` with an inline
      documentation-only `Serializer` (the views themselves still validate
      by hand, per that file's minimal-scope decision) so the generated
      docs actually describe request/response shapes instead of being
      empty. Verified: `manage.py spectacular` generates with zero
      warnings, and schema/Swagger UI both serve correctly through the
      full stack (nginx → backend) — chosen over a hand-written reference
      specifically so it can't drift as jobs/presets/references get added.

## Fifth pass: the missing job-creation API + the React frontend

- [x] `GET /api/presets/` (optional `?mode=`), `GET /api/queue-estimate/`
      (preset_id now optional — omitted shows just the system-wide backlog,
      used by the Queue screen; provided adds that preset's own render time,
      used while drafting a job), `GET|POST /api/jobs/`, `GET /api/jobs/{id}/`.
      `POST /api/jobs/` creates the `GenerationJob` and any `ReferenceAsset`
      rows in one atomic multipart request and enqueues
      `generation.tasks.run_generation_job` via Django-Q2 immediately —
      simpler than the originally-sketched separate
      `POST /api/jobs/{id}/references/` step, since reference files are
      staged client-side before submission anyway (same assumption
      `reference_labels` on `/api/prompt/refine/` already made). Rejects
      video/audio references and over-mode-limit image counts with a 400
      rather than accepting and silently ignoring them at render time.
- [x] `accounts/api.py`: `GET /api/me/` (`AllowAny`, `{authenticated: false}`
      instead of 403 when logged out) so the SPA can decide at boot whether
      to show the app or the login screen.
- [x] `GET /api/config/` gained `oidc_enabled`/`oidc_login_url`/
      `oidc_provider_name` so the login screen knows whether to render an
      OIDC button and where it points, without hardcoding the provider id.
- [x] Found and fixed a real bug while testing the login flow end to end:
      Django/allauth's default post-login/-logout redirect
      (`/accounts/profile/`) 404s here — nothing serves that route in an
      API+SPA project. Added `LOGIN_REDIRECT_URL`/`ACCOUNT_LOGOUT_REDIRECT_URL
      = "/"` in `config/settings.py`.
- [x] Verified: `manage.py check` clean, `manage.py spectacular
      --fail-on-warn` clean after every endpoint addition.
- [x] React frontend built out for real: `react-router-dom` +
      `@tanstack/react-query` added; `src/api/{types,queries}.ts` (typed
      wrappers + polling/cache-invalidation for every endpoint above);
      `App.tsx` as the auth-gated shell (nav + routes); `features/auth`
      (login screen — OIDC button when configured, always a link to
      `/accounts/login/` for admin-created accounts); `features/generate`
      (mode picker, preset picker showing ETA, i2v first/last-frame slots,
      r2v dynamic reference list with per-image `<Picture N>` insert-token
      buttons, prompt box with AI-refine + persisted chat side panel when
      `llm_enabled`, pre-submit queue ETA, submit → `/jobs`); `features/queue`
      (job list polling while any job is active, system backlog, inline
      video player on completion). `App.css`/`index.css` rewritten from
      Vite's default hero/counter demo styles to real app styles (still the
      same light/dark CSS-variable tokens).
- [x] Verified end-to-end in a browser (Playwright, against the project's
      real docker-compose stack rebuilt with this pass's code): logged-out
      → login screen (no OIDC button, since none is configured in this
      dev env) → `/accounts/login/` with a manually-created superuser →
      redirected to `/` (post-fix) → Generate screen → presets load and
      preselect → queued a real job → landed on `/jobs` with the job
      visible and status-polling. Also drove r2v's dynamic add/insert-token
      /remove reference flow and i2v's first/last-frame slots directly.
      Zero browser console errors, zero unexpected-status API requests
      across all of the above. (The queued jobs then failed, as expected —
      this dev sandbox has no reachable ComfyUI at `COMFYUI_BASE_URL`;
      that's the existing untested-live-ComfyUI gap noted below, not a
      frontend bug.) Test-only `GenerationJob` rows were deleted afterward;
      the ad hoc `testadmin` superuser was left in place for further manual
      testing.
- [x] Full docs pass reflecting the now-built frontend: `ARCHITECTURE.md`
      gained a dedicated "Frontend" section (structure, auth gating,
      per-screen behavior), its repo layout diagram/request-flow/
      verification/deferred sections were brought current (removed the
      now-stale "React screens not built" framing throughout, added the
      login-redirect-fix and browser-test writeups); `README.md`'s Status,
      Quick start, and first-time-setup sections updated to describe the
      app as actually usable rather than API-only.

## Sixth pass: serialized FIFO rendering, simplified job statuses, per-job ETA

- [x] `GenerationJob.Status` shrunk from 5 values (`queued`/`running`/
      `completed`/`failed`/`cancelled`) to 3 (`queued`/`processing`/`done`)
      — `done` covers both success and failure now, told apart by
      `video_file`/`error_message` rather than a separate terminal status.
      `cancelled` is dropped entirely (it was already dead code — nothing
      set it). Migration `0005_alter_generationjob_status` (no data
      migration needed — confirmed zero existing `GenerationJob` rows first).
- [x] `generation/tasks.py` rewritten around a `process_queue()` Django-Q2
      entry point that works through the *entire* FIFO queue itself
      (`_claim_next_job()` + `_execute_job()` in a loop) rather than being a
      per-job task — found while investigating that django-q2's ORM broker
      (`django_q/brokers/orm.py`) has no `ORDER BY` in its dequeue query, so
      task pickup order was never actually guaranteed FIFO the way the old
      one-task-per-job design implicitly assumed. FIFO + serialization are
      now enforced explicitly: `_claim_next_job()` claims the oldest
      `queued` job via `order_by("created_at", "id")` + a DB row lock, and
      `Q_CLUSTER_WORKERS` dropped to `1` (settings.py/.env/.env.example)
      so two different jobs can never run in parallel regardless of claim
      order (the row lock alone only prevents double-claiming the *same*
      job). `_execute_job()` no longer re-raises on failure, so one failed
      job doesn't abort the rest of the queue.
- [x] `generation/queue.py` gained `expected_finish_times()`: a per-job
      expected-finish timestamp computed by walking every active job
      system-wide in FIFO order — a `processing` job's expected finish is
      `started_at + estimated_seconds`; every job behind it chains off the
      previous job's expected finish. Wired into `GET /api/jobs/`,
      `GET /api/jobs/{id}/`, and the `POST /api/jobs/` create response as
      `expected_finish_time` (`null` once `done`). Same "system-wide
      computation, per-user-scoped exposure" pattern as the existing
      aggregate ETA — never leaks another user's job identity.
- [x] Frontend: `JobStatus` narrowed to the 3 new values;
      `QueueScreen.tsx` derives a "Failed" badge display (red, via
      `didJobFail()`) from `status === "done" && !video_url` rather than a
      dedicated backend status, and shows each active job's
      `expected_finish_time` inline.
- [x] Verified for real against the live containerized stack (see
      `ARCHITECTURE.md`'s "Verification" section): queued 4 jobs back to
      back, confirmed from `started_at`/`finished_at` timestamps that they
      ran in strict creation order with zero overlap, and confirmed
      `expected_finish_time`'s cumulative math matched exactly. Rebuilt
      backend/qcluster/frontend, re-ran `manage.py check` +
      `spectacular --fail-on-warn` (both clean), browser-tested the Queue
      screen's new badges (screenshot-verified, zero console errors).
      Test job rows cleaned up afterward.
- [x] Updated `ARCHITECTURE.md` (`GenerationJob`/`queue.py`/`tasks.py`/
      `api.py` bullets, `qcluster` service bullet, Frontend section's
      QueueScreen writeup, Request/job flow, Verification, Deferred) and
      `README.md`'s `Q_CLUSTER_WORKERS` row.

## Fifth pass: FUNCTION_CHECK.md + running it for real

- [x] Wrote `FUNCTION_CHECK.md`: a repeatable checklist covering API-level
      checks (curl/Django test client, no browser needed) and a
      browser-driven frontend checklist, plus what's an expected
      non-failure (ComfyUI unreachable, `expected_finish_time: null` races)
      vs. actually worth investigating.
- [x] Ran the full API-level section for real: meta endpoints, auth gating,
      presets, queue-estimate math, prompt-assist 503-gating, job creation
      across all 3 modes with reference-cap enforcement, cross-user
      isolation, and re-verified FIFO/zero-overlap ordering on 4 freshly
      queued jobs. All passed.
- [x] **Found and fixed a real bug**: `r2v`'s non-draft `RenderPreset` was
      missing from the database (present in the seed migration, just not
      in the actual table — something deleted it after seeding) — would
      have broken the real Generate screen's preset auto-select for r2v.
      Restored it to match the migration's intended row.
- [x] Ran the browser-driven section for real (Playwright, installed into a
      scratch Node project since neither `chromium-cli` nor a project
      `run` skill existed yet): logged-out login screen, login redirect
      (confirmed the `/accounts/profile/` 404 fix still holds), nav, mode
      switching, i2v's first/last-frame slots, r2v's full add → insert
      `<Picture N>` token → remove flow (screenshot-verified after two
      test-script selector mistakes gave false negatives), job submission,
      Queue screen's Failed badge. Zero real bugs found; zero browser
      console errors.
- [x] **Found and fixed a second real bug**: `.env`'s `COMFYUI_BASE_URL`
      used the `gpusun` hostname, which resolves fine from the Docker host
      (Windows NetBIOS/mDNS) but NOT from inside the backend/qcluster
      containers (Docker's embedded DNS doesn't do that resolution) —
      every job was failing at the connection step. Fixed to the machine's
      IP address directly.
- [x] With that fixed and explicit go-ahead for the GPU time: queued one
      real draft t2v job against the actual `gpusun` RTX 3090 and watched
      it go queued → processing → done for real. Got back a genuine
      ~126KB valid `.mp4`, empty `error_message`, and confirmed ComfyUI's
      own `/history` was cleared afterward. Took ~71s against that
      preset's `estimated_render_seconds: 30` guess -- one real data
      point, not yet enough to justify overwriting the seed value (that's
      `benchmark_render_times`'s job). **This is the first real ComfyUI
      render this project has ever completed.**
- [x] Updated `ARCHITECTURE.md` (Docker Compose service graph's ComfyUI
      note, Verification, Deferred, Request/job flow), `README.md`'s
      status line, and `.env.example`'s `COMFYUI_BASE_URL` comment (the
      LAN-hostname-resolution gotcha) with all of the above.
- [x] Cleaned up every throwaway test user/job/preset created during this
      pass; left the two real fixes (restored preset, IP-based
      `COMFYUI_BASE_URL`) in place.

## Seventh pass: resolution/length UI redesign + media 404 fix

Triggered by direct user feedback after the pass above: the resolution/
length picker (a flat list of preset cards) took up too much space for too
few options, and generated videos 404'd on the Queue screen.

- [x] **Found and fixed a real bug**: `config/urls.py` never mounted a URL
      pattern for `MEDIA_URL` at all — not a `DEBUG`-only gap, since
      `django.contrib.staticfiles` only ever auto-serves `STATIC_ROOT`, not
      `MEDIA_ROOT`. Every job's `video_url` was dead on arrival. Fixed with
      an unconditional `re_path(...serve_static...)` mount; verified via
      `curl` through nginx (404 → 200 with correct bytes).
- [x] Redesigned the resolution/duration model per user spec, given
      iteratively: `RenderPreset` is now a megapixels/steps quality tier
      (label, e.g. "Draft"/"Standard"/"High quality", `is_draft` flag)
      instead of a fixed `(width, height, duration, estimate)` card; a new
      `RenderDuration` model FKs to it, holding each tier's selectable clip
      lengths with their own curated `estimated_render_seconds`; a new
      `generation/resolution.py` computes `width`/`height` from
      `megapixels` + a separate `aspect_ratio` choice (ratio doesn't affect
      render time, so kept out of the tier). `GenerationJob` snapshots
      `megapixels`/`aspect_ratio`/`width`/`height`/`duration_seconds`
      directly at creation time (not just reachable via FK), per explicit
      user request, so later admin edits to the catalog never retroactively
      change a value already shown to a user.
- [x] Migrated the above: `0006` (schema changes, temp defaults),
      `0007` (standalone data-cleanup `RunPython` deleting pre-existing
      `GenerationJob` rows with no `duration`), `0008` (drop the temp
      defaults, make `duration` `NOT NULL`), `0009` (reseed the full
      megapixels/duration catalog for all 3 modes). Split `0007` out of
      what would otherwise have been part of `0008` after hitting a real
      Postgres error combining them: `cannot ALTER TABLE ... because it has
      pending trigger events` (a `DELETE` with pending FK-cascade triggers
      and a later `ALTER TABLE` can't share one transaction; splitting into
      separate migration files fixed it since each file is its own
      transaction).
- [x] Updated `api.py`/`urls.py`/`admin.py`/`tasks.py` for the new shape:
      `/api/config/` now returns `aspect_ratios`/`default_aspect_ratio`;
      `/api/presets/` nests each tier's `durations`; `/api/queue-estimate/`
      takes `?duration_id=` (was `?preset_id=`); `POST /api/jobs/` takes
      `duration_id` + `aspect_ratio` (was `preset_id`) and computes
      `width`/`height` server-side via `resolution.compute_resolution()`.
- [x] Frontend: replaced the preset-card list with a compact three-control
      row — a quality (megapixels) dropdown, an aspect-ratio dropdown, and
      a length slider reconciling against the selected tier's available
      durations. `frontend/src/api/types.ts`/`queries.ts` updated to match
      the new API shapes (`durationId`/`aspectRatio` instead of
      `presetId`).
- [x] Verified at three levels: direct Django-ORM/API dry-run testing (new
      job-creation path + `compute_resolution()` output sanity-checked
      across several megapixel/ratio combos, all correctly rounded to a
      multiple of 32); a synthetic file-serving test confirming the media
      404 fix; a real Playwright browser pass against the rebuilt stack
      confirming the dropdowns and slider all drive real React state and
      update the displayed estimate correctly (caught and fixed a
      test-script false negative along the way — synthetic `dispatchEvent`
      on the range input doesn't trigger React's `onChange`; real keyboard
      interaction does).
- [x] Updated `ARCHITECTURE.md` (generation domain, api.py/urls.py, Docker
      Compose service graph, frontend section, Request/job flow,
      Verification), `README.md`'s admin first-time-setup note, and
      `FUNCTION_CHECK.md`'s API/frontend checklists (`preset_id` →
      `duration_id`/`aspect_ratio` throughout).

## Eighth pass: frontend redesign (`frontend fixes.txt`) + audio references + job delete

User feedback listed in a `frontend fixes.txt` file at the repo root
(content-type/mode tabs, a subtler resolution/length toolbar, reference
thumbnails + audio support, a more prominent prompt, and a right-side queue
with a per-job modal instead of a separate jobs page), plus a separate ask
to fully wire audio references (not just a UI placeholder).

- [x] **Audio references wired end to end**: live-verified against the
      actual ComfyUI instance first (`/object_info/LoadAudio` — only input
      is `audio`, same shape as `LoadImage.inputs.image`;
      `/object_info/MiniMaxH3ReferenceToVideo` — `ref_audios` is a
      `COMFY_AUTOGROW_V3` group, `min:0 max:3`, identical shape to the
      already-wired `ref_images`), then implemented as a direct mirror of
      the existing image-reference code in `tasks.py`/`api.py`
      (`reference_audio` form field, r2v only, up to 3, each an `<Audio N>`
      token). Dry-run tested against the live containerized DB (mocked
      `upload_media` only) — confirmed correct `ref_audios.ref_audio_N`
      wiring and valid `LoadAudio` nodes.
- [x] `GenerationJobSerializer` (list-level, not just detail) gained
      `raw_prompt`, so the new queue sidebar can show a title without an
      extra request per job.
- [x] `DELETE /api/jobs/{id}/` added (`job_detail`'s `@api_view` extended,
      per-method `@extend_schema` since `GET`/`DELETE` have different
      response shapes) — 409 while `processing`, otherwise deletes the
      reference/video files from disk plus the row, 204. Dry-run tested
      with Django's test `Client` against real rows (both branches);
      needed `Client(SERVER_NAME='localhost')` to match
      `DJANGO_ALLOWED_HOSTS` here, or it 400s with a generic Django error
      page that looks like a real bug but isn't one.
- [x] Frontend rewritten per `frontend fixes.txt`: `App.tsx` is now a
      persistent two-pane layout (`GenerateScreen` + always-visible
      `QueueSidebar`, no more separate `/jobs` route) instead of two routed
      pages; `GenerateScreen` gained content-type tabs (`Video` enabled,
      `Image`/`Audio` disabled placeholders), restyled mode tabs, a
      subdued toolbar for quality/ratio/length (replacing the previous
      pass's boxed fieldset), local thumbnail previews for image
      references (`URL.createObjectURL`, revoked on change/unmount), a
      second reference list for audio, and a visually dominant prompt
      fieldset; new `features/queue/QueueSidebar.tsx` (compact entries,
      prompt-derived title, video-thumbnail-on-done) and
      `features/queue/JobModal.tsx` (prompt/resolution/render-time/video/
      download/delete/redo) replace the old `QueueScreen.tsx`. Redo
      prefills mode/ratio/prompt/duration (resolved once that mode's
      presets reload) but not reference files (not retrievable
      client-side post-upload — a known, deliberate limitation). Page
      title changed to "Minimax H3 Generator" (`index.html` + nav +
      `LoginScreen`).
- [x] `npm run build` (tsc + vite) and `npm run lint` (oxlint) both clean.
- [x] Full Playwright browser pass against the rebuilt stack — every
      `frontend fixes.txt` item plus audio references and delete/redo —
      22/22 checks passed, zero console errors.
- [x] **Real near-miss caught mid-pass**: `COMFYUI_BASE_URL` in this
      environment is genuinely reachable, so the Playwright script's real
      job submission (needed to honestly test submit→modal) got queued for
      an actual GPU render without the explicit go-ahead this project
      requires. Caught via ComfyUI's own `GET /queue` (the job was only
      `queue_pending`, not yet `queue_running` — sitting behind an
      unrelated real job already in progress) and cancelled with
      `POST /queue {"delete": [prompt_id]}` before it could start; the
      orphaned `GenerationJob` row was manually resolved to `done` rather
      than left to time out on its own ~8.5 minutes later, so the FIFO
      queue wasn't stalled. **Lesson for future passes**: in this specific
      environment, ComfyUI being reachable is the normal state, not a rare
      edge case — any browser test that submits a real job needs this same
      immediate-cancel treatment unless GPU time is actually wanted.
- [x] Updated `ARCHITECTURE.md` (repo layout, generation domain, api.py,
      Frontend section rewritten, Getting the workflows working, Request/
      job flow, Verification, Deferred), `resources/COMFYUI_API_GUIDE.md`
      (§4 ref_audio_N no longer "not yet implemented"), `README.md`'s
      status line, and `FUNCTION_CHECK.md`.

## Ninth pass: small fixes + benchmark_render_times crash resilience

- [x] **Found and fixed a real bug**: i2v's "Last frame (optional)" (and,
      for consistency, "First frame") file slot had no way to clear a
      picked file once selected — no Remove button, and file inputs can't
      be reset back to empty from their own native picker UI. Added Remove
      buttons for both, with a `key`-remount trick to reset the
      (uncontrolled) file input's own DOM state so re-picking the exact
      same file afterward still fires `onChange` (a plain `setState(null)`
      alone doesn't clear what the input element itself thinks is
      selected). Verified in a real browser: thumbnail appears, Remove
      clears it without accidentally opening the native file picker
      (Remove is a sibling of the `<label>`, not nested inside it, to avoid
      that), and re-picking the same file afterward works.
- [x] `manage.py benchmark_render_times` made resilient to ComfyUI crashing
      mid-sweep, per direct user request: their ComfyUI runs under a
      process manager that auto-restarts it within ~1 minute of a crash,
      and they want to start an overnight sweep unattended rather than
      babysit it for manual restarts. Previously any crash stopped the
      whole command. Now: a crash triggers `_wait_for_restart()` (polls
      `is_alive()`, `--restart-timeout` default 300s), then `_warm_up()`
      (a tiny throwaway 2s t2v render so the model's loaded before the next
      *real*, recorded combination runs), then retries the SAME combination
      that crashed (it never completed, so moving on would silently lose
      that data point) — capped at `--max-crash-retries` (default 3) before
      giving up on just that one combination and moving on, so a single bad
      combination can't stall the whole run. Dry-run tested by mocking
      `integrations.comfyui`'s network calls directly (no real ComfyUI
      crash needed): crash-then-recover ends in a correct `ok` result;
      a combination that keeps crashing gives up cleanly after exactly
      4 attempts without raising (proving the sweep loop isn't halted);
      ComfyUI never coming back respects `--restart-timeout` rather than
      hanging. `manage.py check` clean.
- [x] Updated `ARCHITECTURE.md`'s "Benchmarking render times" section and
      added a "Verification" entry for the dry-run tests above.

## Tenth pass: LLM_API_KEY wrongly required

- [x] **Found and fixed a real bug**: user set `LLM_API_BASE_URL`/`LLM_MODEL`
      in `.env` for their self-hosted OpenAI-compatible server (no API key
      needed) but left `LLM_API_KEY` blank — `settings.LLM_ENABLED` and
      `integrations.llm.is_configured()` both required all three vars
      truthy, so `GET /api/config/`'s `llm_enabled` stayed `false` and no AI
      UI ever showed, even though the endpoint itself was fully reachable
      and working. Fixed: `LLM_API_KEY` is no longer part of either gate
      (only base URL + model are required); `_post_chat_completion()` now
      only sends an `Authorization` header when a key is actually set,
      rather than sending `Bearer ` with an empty string. Verified for
      real: `GET /api/config/` now reports `llm_enabled: true`, and a real
      `llm.improve_prompt()` call against the user's actual local server
      succeeded with no key sent. Updated `.env.example`'s LLM comment
      (key is optional; also noted `.env` edits need a backend/qcluster
      rebuild+recreate to take effect, since env_file values are only read
      at container start — not the cause here, just worth calling out) and
      `ARCHITECTURE.md`'s "LLM integration" section.

## Eleventh pass: chat UX -- feedback while waiting, markdown, final-prompt extraction

Direct follow-up to the tenth pass's fix: once the AI chat was actually
reachable, three usability problems surfaced: no visible feedback while
waiting for a reply ("looks like nothing is happening"), assistant replies
showing raw unrendered markdown, and no easy way to pull just the finished
prompt out of a reply full of commentary.

- [x] Added `react-markdown` + `remark-gfm` (chosen over `marked` +
      `dangerouslySetInnerHTML` specifically because it never renders raw
      HTML from the model's output by default -- no separate sanitizer
      needed to stay XSS-safe). Assistant chat messages now render as real
      markdown (`.chat-markdown` in `App.css`).
- [x] Added a typing-dots indicator + "Sending…" Send-button state while
      `postChatMessage.isPending`, auto-scroll so it's actually visible,
      and a visible error message on a failed send (previously the dots
      just silently vanished with no explanation).
- [x] `llm.chat_reply()`'s system prompt now asks the model to wrap a
      finalized prompt in a `` ```final-prompt ``` `` fenced block
      (`llm.FINAL_PROMPT_FENCE`); the frontend
      (`features/generate/chatMarkdown.ts`'s `parseChatMessage()`) extracts
      it mechanically, hides the raw fence from the rendered message, and
      shows it instead as its own "Suggested prompt" card with a one-click
      **Use this prompt** button -- replacing the old behavior of a generic
      "Use as prompt" button that copied the whole reply, commentary
      included. Falls back to that generic button for any reply that
      doesn't contain the block.
- [x] Verified against the real, actually-configured LLM (not mocked) both
      at the backend level (confirmed the model reliably follows the fence
      convention) and in a full real-browser Playwright pass (typing
      indicator, Send-button state, markdown rendering, fence extraction,
      "Use this prompt" filling the textarea with clean text) -- 10/10
      checks passed, zero console errors.
- [x] Updated `ARCHITECTURE.md`'s "LLM integration" section and added a
      "Verification" entry.
- Noted, not fixed (pre-existing, unrelated to this pass): `npm install`
  flagged 2 high-severity advisories in `react-router` (pulled in
  transitively via `react-router-dom`); `npm audit fix --force` would
  downgrade `react-router-dom` as a breaking change -- left alone since
  it's out of scope for this pass, but worth a deliberate look later.

## Twelfth pass: 413 on reference uploads (nginx body-size limit)

- [x] **Found and fixed a real bug**: user reported `413 Request Entity Too
      Large` when submitting a job with references attached. Root cause:
      `frontend/nginx.conf` never set `client_max_body_size`, so nginx's own
      1MB default rejected the multipart request outright — before it ever
      reached Django. Django's `DATA_UPLOAD_MAX_MEMORY_SIZE` doesn't apply
      to file upload bytes (excluded by its own docs), so this was the only
      thing actually capping upload size, and it was invisible to every
      API-level check done via Django's test `Client` in `FUNCTION_CHECK.md`
      §1 (which talks to the view directly, bypassing nginx entirely).
      Fixed with `client_max_body_size 500m;` on the proxied location.
      Reproduced first (`curl` a 2MB file through nginx -> confirmed real
      `413`), then verified the fix the same way (same request -> `403`,
      i.e. it now reaches Django's auth check; also tried a combined ~22MB
      two-file r2v-shaped request to comfortably cover realistic reference
      uploads).
- [x] Updated `ARCHITECTURE.md` ("Why a single nginx entrypoint" section)
      and `FUNCTION_CHECK.md` (§1.6 — added a note that reference-upload
      checks need at least one realistically-sized file sent through nginx
      itself, not just Django's test client, specifically because this bug
      was invisible to the latter).

## Thirteenth pass: no modal on queue, redo drops the AI-refined prompt

- [x] **Found and fixed a real bug**: submitting a job opened its `JobModal`
      immediately (an `onJobCreated` callback from an earlier pass) — user
      wanted queuing to just add the job to the sidebar and leave them on
      the form, not pop up a modal. Removed `onJobCreated` entirely (not
      left as a disabled no-op) from `GenerateScreen`/`App.tsx`; the
      sidebar already reactively picks up new jobs via `useCreateJob`'s own
      cache invalidation, so nothing else was needed.
- [x] **Found and fixed a real bug**: `redoJob`'s effect explicitly cleared
      `improvedPrompt` instead of restoring `redoJob.improved_prompt` — a
      redo of an AI-refined job silently lost the refinement, always
      falling back to the raw prompt. One-line fix.
- [x] Verified both in a real browser against the real, actually-configured
      LLM: AI-refine a prompt, queue it (Draft tier), confirm no modal +
      sidebar picks it up; open it, click Redo, confirm both raw and
      AI-refined prompt are restored verbatim. This submission reached real
      ComfyUI and actually rendered (~50s, Draft tier) -- unlike an earlier
      pass's near-miss, this time nothing was ahead of it in ComfyUI's own
      queue, so there was no pending window to cancel it in before it
      started; let it finish rather than interrupt an in-progress render.
      Flagged transparently rather than glossed over, per this project's
      standing practice around real GPU time.
- [x] Updated `ARCHITECTURE.md` ("Frontend" section, Request/job flow,
      Verification).

## Fourteenth pass: redo also restores reference images/audio

Direct follow-up to the thirteenth pass: "redo doesn't keep images" —
reference files were shipped as a documented limitation (a `File` object
can't be recovered client-side once already uploaded) rather than actually
attempted.

- [x] Implemented restoration properly: each reference's bytes are still
      sitting at its own already-uploaded, same-origin media URL, so
      `fetchAsFile()` re-downloads them and repacks as a fresh `File`,
      sorted back into the right slots the same way `tasks.py` interprets
      them server-side (i2v order 0/1 -> first/last frame; r2v -> the
      image/audio lists).
- [x] **Found and fixed a second real bug while verifying the first fix**:
      the restore is naturally async, but the same effect calls
      `onRedoConsumed()` (setting `redoJob` back to `null`) in the same
      pass -- a naive cleanup-based cancellation guard couldn't tell that
      apart from the redo being superseded by a genuinely different one,
      and silently cancelled every restore before its fetches resolved
      (images/audio always came back empty, no error, no console output).
      Fixed with an id-keyed ref (`activeRedoIdRef`) checked after the
      fetches resolve instead of a cleanup-triggered flag.
- [x] Verified both together in a real browser against jobs seeded
      directly via Django shell with real reference files attached (no
      ComfyUI call involved at all, avoiding another accidental real
      render) -- r2v redo restored 2 images + 1 audio file, correct
      filenames/order/thumbnails; i2v redo restored both first and last
      frame. 7/7 checks passed, zero console errors.
- [x] Updated `ARCHITECTURE.md` ("Frontend" section, Verification) and
      `FUNCTION_CHECK.md` (§2 step 7 rewritten -- restoring references is
      now the expected behavior to check, not a documented non-failure to
      wave away; also fixed a stale line there still describing submit as
      opening a modal, from before the thirteenth pass's fix).

## Fifteenth pass: unguessable media filenames + real access control (security)

- [x] **Found and fixed a real security bug**: user flagged that filenames
      were predictable and URLs could be "walked." Confirmed concretely
      before fixing: stored video filenames were ComfyUI's own literal
      sequential output names (`MiniMax_H3_00324_.mp4` -> `00325` ->
      `00326` -> ..., trivially incrementable), reference filenames were
      the uploader's original filename verbatim (phone-camera timestamps
      like `20230625_092948.jpg`), and `/media/`'s `django.views.static.serve`
      mount has no authentication or per-user access control at all --
      combined, anyone who could reach the app could enumerate and
      download any user's generated videos or reference uploads.
- [x] Fixed the filename half: `GenerationJob.video_file`/
      `ReferenceAsset.file` both now use a callable `upload_to`
      (`generated_video_upload_path`/`reference_upload_path` in
      `generation/models.py`) discarding the original filename entirely
      except a sanitized extension, replaced with a random UUID. Migration
      `0011` (`AlterField`, no schema change -- `upload_to` isn't a DB
      column property, Django just tracks it for migration-state
      consistency). Verified: a fresh video save and reference upload both
      land on unguessable paths now; existing render/dry-run pipeline
      still works unaffected (filename choice is purely a storage-path
      detail, doesn't touch the ComfyUI workflow-building code path).
      Only affects *new* uploads going forward -- existing files keep
      their old predictable names unless separately renamed (not done).
- [x] **Flagged the deeper issue before building anything more**: `/media/`
      had zero access control regardless of filename guessability, so
      anyone with *any* valid URL (leaked link, log, referrer, etc.) could
      still fetch it with no login at all -- a distinct, larger change from
      the filename fix, so asked rather than assumed. User confirmed: fix
      it too.
- [x] Added real access control: new `generation/media_views.py::
      serve_protected_media()` wraps `django.views.static.serve` (keeping
      its Range/ETag/conditional-GET handling for `<video>` seeking) behind
      a check that the requesting user owns the `GenerationJob`/
      `ReferenceAsset` the path resolves to; 404 (not 403) for both
      logged-out and logged-in-as-someone-else, matching this codebase's
      existing not-found-not-forbidden convention for cross-user access.
      Staff bypass the ownership check (not the auth check) since
      `/admin/`'s own file links point at the same URLs and staff already
      has full DB read access anyway. Deliberately plain Django-served
      responses, not nginx `X-Accel-Redirect` -- would need giving
      `frontend` access to the media volume it deliberately has none of,
      for a performance win this app's scale doesn't need.
- [x] Verified with real login sessions (not mocked), both media kinds:
      no auth -> 404; owner -> 200 with correct bytes; a different
      logged-in user -> 404; staff (non-owner) -> 200. Also verified the
      actual `<video>` element in `JobModal` still resolves/plays for the
      owning user through the real UI, confirming the fix didn't break
      normal playback while closing the hole.
- [x] Updated `ARCHITECTURE.md` (`backend` service bullet, Verification;
      removed the now-resolved Deferred bullet).

## Sixteenth pass: orphaned-job recovery after a restart (+ a real incident)

- [x] **Found and fixed a real bug**: user reported a job stuck showing
      "Processing…" forever, correctly self-diagnosed as a qcluster
      restart landing mid-render -- `_claim_next_job()` only ever claims
      `QUEUED` jobs, so nothing would ever pick a `PROCESSING` job back up.
      Built `generation.tasks.recover_orphaned_processing_jobs()`, called
      at the top of `process_queue()` and once at qcluster startup via new
      `manage.py recover_stale_jobs` (wired into `docker-compose.yml`'s
      `qcluster` command). Tries to actually recover the result: checks
      ComfyUI's `/history` (may have finished while nothing was watching),
      then `/queue` (may genuinely still be rendering -- resumes the wait
      rather than abandoning it), only marks failed once ComfyUI has no
      record at all. New `comfyui.get_history()`/`is_prompt_queued()`;
      `_execute_job()`'s finalize logic extracted into
      `_finish_job_from_history()` so recovery and the normal path share it.
- [x] **Caused and then fixed a real incident testing it**: dry-run tested
      the three recovery scenarios by mocking `comfyui` and calling
      `recover_orphaned_processing_jobs()` directly via shell against the
      real shared dev database. That function queries *every* `PROCESSING`
      row, no scoping, no locking -- it swept up two real jobs alongside
      the synthetic test rows: one had already finished successfully and
      got wrongly marked "lost"; the other was **still genuinely
      rendering** at that exact moment and got wrongly marked "lost"
      mid-render -- exactly the "a video that is still rendering failed
      when it started up" the user then reported. Not a logic bug (a real
      unmocked check would have found it still running) -- the function's
      only real safety net (`Q_CLUSTER_WORKERS=1` serializing it against a
      live `_execute_job()`) only holds at its two sanctioned call sites,
      and calling it ad hoc from a shell sidesteps that. Recovered both:
      the finished one by fetching its real result by hand; the
      still-rendering one by leaving it completely alone and monitoring
      ComfyUI until the *original* live worker (never restarted -- only
      `backend`'s image had been rebuilt) self-healed it correctly on its
      own a few minutes later. Both needed a follow-up save clearing a
      stale `error_message` string the incorrect write left behind
      (`_finish_job_from_history()`'s `update_fields` doesn't touch that
      column). Both ended up with their real, correct videos -- no actual
      data loss, confirmed by re-downloading and checking each file's
      header. Re-verified the recovery logic afterward properly: calling
      `_recover_one_orphaned_job()` directly on one explicit synthetic job
      object, never the sweep, with an explicit assertion no real job was
      `PROCESSING` before/after.
- [x] Added a loud warning to `recover_orphaned_processing_jobs()`'s own
      docstring against ad hoc invocation outside its two real call sites,
      naming this incident as the reason.
- [x] Updated `ARCHITECTURE.md` ("Verification", full incident writeup).

## Seventeenth pass: preset/duration catalog + JobModal + chat rewrite (batch of direct requests)

- [x] Migration `0012`: genuine non-draft `Standard` tier (0.2MP, 20 steps)
      alongside the existing `Draft` (0.2MP, 8 steps); every tier (6 now,
      per mode) offers every integer second 2-20 (19 options, was a curated
      3/5/8/12 spread) -- only *adds* rows, never deletes/reseeds, since
      real jobs already `PROTECT`-reference the old catalog by this point.
      Verified: full 2-20s range confirmed per tier; pre-existing jobs'
      duration FKs confirmed untouched.
- [x] `JobModal` now shows `megapixels` alongside resolution/ratio (was on
      the job already, just never displayed).
- [x] **Found and fixed a real bug**: the "keep the same duration when
      switching quality" logic looked up the current duration in the *new*
      tier's array using the *old* id -- always empty, so it silently fell
      back to the tier's first option every time, never actually working.
      Fixed with a `lastDurationSecondsRef` tracking the last-selected
      seconds value independently, falling back to the *nearest* available
      length (not just the first) when the exact value isn't offered.
- [x] Full chat rewrite, three direct requests bundled together:
  - **Stateless until job-linked**: replaced the three
    session-based endpoints (`create_chat_session`/`get_chat_session`/
    `post_chat_message`) with one stateless `POST /api/prompt/chat/` --
    the frontend resends the whole transcript each turn, nothing is
    written to the DB during the live conversation. `POST /api/jobs/`
    gained an optional `chat_transcript` field; a `PromptChatSession`
    (linked via `resulting_job`) is only ever created there, in the same
    transaction as the job. Verified both halves for real: a live chat
    with no job queued left zero DB rows; a job created with a transcript
    attached left exactly one correctly-linked session with all messages.
  - **Context-aware**: chat now receives the user's current draft prompt
    (folded into the system message) and, gated behind a new
    `LLM_VISION_ENABLED` env var (off by default), actual reference image
    bytes as vision content parts. Tested vision against this project's
    own real configured model with a real test image -- it replied "I
    cannot see any attached image," so vision is confirmed *not* actually
    working with this specific server/model combo (likely no
    vision-projector loaded server-side); left off in the real `.env`
    based on that finding rather than assumed to work.
  - **"Use as prompt" no longer clobbers the raw prompt**: both the
    final-prompt-card button and the generic fallback now write into
    `improvedPrompt` (same field the one-shot refine button uses) instead
    of overwriting `rawPrompt`, renamed to "Use as AI-refined prompt".
- [x] Verified the whole batch in a real browser against the real,
      actually-configured LLM: quality dropdown shows both Draft and
      Standard; length slider spans 2-20s; switching quality preserves the
      exact seconds value; a real chat reply referenced an unsent draft
      prompt's content unprompted; the AI-refined button left the raw
      prompt untouched; zero DB rows existed after a real conversation
      with no job queued. 12/12 checks (after resolving one test-script
      false negative -- it compared a whole label string including the
      *estimated render time*, which legitimately differs between tiers,
      instead of just the *duration value*, which was in fact identical).
- [x] Updated `ARCHITECTURE.md` (generation domain, Frontend, LLM
      integration -- substantially rewritten, Verification), `README.md`
      (LLM env var table), `FUNCTION_CHECK.md` (§1.5 prompt-assist).

## Eighteenth pass: invite-management admin page, visual polish, mobile responsiveness

- [x] **Backend**: `accounts/api.py` gained `GET/POST /api/invites/` (list
      newest-first / create, `expires_in_days` computed server-side into
      `expires_at` to avoid client clock skew) and `DELETE
      /api/invites/<id>/` (revoke regardless of redeemed state), all gated
      by DRF's `IsAdminUser` (`request.user.is_staff` — same bar Django's
      own `/admin/` uses). `GET /api/me/` gained `is_staff` on its response.
      Dry-run verified via Django's test client: non-staff gets 403 on all
      three, staff can list/create/delete for real, `me()` reports
      `is_staff` correctly for both.
- [x] **Frontend**: new `features/admin/InvitesScreen.tsx` — create form
      (optional email + expiry), list with status badges
      (Active/Redeemed/Expired), copy-link (writes
      `/invite/<token>/` to the clipboard) and revoke actions. Wired into
      `App.tsx` as a new SPA route, nav gains an "Admin" link visible only
      to staff (`me.data.is_staff`), route itself redirects non-staff away
      client-side (defense in depth — `IsAdminUser` is the real gate).
- [x] **Real infra finding**: `frontend/nginx.conf` prefix-matches
      `^/(api|accounts|admin|static|media)/` straight to Django, so a naive
      `/admin` SPA route would've been silently swallowed by Django's own
      admin-site proxy rule before React Router ever saw it. Used
      **`/manage`** for the new route instead — caught before it shipped as
      a bug, not after. Documented in `ARCHITECTURE.md` so it isn't
      rediscovered the hard way.
- [x] **Visual polish pass** (`App.css`, CSS-only except one wrapper `<div
      className="login-card">` in `LoginScreen.tsx`): put the previously
      wholly-unused `--shadow` custom property to work (button/queue-entry
      hover, permanent modal shadow, login card); added `transition`s to
      buttons/tabs/inputs/queue entries (were instant color snaps);
      consistent `:focus-visible` ring (`--accent`) across
      buttons/inputs/tabs (previously just browser-default outline);
      richer empty states via a new `.empty-state` class (queue sidebar,
      invites list); full `.admin-screen`/`.invite-*` styling for the new
      admin page (was entirely unstyled after the frontend work above).
- [x] **Mobile responsiveness audit** — new `max-width: 480px` breakpoint:
      `.tab-strip` scrolls horizontally instead of overflowing
      (`overflow-x: auto` + `.tab { flex-shrink: 0 }`); reduced
      `main`/`.app-nav` padding; `.modal` goes near-full-screen (zero
      radius, full height) instead of a postage-stamp dialog with wasted
      margin; bumped tap-target padding on buttons/tabs.
- [x] **Found and fixed a real bug during browser verification**: at a
      390px viewport the nav bar (`title` + `Generate`/`Admin` links +
      `username · Log out`) didn't wrap, so the username/logout span was
      pushed off-screen (`document.documentElement.scrollWidth` = 505 vs.
      a 390 viewport — confirmed via a Playwright script that scans every
      element for `rect.right > viewport width`, not just eyeballing a
      screenshot). Fixed with `flex-wrap: wrap` on `.app-nav` and
      `flex-basis: 100%` on `.app-user` at the 480px breakpoint, so the
      user/logout block drops to its own row instead of overflowing.
      Re-verified: 0px overflow on Generate/Admin/Login at 390px after the
      fix.
- [x] Verified in a real browser (Playwright, reusing a prior session's
      cached `node_modules`/Chromium via `NODE_PATH`): 13/13 checks passed
      — desktop (login card renders, staff sees the Admin link and
      non-staff doesn't, `/manage` loads and redirects non-staff away,
      full invite create/copy-link/revoke cycle works end-to-end) and
      mobile 390×844 (no horizontal overflow on Generate/Admin/Login,
      tab-strip scrolls within itself rather than overflowing the page).
      Zero console/page errors throughout. `npm run build` and `npm run
      lint` both clean. Throwaway staff/non-staff test accounts and the
      test invite created during verification were all cleaned up
      afterward.
- [x] Updated `ARCHITECTURE.md` (`accounts` app bullet, new
      `admin/InvitesScreen.tsx` file-tree entry, the `/admin`-vs-`/manage`
      nginx collision called out explicitly) and `README.md` ("Accounts &
      invites" now describes `/manage` as the primary path, `/admin/`
      Django admin as the fallback).

## Nineteenth pass: quality/duration catalog admin tooling

- User complaint: adding a new quality level meant hand-creating 3
  `RenderPreset` rows (one per mode) one at a time in Django admin with no
  copy tool; renaming a level meant editing every mode's row individually
  since "label" isn't a first-class entity; extending the duration range
  or limiting a duration/level to specific modes meant creating/deleting
  individual `RenderDuration` rows by hand, no batch operation existed.
  User chose (via AskUserQuestion) to build this as a **new tab in the
  existing `/manage` SPA** rather than richer Django admin.
- [x] **Backend**, zero schema changes: `RenderPreset.is_active`/
      `RenderDuration.is_active` already gave exactly the soft-disable
      semantics needed (and sidestep `GenerationJob`'s `PROTECT` FKs,
      which make hard deletes of in-use rows impossible anyway) — a
      "quality level" stays a convention (rows sharing one `label` across
      modes), not a new model. New `generation/admin_api.py`, 4
      `IsAdminUser`-gated endpoints: `GET /api/quality-catalog/` (full
      read model grouped by label, including inactive rows so they can be
      re-enabled), `POST /api/quality-levels/` (create across 1+ modes at
      once, with an optional `copy_durations_from` so a level is never
      born with zero selectable lengths), `PATCH
      /api/quality-levels/<label>/` (rename — updates every mode's row in
      one call — and/or partial per-mode megapixels/steps/is_active
      update, creating a mode's row if it didn't have one), `PATCH
      /api/quality-durations/<seconds>/` (the actual "limit duration X to
      certain quality levels/modes" batch tool — a list of `(label, mode,
      is_active, estimated_render_seconds)` targets, validated fully
      before anything is written; also how a brand new duration value
      gets introduced, since it upserts). Dry-run verified via Django's
      test client: non-staff 403 on all 4; as staff, created a throwaway
      level (t2v only, cloned durations from Standard), renamed it,
      toggled a mode off/on, added a second mode to it, deactivated then
      reactivated a duration (confirming reactivation without a fresh
      estimate reuses the prior one), and every validation error path
      (duplicate label, missing megapixels/steps, rename collision,
      activating a brand-new duration with no estimate, unknown level in
      a target). Hard-deleted the throwaway rows afterward (safe — no
      real `GenerationJob` ever referenced them).
- [x] **Frontend**: new `admin/AdminLayout.tsx` (tab nav, reusing the
      existing `.tab-strip`/`.tab` CSS) wrapping nested `/manage/invites`
      and `/manage/catalog` routes; new `admin/CatalogScreen.tsx` — a
      quality-levels table (inline-editable label/megapixels/steps/
      is_draft/is_active, a "+ enable" control per empty mode cell, a "0
      active durations" warning badge) plus an "Add quality level" form,
      and a spreadsheet-style durations table (rows = duration values,
      columns grouped by level) with checkbox+estimate cells and an "Add
      duration (or a range like 21-25)" control that stages new rows
      client-side until the first cell is actually checked. Every field
      auto-saves on change/blur — no page-wide save button, matching this
      app's existing immediate-action UX (Delete/Revoke/Redo already
      commit immediately elsewhere).
- [x] **Found and fixed two real bugs during browser verification**
      (Playwright, reusing this session's cached install/Chromium):
  - The megapixels `<input type="number">`s used `step="0.05"` — the
    browser's native constraint validation silently blocks submitting
    any value not on that exact grid (e.g. 0.33), even though
    `RenderPreset.megapixels` is a free `FloatField` with no such
    restriction server-side. Caught because a Playwright-typed "0.33"
    produced a validation tooltip and no request was ever sent. Fixed by
    switching every megapixels input to `step="any"`.
  - A much more interesting one: the quality-levels table rendered with
    every mode's data stacked in the **first** mode column and the other
    columns empty — confirmed via the API directly that the backend
    grouping was correct (6 levels, each with all 3 modes), so the bug
    was frontend-only. Root cause: `.catalog-mode-cell`/
    `.catalog-cell-enabling` set `display: flex` directly on the `<td>`
    elements — in CSS, `display: flex` **replaces an element's outer
    display type**, so a `<td>` with `display: flex` stops being a
    `table-cell` for layout purposes entirely and falls out of the
    table's column grid, rendering as a stacked block instead (confirmed
    by reading `getBoundingClientRect()` on the actual DOM nodes — all
    three mode cells shared the same `x`, stacked at different `y`).
    Fixed by moving the flex styling to a new inner wrapper
    (`.catalog-mode-cell-inner`/`.catalog-cell-enabling-inner`) inside
    each `<td>`, leaving the `<td>` itself with default table-cell
    display. **Worth remembering for any future table-based UI in this
    project**: never put `display: flex`/`grid` directly on a `<td>`/
    `<th>` — always wrap the cell's contents in an inner element instead.
  - Also fixed (not from browser verification, spotted during the same
    pass): the level's `is_draft` checkbox and each mode cell's
    `is_active` checkbox were purely controlled by server state with no
    local buffer — clicking one fired the mutation but React re-rendered
    the checkbox back to its pre-click state on the same tick (since
    `preset.is_active`/`level.is_draft` hadn't changed yet), making
    clicks look unresponsive until the mutation+refetch round-trip
    finished. Gave both local optimistic state (flip immediately on
    click, resynced via `useEffect` once fresh server data arrives),
    matching the pattern `DurationCell` already used.
- [x] Verified in a real browser: 14/14 Playwright checks passed after
      the fixes above — 6×3 matrix renders correctly (confirmed via raw
      `getBoundingClientRect()`, not just a screenshot), full create →
      rename → toggle-mode-off/on → duration-toggle-off/on-with-estimate
      → add-new-duration-value → activate cycle all persisted correctly
      across a page reload, Invites tab still reachable/renders and shows
      correct active-tab styling (regression check on the new nested
      routing). Zero console errors throughout. `npm run build`/`npm run
      lint` clean. All throwaway staff/non-staff users and the test level
      cleaned up afterward; confirmed the real catalog was back to
      exactly 18 presets.
- [x] Updated `ARCHITECTURE.md` (`generation` app bullet gains the 4 new
      endpoints and the "quality level is a convention, not a model"
      rationale; new `admin/AdminLayout.tsx`/`CatalogScreen.tsx`
      file-tree entries).

## Twentieth pass: catalog admin refinements (ordering, column labels, curve-fit estimates)

Direct follow-up requests on the just-shipped Quality & Duration tab:
explicit sorting for quality levels (bonus: drag-to-reorder), real column
headers for the megapixels/steps sub-columns (previously packed into one
unlabeled cell), and a way to estimate `RenderDuration.estimated_render_
seconds` by fitting a curve to real completed-job render times instead of
guessing.

- [x] **`RenderPreset.sort_order`** (new `IntegerField`, migration
      `0013_renderpreset_sort_order.py`): admin-controlled display order,
      kept in sync across every mode's row for the same label. `Meta.
      ordering` changed from `["mode", "megapixels"]` to `["sort_order",
      "mode", "megapixels"]` — since `GET /api/presets/` relies on this
      same ordering with no explicit `.order_by()`, reordering in the new
      admin tool also reorders the quality dropdown on the Generate
      screen, which was the actual point. Backfilled the live catalog's 6
      labels to their existing curated order (Draft/Standard/Low/Medium/
      High/Max) so the migration doesn't visibly change anything on its
      own.
- [x] **Discovered while backfilling**: the live catalog already had
      "Standard" renamed to "Lowest" — real usage of the rename feature
      from the previous pass, not a bug. Fixed up its `sort_order`
      manually to slot back in right after Draft.
- [x] **Backend**: two new endpoints in `generation/admin_api.py` —
      `POST /api/quality-levels/reorder/` (body: every existing label in
      the desired order; 400 if the set doesn't match exactly, catching
      stale client state) and `POST /api/quality-durations/estimate/`
      (fits an OLS line — plain Python, no numpy/scipy dependency added —
      to real `GenerationJob.started_at`/`finished_at` data for one exact
      `(label, mode)` preset; `fit_available: false` rather than an error
      when there's fewer than 2 distinct requested durations among
      completed jobs; `apply: true` writes the fit onto existing
      `RenderDuration` rows only, never creates new ones or touches
      `is_active`). `update_quality_level()` also gained a direct
      `sort_order` field for one-off priority edits, and
      `create_quality_level()` now defaults new levels to the end of the
      order rather than an arbitrary position.
- [x] **Found and fixed a real bug during dry-run verification**:
      `RenderDuration.objects.values_list("duration_seconds", flat=True)
      .distinct()` returned 342 rows instead of the expected 19 —
      `RenderPreset.Meta.ordering` bleeds into the JOIN's implicit `ORDER
      BY`, and Postgres requires `DISTINCT` queries to include every
      `ORDER BY` expression in the `SELECT` list, silently turning it into
      "distinct `(duration_seconds, sort_order, mode, megapixels)`"
      instead of just `duration_seconds` (confirmed via `.query` on the
      queryset showing the extra columns Django had added). Fixed by
      deduplicating in Python over already-fetched values, matching the
      pattern `_serialize_catalog()` already used for exactly this reason
      — worth remembering for any future `.distinct()` call on a model
      with a non-trivial `Meta.ordering`.
- [x] **Frontend**: `CatalogScreen.tsx`'s quality-levels table gained a
      2-row header (`Label`/`Draft` rowspan-2, then `MP`/`Steps`/`Active`
      sub-columns under each mode) and `LevelModeCell` was restructured
      to return 3 separate `<td>`s instead of one flexed cell — the
      empty/"+ enable" state uses `colSpan={3}` to still span all three.
      A new "Order" column holds a drag handle (native HTML5 DnD, no new
      dependency) and up/down buttons, both calling the same
      `useReorderQualityLevels()` mutation with the full recomputed label
      order. The duration table's mode sub-headers gained a small "fit"
      button opening a modal (reusing the existing `.modal-overlay`/
      `.modal` CSS from `JobModal`) that previews the OLS fit — sample/
      distinct counts, the fit line in plain English, a Duration/Current/
      Fitted table — before an explicit "Apply" write.
- [x] Verified in a real browser (Playwright, 11/11 checks, zero console
      errors): the 2-row header's MP/Steps/Active sub-columns align
      exactly with their body cells (checked via `getBoundingClientRect()`
      on every column, the same technique that caught the `display:
      flex`-on-`<td>` bug last pass — never just trust a screenshot for
      table layout); up/down buttons swap adjacent rows and persist after
      reload; native drag-and-drop (`locator.dragTo()`) reorders and
      persists; the estimate modal opens, shows a real "not enough data"
      or fitted-curve result, and closes cleanly. Backend dry-run
      separately verified the full estimate flow with synthetic
      `GenerationJob` rows (linear data in, sane slope/intercept out,
      `apply: true` updated exactly the existing rows and created none),
      the reorder endpoint's validation (missing-label 400), and the
      direct `sort_order` PATCH field. All synthetic jobs, shuffled
      ordering (restored to the curated order), and the throwaway staff
      user cleaned up afterward.
- [x] Updated `ARCHITECTURE.md` (`generation` app bullet: `sort_order`,
      the two new endpoints, and the `.distinct()`/`Meta.ordering` bug
      called out explicitly so it isn't rediscovered the hard way).

## Twenty-first pass: multi-dimensional (pooled) duration estimation

Direct follow-up to the just-shipped curve-fit estimator: "I want the fit
function to work across all at the same time. If you have a 2s on draft
and 2s on lowest, you could see the gap, and if you got more points you
could fit the curve. Basically have it work in multiple dimensions...
There will probably be 1-2 abrupt curve adjustments where it goes from
enough vram -> enough system ram -> swapping parts, it would be different
spot on each curve but should be happening at same data amount so should
be some correlation." A real, physically-grounded modeling request —
engineered honestly rather than faked: introduced a composite "workload"
variable, pooled completed jobs across every quality level of a mode into
one fit, added an optional single-breakpoint piecewise model, and built
two hand-rolled SVG charts since "you could see the gap" is explicitly a
visual ask and this project has no charting library. Scope call made and
stated up front rather than asked about: **at most one breakpoint**
supported (not the full "1-2"), since a second roughly squares the
brute-force search space and raises overfitting risk against what's
likely a modest number of real completed jobs — a reasonable follow-up
once real data shows it's needed.

- [x] **`GenerationJob.steps`** (new field, migration
      `0014_generationjob_steps_snapshot.py`, backfilled from
      `job.preset.steps` for all 15 real existing jobs): pooling fits
      across levels means every data point's workload depends on that
      job's steps count, but it wasn't snapshotted before (only
      `megapixels`/`duration_seconds` were) — computing it from
      `job.preset.steps` live would silently mis-attribute old completed
      jobs to whatever steps count the preset has *now*, a real accuracy
      bug given the previous pass made editing a preset's steps routine.
      `generation/api.py`'s `jobs()` now snapshots it at creation time
      alongside the existing fields.
- [x] **Backend**: `POST /api/quality-durations/estimate/` reworked from
      `{label, mode, apply}` (one preset) to `{mode, apply}` (every level
      of that mode, pooled). Workload = `steps * megapixels *
      duration_seconds`. New `_sse()` and `_find_best_piecewise_fit()`
      helpers (pure Python, no numpy/scipy — brute-force search over
      candidate workload splits from the sorted distinct values, each
      candidate needs ≥3 points and ≥2 distinct x-values on both sides to
      avoid a zero-division on a degenerate single-x segment; picks the
      split with lowest total SSE; only reported/used if it beats the
      single line by ≥15% and total points ≥8). Response now carries
      `model`, `linear`, `piecewise` (nullable), `samples` (every
      completed job used — the chart's raw data), and `estimates` spanning
      every level's every duration, not just one preset's.
- [x] **Found and fixed a real bug during dry-run verification**: the
      exact same `.distinct()`-on-an-ordered-queryset issue from last
      pass (documented in the twentieth-pass entry above) needed the
      identical Python-side-dedup fix applied a second time, since the
      new pooled endpoint computes the duration palette independently —
      confirms it's a durable gotcha worth remembering for this model,
      not a one-off.
- [x] **Process incident during dry-run verification, worth recording**:
      testing the pooled fit with synthetic jobs on real `t2v` presets
      (Draft + Lowest) and calling `apply: true` — twice — silently
      overwrote the *real* `t2v` catalog's `estimated_render_seconds`
      values with fits contaminated by a mix of real historical jobs and
      my synthetic ones, since `apply` is necessarily mode-wide by
      design (it has to touch every level to be useful) and I hadn't
      snapshotted the original values first. Recovered by restoring every
      `t2v` `RenderDuration` row to the documented seed formula from
      `0012_standard_tier_and_full_duration_range.py`'s docstring (`15 +
      (steps/8) * megapixels * 60 * duration_seconds`), cross-checked
      against an untouched `i2v` value that matched exactly. **Caveat
      worth flagging**: this recovers the *documented baseline*, not
      necessarily whatever hand-tuning (if any) `t2v` specifically had
      before — low-stakes since these are only queue-ETA estimates
      (never retroactive; `GenerationJob.estimated_seconds` is already
      snapshotted per-job and unaffected), but real data was touched
      without a backup, a genuine process mistake. **Fixed the process
      itself, not just the data**: for all further apply-testing in this
      same pass (the browser/Playwright verification, which necessarily
      clicks the real Apply button), snapshotted every real `RenderDuration`
      row for the tested mode (`r2v`, otherwise untouched) to a file
      *outside* the container before testing, and restored from that
      exact snapshot afterward — confirmed byte-for-byte identical
      post-restore, not just formula-plausible. This discipline (snapshot
      real data before any test that calls a real mutating admin
      endpoint against production rows) should carry forward to any
      future testing of this subsystem.
- [x] **Frontend**: the per-`(level, mode)` "fit" button in each duration
      subheader is replaced by a 3-button toolbar above the "Duration
      options" table (`t2v`/`i2v`/`r2v`, matching the endpoint's new
      mode-only scope). `EstimateModal` reworked: a plain-English summary
      of the fit (linear or two-segment, with the breakpoint workload
      called out), **two hand-rolled inline SVG charts** (no charting
      library — none exists in this project and none is needed for a few
      dozen points) — one in duration-space (x = `duration_seconds`, one
      colored series per level, literally "the gap at 2s"), one in
      workload-space (x = workload, all levels pooled with the fitted
      line(s) overlaid, "the correlation") — a small fixed 8-color
      palette assigned by each level's table-row position, a legend, and
      a Level/Duration/Current/Fitted table spanning every level. New
      `.modal-wide` modifier (bumps max-width past the default 720px) to
      fit two charts plus a wider table.
- [x] Verified: unit-level math checks on `_ols_fit`/`_sse`/
      `_find_best_piecewise_fit` with clean synthetic ground truth
      (exact recovery of a noise-free `y = 10 + 5x` line; correct low-segment
      recovery and correct rejection of the too-few-points and
      degenerate-same-x-segment cases — confirmed no `ZeroDivisionError`).
      Endpoint-level structural checks against the real, already-populated
      `t2v` history (sample count grows correctly when jobs are added,
      `apply` writes match the response's `fitted_estimate` values
      exactly via the ORM, bad mode → 400). Real browser (Playwright):
      toolbar shows exactly 3 buttons and no per-cell buttons remain;
      both charts render with in-bounds, multi-colored data points;
      legend and table span multiple levels; Apply closes the modal and
      the catalog reflects the new numbers after reload. The 8 console
      404s seen during this pass were confirmed (via nginx access logs)
      to be the test's own synthetic jobs' fake `video_file` thumbnails
      failing to load on the Generate/Queue page's brief render during
      login redirect — not an app bug. Zero *unexpected* console errors.
      `npm run build`/`npm run lint` clean. All synthetic jobs, the
      throwaway staff user, and (per the incident above) every touched
      `RenderDuration` row restored; final state confirmed back to
      exactly 18 presets / 342 durations / 15 real jobs.
- [x] Updated `ARCHITECTURE.md` (`generation` app bullet: `GenerationJob
      .steps`, the reworked pooled/workload/piecewise estimate endpoint).

## Still outstanding (next pass)

- A real `benchmark_render_times` run to actually sweep the matrix and
  inform real `RenderPreset.estimated_render_seconds` values (one manual
  data point now exists, see above, but that's not the same thing)
- r2v's `ref_video_N`/`ref_video_audio_N` (only `ref_image_N`/`ref_audio_N`
  are wired — `ref_video_N` needs frame-extraction, a different shape)
- i2v's first/last-frame role is inferred from `ReferenceAsset.order`
  (convention, not an explicit field)
- No way to cancel a job that's actively `processing` (only `queued`/`done`
  jobs can be deleted now) — would need reintroducing a distinct terminal
  state
- No frontend/API typegen — `frontend/src/api/types.ts` is hand-maintained
  to match the backend response shapes rather than generated from
  `/api/schema/`; fine at this size, worth automating if the API surface
  keeps growing
- No deep link for the job modal (`?job=<id>`) — plain component state
- Image/Audio content-type tabs are cosmetic placeholders — no such
  generation pipeline exists
- Tests
- TLS / production hardening
