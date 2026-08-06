# Function check

A repeatable procedure for verifying the app actually works end to end —
API first (fast, scriptable, no browser needed), then the frontend in a real
browser. Written so it can be re-run after any change without re-deriving
what "working" means each time. See [`ARCHITECTURE.md`](ARCHITECTURE.md) for
*why* things are built this way; this doc is just the *how do I know it
still works* checklist.

## 0. Before you start

- Stack must be running the **current** code: `docker compose up -d --build`
  (rebuilds anything changed; safe to run even if nothing changed).
- Check `docker compose exec backend python manage.py showmigrations` has no
  unapplied (`[ ]`) migrations — `migrate` should have applied everything on
  startup, but confirms it if you're unsure the rebuild actually landed.
- Know your `COMFYUI_BASE_URL` (`.env`) and whether it's actually reachable
  right now (`curl -m 5 <that-url>/system_stats` — expect a JSON body with
  `"system"`/`"devices"` keys). If it's not reachable, a job that reaches
  ComfyUI just ends `done` with `error_message` set — itself correct,
  expected behavior, not a check failure, for every step in §1-§2.
  **If it *is* reachable, §2 step 6 (a real browser submit) is itself real
  GPU-queuing, not just job creation** — `qcluster` picks it up immediately
  and gets a real `queue_prompt()` accepted. This has bitten a prior pass:
  don't assume §2 is automatically safe just because §1's API-level checks
  are. Either get the same explicit go-ahead §4 requires before doing step
  6, or immediately cancel the resulting job afterward: check
  `GET <COMFYUI_BASE_URL>/queue` for the job's `comfyui_prompt_id` (pull it
  via `manage.py shell` — not exposed over the API), and if it's only in
  `queue_pending` (not yet `queue_running`), cancel with
  `POST <COMFYUI_BASE_URL>/queue {"delete": [prompt_id]}` before it starts,
  then resolve the now-orphaned job to `done` via shell rather than letting
  it time out on its own (~`estimated_seconds * 3 + 300` later). Only
  attempt §4 itself (a real full render) with the same go-ahead — see that
  section.
- Know whether `LLM_API_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL` and the `OIDC_*`
  vars are set in `.env` — several steps below branch on this
  (`GET /api/config/` tells you definitively; don't guess from `.env.example`).

## 1. API-level check (no browser needed)

Everything here uses a Django test client from `manage.py shell` (or
equivalent `curl` calls with a real cookie jar) — it exercises the exact
same view code a browser session would, without needing to drive one.
Create one throwaway user for this and delete it at the end (§1.11).

### 1.1 Public/meta endpoints (no login required)

```
GET /api/health/          -> 200 {"status": "ok"}
GET /api/config/          -> 200 {"llm_enabled": bool, "oidc_enabled": bool,
                                   "oidc_login_url": str|null, "oidc_provider_name": str}
GET /api/me/ (logged out) -> 200 {"authenticated": false}
```

If `oidc_enabled` is `true`, confirm `oidc_login_url` is a real path (starts
`/accounts/`) — that's what the frontend's login button points at.

### 1.2 Auth gate

Every other `/api/` endpoint below requires a session. Confirm the gate
itself works before testing what's behind it:

```
GET /api/presets/ (no session) -> 403
```

Then create a session (log in as a throwaway superuser via
`Client.force_login(user)` in a shell, or through `/accounts/login/` with
real credentials) and confirm:

```
GET /api/me/ (logged in) -> 200 {"authenticated": true, "id", "username", "email"}
```

### 1.3 Presets

```
GET /api/presets/            -> 200, list covering all 3 modes (t2v/i2v/r2v),
                                 each with is_draft true and false variants and
                                 a non-empty nested "durations" list
GET /api/presets/?mode=t2v   -> 200, only t2v presets
GET /api/presets/?mode=bogus -> 400
```

If the list is empty, the seed migration
(`generation/migrations/0009_seed_resolution_duration_catalog.py`) didn't
run — that's a setup problem, not a code problem; re-check §0.

### 1.4 Queue estimate

```
GET /api/queue-estimate/                          -> 200, seconds_ahead reflects
                                                       current backlog, additional_seconds: 0
GET /api/queue-estimate/?duration_id=<a real id>  -> 200, additional_seconds equals
                                                       that duration's estimated_render_seconds,
                                                       total_seconds = seconds_ahead + additional_seconds
GET /api/queue-estimate/?duration_id=999999       -> 404
```

### 1.5 Prompt-assist (skip if `llm_enabled` is `false`)

Chat is a single stateless endpoint (no session id, no DB persistence
during the live conversation at all — see `ARCHITECTURE.md`'s "LLM
integration") — `history` is JSON-encoded and sent by the *caller* each
time, matching what the frontend already keeps in React state:

```
POST /api/prompt/refine/ {mode, raw_prompt}                       -> 503 when llm_enabled=false
POST /api/prompt/refine/ {mode: "bogus", raw_prompt: "x"}          -> 400
POST /api/prompt/chat/ (multipart) {mode, history: "[]", content}  -> 200 {role: "assistant", content}
POST /api/prompt/chat/ {mode, history: "not json", content}        -> 400
```

Confirm no `PromptChatSession`/`PromptChatMessage` rows get created by the
chat call above at all (check via shell) — that's the point of it being
stateless. Then confirm the *other* half of the persistence design: queue
a job (§1.6) with a `chat_transcript` field (JSON-encoded array of
`{role, content}`) attached, and confirm exactly one `PromptChatSession`
now exists, already linked via `resulting_job` to that job, with all
transcript messages persisted — chat only ever reaches the DB by riding
along with an actual job creation, never on its own.

If `llm_enabled` is `true` and you don't want to spend a real LLM call,
mock `requests.post` (see the dry-run pattern from earlier passes: patch
`integrations.llm._post_chat_completion`'s underlying `requests.post`) —
the point of this check is the Django plumbing (auth, persistence-timing,
503 gating), not the LLM's actual output quality.

### 1.6 Job creation (all three modes)

For each mode, `POST /api/jobs/` as multipart form data and confirm `201`
with a `GenerationJob` body (`status: "queued"`, `expected_finish_time` set,
`references` matching what you sent):

- **t2v**: `{mode: "t2v", duration_id, aspect_ratio, raw_prompt}` — no files.
- **i2v**: `{mode: "i2v", duration_id, aspect_ratio, raw_prompt}` +
  `reference_images` = 1 file (first frame only) — confirm
  `references[0].label == "Picture 1"`. Repeat with 2 files (first + last
  frame).
- **r2v**: `{mode: "r2v", duration_id, aspect_ratio, raw_prompt}` +
  `reference_images` = 2-3 files — confirm each gets `"Picture N"` labels in
  order. Also, separately, `reference_audio` = 1-3 files — confirm each gets
  `"Audio N"` labels; t2v/i2v reject any `reference_audio` (max is 0 for
  both).

Also confirm the created job's `width`/`height` match
`resolution.compute_resolution(preset.megapixels, aspect_ratio)` and that
`megapixels`/`duration_seconds` on the job match the `RenderDuration`/
`RenderPreset` rows `duration_id` pointed at (all snapshotted at creation
time, not just reachable via `duration_id`).

Also confirm the reference-count caps are enforced server-side (not just in
the frontend):

```
POST /api/jobs/ {mode: "t2v", ...} + 1 reference_images file   -> 400 (t2v allows 0 images)
POST /api/jobs/ {mode: "i2v", ...} + 3 reference_images files  -> 400 (i2v allows at most 2 images)
POST /api/jobs/ {mode: "r2v", ...} + 10 reference_images files -> 400 (r2v allows at most 9 images)
POST /api/jobs/ {mode: "i2v", ...} + 1 reference_audio file    -> 400 (i2v allows 0 audio refs)
POST /api/jobs/ {mode: "r2v", ...} + 4 reference_audio files   -> 400 (r2v allows at most 3 audio refs)
```

**Test at least one reference upload with a realistically-sized file (a
few MB, not a 1KB placeholder) through nginx specifically** — i.e. a real
`curl`/browser request to `http://localhost:8080/api/jobs/`, not just
Django's test `Client` (which talks to the view directly and never goes
through nginx at all). This isn't redundant with the size-cap checks above:
nginx's own `client_max_body_size` (`frontend/nginx.conf`) is a completely
separate limit in front of Django, and a real bug here — nginx's 1MB
default rejecting every reference upload with a `413`, before the request
ever reached Django — was invisible to every `Client`-based check in this
section and only showed up as a real submission failure. Any check that
only exercises Django directly can't catch an nginx-layer misconfiguration
like this.

### 1.7 Job listing/detail/delete + cross-user isolation

```
GET /api/jobs/               -> 200, only the logged-in user's own jobs, each
                                 including raw_prompt (list-level, not detail-only)
GET /api/jobs/<id>/           -> 200, full detail incl. raw_prompt/improved_prompt/
                                  error_message/references (list view omits the
                                  latter three)
GET /api/jobs/<id>/ (as a *different* user) -> 404
```

Deletion:

```
DELETE /api/jobs/<id>/ (status=processing)          -> 409, job untouched
DELETE /api/jobs/<id>/ (status=queued or done)      -> 204, row + its reference/
                                                        video files actually gone
DELETE /api/jobs/<id>/ (as a *different* user)      -> 404
```

If testing via Django's test `Client` from `manage.py shell` rather than
real `curl`/browser requests, use `Client(SERVER_NAME='localhost')` —
the default `testserver` host isn't in `DJANGO_ALLOWED_HOSTS`, so every
request 400s with a generic Django error page that looks like a real bug
but isn't one (hit this for real writing this check).

### 1.8 FIFO / serialized processing (the part worth actually re-verifying,
not just trusting the code comments)

This works whether or not ComfyUI is reachable — an unreachable ComfyUI just
means each job fails fast, which is fine for a timing/ordering check:

1. Queue 3-4 jobs back to back (§1.6, any mode, cheap/draft preset).
2. Poll `GET /api/jobs/` every second or two until every job's
   `status == "done"`.
3. Confirm from the timestamps:
   - `started_at` order matches creation order (strict FIFO — no job starts
     out of turn).
   - No overlap: every job's `finished_at` is `<=` the next job's
     `started_at` (never two jobs `processing` at once).
   - Each job's `expected_finish_time`, captured **at creation time**,
     roughly equals the previous job's `expected_finish_time` plus this
     job's own `estimated_seconds` (small variance is fine; it's a live
     estimate, not a promise). A job created when the queue is empty may
     legitimately show `expected_finish_time: null` in its create response
     if it finishes before that same request's own lookup runs (only
     possible with near-instant failures, i.e. ComfyUI unreachable — not a
     bug, see `ARCHITECTURE.md`'s "Verification" section).

### 1.9 Invite management (staff only)

`GET`/`POST /api/invites/` and `DELETE /api/invites/<id>/` are gated by
`IsAdminUser` (`request.user.is_staff`), separate from the general session
auth gate in §1.2:

```
GET /api/invites/  (non-staff, logged in)      -> 403
POST /api/invites/ (non-staff, logged in)      -> 403
DELETE /api/invites/<id>/ (non-staff)          -> 403
GET /api/invites/  (staff)                     -> 200, list newest-first
POST /api/invites/ {expires_in_days: 7} (staff) -> 201, expires_at set to
                                                    ~7 days from now (server-
                                                    computed, not trusted
                                                    from the client)
DELETE /api/invites/<id>/ (staff)              -> 204, row actually gone
```

Also confirm `GET /api/me/` includes `is_staff: true`/`false` matching the
logged-in user — the frontend's Admin nav link and `/manage` client-side
redirect both key off this field (the real gate is still `IsAdminUser`
above, this is UX only).

### 1.10 Quality/duration catalog management (staff only)

`generation/admin_api.py`'s 6 endpoints, also `IsAdminUser`-gated. Only
the `sort_order` field on `RenderPreset` is schema-backed (migration
`0013`) — a "quality level" is still every `RenderPreset` row sharing one
`label` across modes; "removing" anything is always `is_active: false`,
never a real delete (both models are `PROTECT`ed by `GenerationJob`).
Labels aren't fixed — the live catalog's original "Standard" has already
been renamed to "Lowest" through real use of this tool, so don't assume
any specific label name still exists; use `GET /api/quality-catalog/`
first to pick a real one for `copy_durations_from`/etc. below.

```
GET /api/quality-catalog/ (non-staff)  -> 403
GET /api/quality-catalog/ (staff)      -> 200, "levels" has one entry per
                                           distinct label (6 in the seeded
                                           catalog), each with a "modes"
                                           dict; "durations" has one entry
                                           per distinct duration_seconds
POST /api/quality-levels/
  {label, is_draft, modes: {t2v: {megapixels, steps}},
   copy_durations_from: "<an existing label>"}     -> 201; the new
                                           preset's durations were
                                           actually cloned from the source
                                           label's active ones (check via
                                           the ORM, not just the response)
POST /api/quality-levels/ {label: "<existing label, different case>", ...}
                                        -> 400 (case-insensitive collision)
PATCH /api/quality-levels/<label>/
  {new_label: "..."}                   -> 200, every RenderPreset row
                                           under the old label renamed in
                                           one call, old label now 404s
PATCH /api/quality-levels/<label>/
  {modes: {t2v: {is_active: false}}}   -> 200, that mode's preset
                                           deactivated; reactivating it
                                           the same way brings it back
PATCH /api/quality-levels/<label>/
  {modes: {i2v: {megapixels, steps}}}  -> 200, creates that mode's preset
                                           if the level didn't have one
PATCH /api/quality-durations/<seconds>/
  {targets: [{label, mode, is_active: false}]}         -> 200, that
                                           (label, mode)'s RenderDuration
                                           row deactivated (or no-op if it
                                           never existed)
PATCH /api/quality-durations/<seconds>/
  {targets: [{label, mode, is_active: true}]}           -> 200 if a row
                                           already existed for that target
                                           (reuses its prior
                                           estimated_render_seconds), else
                                           400 (activating a duration with
                                           no prior row requires
                                           estimated_render_seconds)
PATCH /api/quality-durations/<new-seconds>/
  {targets: [{label, mode, is_active: true,
              estimated_render_seconds: N}]}            -> 200, a brand
                                           new RenderDuration row created
                                           (this endpoint upserts — no
                                           separate "create a duration
                                           value" endpoint exists)
PATCH /api/quality-levels/<label>/ {sort_order: 99}    -> 200, every row
                                           under that label gets
                                           sort_order=99 directly
POST /api/quality-levels/reorder/
  {order: [<every current label, shuffled>]}            -> 200,
                                           GET /api/quality-catalog/'s
                                           "levels" now reflects the new
                                           order; a list missing/adding a
                                           label -> 400 naming which
POST /api/quality-durations/estimate/
  {mode}                                                 -> 200; pools
                                           completed jobs across EVERY
                                           quality level of that mode, not
                                           one preset -- workload =
                                           job.steps * job.megapixels *
                                           job.duration_seconds is the
                                           shared x-axis. fit_available:
                                           false with sample_count/
                                           distinct_workloads if fewer
                                           than 2 distinct workload values
                                           exist among completed jobs for
                                           that mode -- seed 2+ synthetic
                                           GenerationJob rows (status=
                                           "done", a real video_file name,
                                           started_at/finished_at set,
                                           steps=preset.steps set
                                           explicitly) spanning 2+ presets
                                           of that mode to get
                                           fit_available: true, model:
                                           "linear", a sane slope/
                                           intercept, "samples" (one per
                                           completed job used) and
                                           "estimates" spanning every
                                           level's every duration (not
                                           just one preset's)
POST /api/quality-durations/estimate/
  {mode}  (8+ points, an obvious kink       -> model: "piecewise",
   in workload)                               a sane breakpoint_workload,
                                               segment_low/segment_high
                                               each with their own
                                               intercept/slope
POST /api/quality-durations/estimate/
  {mode, apply: true}                                    -> 200, every
                                           RenderDuration row that already
                                           existed for ANY level of that
                                           mode has its
                                           estimated_render_seconds
                                           updated to the fitted value
                                           (check via the ORM); confirm no
                                           new RenderDuration rows were
                                           created and no is_active values
                                           changed
```

**Before testing `apply: true` against a real mode**: snapshot every
`RenderDuration.estimated_render_seconds` for that mode to a file first
(`{d.id: d.estimated_render_seconds for d in RenderDuration.objects.
filter(preset__mode=mode)}`, copied out of the container, not just to
`/tmp` where a container restart loses it) and restore from that exact
snapshot afterward. `apply` is necessarily mode-wide (it has to touch
every level to be useful), so testing it against a real mode's real data
without a snapshot **will** contaminate real catalog numbers with
synthetic test data — this actually happened once (documented in
`todo.md`'s twenty-first pass) and had to be recovered from the
documented seed formula rather than an exact backup. Prefer testing
against a mode with no real completed-job history if one exists, or take
the snapshot regardless.

**Real bug to watch for if this ever needs re-touching**: don't compute
distinct duration values via `RenderDuration.objects.values_list(
"duration_seconds", flat=True).distinct()` -- `RenderPreset.Meta.
ordering` bleeds into the JOIN's implicit `ORDER BY`, and Postgres then
requires those columns in the `SELECT` list for `DISTINCT` to be valid,
silently returning one row per `(duration_seconds, sort_order, mode,
megapixels)` combination instead (342 rows instead of 19, in the seeded
catalog) -- `.count()` on the same queryset looks correct even when
iterating it doesn't, so check the actual row count, not just `.count()`.
Both `_serialize_catalog()` and the estimate endpoint dedupe in Python
over already-fetched values instead, which sidesteps it.

Frontend check (§2 below) covers the UI on top of this — the API checks
here are enough on their own to confirm the batch semantics (clone-on-
create, rename-updates-every-mode, reactivate-reuses-prior-estimate,
upsert-on-new-duration, reorder-matches-exact-label-set) work independent
of any rendering concerns.

### 1.11 Cleanup

Delete the throwaway user (cascades to their `GenerationJob`/
`ReferenceAsset`/`PromptChatSession` rows) and any `RenderPreset` you
created solely for testing. Don't leave test data in a shared environment.
(Individual test jobs can also be cleaned up one at a time via
`DELETE /api/jobs/<id>/`, §1.7 above, rather than deleting the whole user —
useful if you want to keep the throwaway user around across runs.)

## 2. Frontend check (real browser)

Requires an actual browser session (Playwright or by hand) — I don't have
direct browser tooling in every context, so either drive this manually or
via an agent/skill that does (this project's `run` skill, if invoked,
handles exactly this). Point it at `http://localhost:8080/` (or whatever
`frontend`'s published port is).

1. **Logged out**: `/` shows the login screen, not a blank page or an
   error. If `oidc_enabled` is `false`, no OIDC button renders — just the
   `/accounts/login/` link. If `true`, the OIDC button is present and its
   label matches `oidc_provider_name`.
2. **Login**: via `/accounts/login/` (admin-created account) or the OIDC
   button. Lands back on the SPA — **not** a 404 at `/accounts/profile/**`
   (a real bug hit and fixed in an earlier pass; regressions here are easy
   to reintroduce by touching `LOGIN_REDIRECT_URL` in `config/settings.py`).
3. **Nav**: logged in shows the page title/brand and a "Log out" link with
   the username. A staff user (`is_staff: true`) additionally sees
   "Generate"/"Admin" nav links; a non-staff user sees neither (Generate
   and the queue are one persistent layout at `/`, not a separate page, so
   there's nothing to link to until Admin exists). Confirm a non-staff user
   who manually navigates to `/manage` gets redirected back to `/` and
   never sees the Admin link render.
4. **Generate screen** (`/`, page `<title>` is "Minimax H3 Generator"):
   - **Content-type tabs**: `Video` shown selected; `Image`/`Audio` present
     but disabled (no such generation pipeline exists — this is intentional,
     see §3 below, not a bug).
   - **Mode tabs** (t2v/i2v/r2v): switching changes which reference-upload
     UI shows below (none / two explicit slots / dynamic add-list).
   - **Toolbar** (quality/aspect-ratio/length): all three load and
     auto-select sane defaults (a non-draft quality tier,
     `default_aspect_ratio` from `/api/config/`, the slider's own default
     duration); the length slider's available steps update when the quality
     tier changes (each tier has its own `durations` list); the displayed
     estimate below the form updates when quality, ratio, or length
     changes.
   - **Prompt** is the visually dominant fieldset (larger textarea,
     autofocused). If `llm_enabled`, "AI refine" and "Chat with AI" are
     visible on it; if not, neither is.
5. **Reference flow**:
   - i2v: attach a first-frame image, optionally a last-frame image — each
     shows a local thumbnail preview once picked.
   - r2v: add 2+ reference images via the dynamic list (each gets a small
     thumbnail), use "Insert token" on one and confirm it writes
     `<Picture N>` into the prompt textarea at the cursor, then remove one
     and confirm the list updates. Separately, add 1+ reference audio files
     via their own dynamic list (filename shown, no thumbnail) and confirm
     "Insert token" writes `<Audio N>`.
6. **Submit**: fill a prompt, pick quality/ratio/length, submit (see §0's
   note on this potentially reaching real ComfyUI if it's reachable). No
   modal, no navigation — the form just resets and the job shows up in the
   sidebar (step 7) on its own; submitting must **not** pop anything up.
7. **Queue sidebar + job modal**: the always-visible sidebar on the right
   shows the new job with a title derived from its prompt (first ~40
   chars), and its status updates without a manual refresh. Clicking an
   entry opens its modal: prompt, resolution/length, render time, and —
   once `done` with a `video_url` — the video playing inline (not a 404 —
   `/media/` must be reachable through nginx, see `ARCHITECTURE.md`'s
   "Verification" section for the bug this used to have) plus a working
   Download link. A `done` job with no `video_url` (i.e. it errored) shows
   a red "Failed" state, not a successful one, and the modal's own
   `error_message` text explains why. The sidebar entry itself also gets a
   small video thumbnail once done. Confirm **Delete** removes the entry
   from the sidebar and closes the modal (disabled with an explanatory
   tooltip while the job is `processing` — matches the API's 409, see
   §1.7). Confirm **Redo** repopulates the Generate form's mode/ratio/
   prompt/AI-refined-prompt/length *and* re-attaches its reference images
   and audio (re-fetched from their own media URLs and restored into the
   right slots/order — i2v's first/last frame, r2v's image and audio
   lists, thumbnails included) — this used to be a documented limitation
   but is now expected to actually work; a job with references that come
   back empty after Redo is a real bug, not the known non-failure it once
   was.
8. **Console**: zero unexpected errors in the browser dev console across
   all of the above (network 4xx/5xx you *intentionally* triggered while
   testing validation don't count).
9. **Admin: Invites tab** (`/manage/invites`, staff only — see step 3,
   default landing tab for `/manage`): create an invite (email optional,
   expiry Never/7/30 days), confirm it appears in the list with an
   "Active" status badge and the expected created/expires metadata. "Copy
   link" writes `<origin>/invite/<token>/` to the clipboard (shows
   "Copied!" briefly). "Revoke" removes it from the list immediately
   regardless of redeemed state. Empty list shows the empty-state message,
   not a blank area.
10. **Admin: Quality & Duration tab** (`/manage/catalog`): confirm the
    quality-levels table shows one row per distinct label, each with a
    two-row header (`Label`/`Draft` spanning both, then `MP`/`Steps`/
    `Active` sub-columns per mode) and every sub-column's body cell
    aligned directly under its header — not stacked in a single column (a
    real bug here is documented in `todo.md`'s nineteenth pass, caused by
    `display: flex` on a `<td>` breaking table layout; if it recurs the
    symptom is every mode's data appearing under the first mode column
    with the others empty — verify with `getBoundingClientRect()` on
    header vs. body cells, not just a screenshot). Rename a level inline
    (blur commits it, every mode's row renamed together). Toggle a mode's
    "active" checkbox off — it must visually uncheck **immediately** on
    click, not lag or silently revert (the checkboxes here use local
    optimistic state for exactly this reason, see `todo.md`'s nineteenth
    pass). **Reorder**: click a row's ▲/▼ buttons and confirm adjacent
    rows swap and the new order survives a reload; drag a row's ⠿ handle
    onto another row and confirm the same — this is also the order the
    quality dropdown uses on the Generate screen (`/`), worth spot-checking
    too. In the durations table, toggle a cell off then on for an existing
    duration/level/mode combination and confirm the estimate persists
    across a reload; use "Add a duration value" with a range like "21-25"
    and confirm the new rows appear immediately client-side (before any
    are actually saved), then check one cell to confirm it becomes a real
    persisted row. Use "Add quality level" to create one spanning at least
    one mode with "copy durations from" set, and confirm it isn't born
    with a "0 durations" warning badge. Click one of the three mode
    buttons in the "Estimate from completed jobs" toolbar above the
    durations table and confirm the (wider, `.modal-wide`) estimate modal
    opens showing either a real fitted curve/charts or a clear "not
    enough completed jobs yet" message (most modes won't have 2+ distinct
    workload values without seeding synthetic completed jobs first across
    2+ presets, see §1.10 — expected, not a bug). When data exists,
    confirm: both SVG charts render points at sane, in-viewBox coordinates
    (check via the DOM — `circle` `cx`/`cy` attributes — not just a
    screenshot, the same discipline that caught the `display:flex`-on-`
    <td>` bug); points use more than one color when multiple levels are
    represented; the legend and the Level/Duration/Current/Fitted table
    both list multiple levels, not just one; "Apply" round-trips into the
    durations table after a reload. **Read the snapshot-before-testing
    warning in §1.10 before clicking Apply against a mode with real
    history** — it mutates real catalog rows for every level of that
    mode.
11. **Mobile viewport** (~390×844): repeat steps 4-7 and, for a staff
    user, 9 (the Quality & Duration tab is a dense desktop-oriented power
    tool and is deliberately out of scope for mobile polish). Specifically
    confirm: the mode tab strip scrolls horizontally within itself rather
    than overflowing the page; the nav wraps the username/logout onto its
    own row rather than pushing it off-screen
    (`document.documentElement.scrollWidth` should equal the viewport
    width — a real bug here, caught by exactly this check, is documented in
    `todo.md`'s eighteenth pass); the job modal is usable as a near-full-
    screen sheet, not a cramped dialog with wasted margin.

## 3. Known-expected non-failures

- **A queued job ends `done` with `error_message` set, no `video_url`** —
  correct behavior when ComfyUI isn't reachable at the configured
  `COMFYUI_BASE_URL`, or a submitted job is otherwise invalid on ComfyUI's
  side. Only worth investigating if ComfyUI *is* confirmed reachable (§0)
  and the failure message doesn't explain itself.
- **`expected_finish_time: null` on a job's create response** — see §1.8,
  point 3. Only a bug if it happens with a real (non-instant) render.
- **No OIDC button** — expected whenever `OIDC_CLIENT_ID` is unset in
  `.env`; not a bug, see `ARCHITECTURE.md`'s "Backend apps" section.
- **`Image`/`Audio` content-type tabs are disabled and do nothing when
  clicked** — expected; no image/audio-only generation pipeline exists in
  this backend yet, see `ARCHITECTURE.md`'s "Deferred" section.

## 4. Real ComfyUI render (optional, separate from the rest of this check)

Everything above is safe to run any time — nothing in §1-3 spends real GPU
time even when ComfyUI is reachable, since job *creation* always succeeds
regardless of whether the eventual render does. Actually letting a job reach
ComfyUI and render for real is the one thing genuinely worth calling out
separately: it costs real time on real hardware and, per
`ARCHITECTURE.md`'s benchmarking notes, an oversized combination has been
observed to crash the ComfyUI process itself. Confirm `COMFYUI_BASE_URL`
points at a real, currently-reachable instance (§0), pick the cheapest
available preset (`is_draft: true`), and get an explicit go-ahead before
queuing it — don't fold this into a routine function check by default.
