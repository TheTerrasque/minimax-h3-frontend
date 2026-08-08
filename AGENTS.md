# Notes for coding agents working in this repo

Operational knowledge that isn't obvious from the code alone — mostly
hard-won during actual sessions in this repo. Keep this current: add to it
when something costs real back-and-forth to figure out, prune it when it
goes stale.

## Docker: nothing is bind-mounted

`backend/Dockerfile` `COPY`s `backend/` and `resources/` in at build time;
`frontend/Dockerfile` does an `npm run build` at build time. **A code
change is invisible to the running stack until its image is rebuilt** —
editing a file and running `docker compose exec backend python manage.py
whatever` runs the *old* code.

- **`backend`, `qcluster`, and `migrate` each build their own separate
  image**, even though all three share `backend/Dockerfile` and the same
  build context — Compose does not dedupe this. `docker compose build
  backend` alone does **not** rebuild `qcluster` or `migrate`. After any
  backend change: `docker compose build backend qcluster migrate` (add
  `frontend` too if frontend changed), then `docker compose up -d`. Forgot
  `migrate` once this session and a whole migration silently ran against
  stale code with `Unknown command` / stale schema — always list all four
  explicitly rather than relying on Compose to infer what changed.
- `docker compose up -d` recreates containers from whatever images/config
  currently exist *and* re-runs the one-shot `migrate` service.
- **Already-applied migrations don't re-run**, even if you edit the
  migration file afterward (Django tracks `(app, name)` in
  `django_migrations`, not file content). To force a specific migration to
  actually re-execute (e.g. you fixed a bug in a data migration's body
  after it already "succeeded" but did nothing useful): unapply then
  reapply it explicitly —
  `docker compose exec backend python manage.py migrate <app> <migration_before_target>`
  then `... migrate <app> <target>`.
- **The one-shot `migrate` service may not have the same volume mounts as
  `backend`/`qcluster`** (it didn't have `media_data` until this was hit
  for real — see `docker-compose.yml`'s `migrate` comment). A data
  migration that reads/writes actual media files can fail with a
  misleading "file not found" for files that genuinely exist, just not
  inside *that* container. Prefer writing such logic as a reusable,
  idempotent management command (e.g. `backfill_thumbnails`) that can be
  run manually against `backend`/`qcluster` (which do have real media
  access), with the migration itself only calling it best-effort and never
  blocking the deploy if it can't run there.
- This is the repo owner's own dev/test stack, not a production
  deployment they've asked to be treated cautiously — confirmed explicitly
  once already this history. Default to just rebuilding + redeploying once
  changes are verified locally, without asking first each time. If a
  future session has reason to believe a deployment *is* meant to be
  treated as live/shared, that overrides this note — ask.

## Git

- **Commit after each logical chunk**, not one giant commit at the end —
  this session's history is a good model: one commit per feature/fix
  (extras toggle, docs reorg, the six `frontend fixes.txt` items, the
  thumbnail-scaling fix, the title-length fix all landed separately).
- `frontend fixes.txt` (repo root) is the owner's personal, deliberately
  untracked scratch notes file — never stage it unless explicitly asked to.
  `git add -A -- ':!frontend fixes.txt'` (or just add paths explicitly)
  rather than a bare `git add -A`.
- Match the existing commit style: short imperative subject, a body that
  explains *why* (not a restated diff), `Co-Authored-By: Claude Sonnet 5
  <noreply@anthropic.com>` trailer.

## Backend dev loop

- This repo uses `uv`, not a bare `pip`/venv — plain `python` isn't on
  `PATH` here, but the Windows `py` launcher and `uv` itself are. Still run
  backend commands as `cd backend && uv run python manage.py <command>`,
  not `py`/a bare `python` — `uv run` is what actually puts this project's
  own `.venv` (and its installed deps) on the path; `py` alone would hit
  whatever Python it defaults to instead.
- **No live DB from the host shell** — `DB_HOST=db` only resolves inside
  the Compose network, so `manage.py migrate`, `migrate --plan`, etc. fail
  locally with `failed to resolve host 'db'`. What *does* work locally
  (no DB needed): `manage.py check`, `manage.py makemigrations` /
  `makemigrations --check --dry-run`, and any `SimpleTestCase`-based test.
  Anything that needs the real DB or real media files: run inside the
  container, `docker compose exec backend python manage.py ...`.
- `makemigrations app1 app2 --name X` applies the *same* `--name` to every
  app touched in that invocation — produces a misleadingly-named file for
  whichever app the name doesn't actually describe. Check the generated
  filenames and rename before committing (content is unaffected, only the
  filename).

## Frontend dev loop

Before calling frontend work done: `npx tsc --noEmit` (type-check),
`npx oxlint` (lint — silent output means clean), `npm run build`
(`tsc -b && vite build`). All three, every time.

## Gotchas worth knowing before you hit them

- **`backend/generation/media_views.py::_owner_id_for_path`** hardcodes
  recognized `MEDIA_ROOT` path prefixes (`generated_videos/`,
  `thumbnails/`, `references/`) to look up who owns a file for the
  protected-media view. Add a new `FileField` with a new upload directory
  and forget this, and its URLs 404 for *everyone*, including the file's
  owner — silently, no error anywhere obvious. Always add a matching
  branch here when adding a new `FileField`/`upload_to`.
- **ComfyUI's `GET /object_info/<class_type>` never 404s** — an unrecognized
  node type still returns `200 {}`. Check whether the body is empty, not
  the status code, when probing whether a custom node is actually
  installed (see `integrations/comfyui.get_object_info`).
- Bash tool + this repo's path: the working directory has a space
  (`.../AI stuff/MinimaxH3 front`) — always `cd` with the full path
  double-quoted in one go rather than a bare relative `cd backend &&` from
  an assumed cwd.

## Where things live

- `README.md` — user-facing setup/config.
- `docs/ARCHITECTURE.md` — how/why the system is built the way it is;
  has a "Deferred" section for known-not-built-yet items.
- `docs/FUNCTION_CHECK.md` — manual end-to-end verification procedure.
- `docs/extras.md` — optional third-party ComfyUI custom-node integrations
  (`COMFYUI_EXTRAS` env var).
- This codebase has a strong convention of dense, *why*-focused inline
  docstrings/comments that cross-reference other files by name (e.g. "see
  `ARCHITECTURE.md`'s Deferred section") — match that style in new code
  rather than comments that just restate what the code does.
- **Keep these docs updated as part of the change that needs it**, not as
  a followup — a new feature, config option, endpoint, or gotcha belongs in
  the relevant doc (and this file, if it's operational) in the same commit,
  same as the code-level cross-referencing above. A stale README/
  ARCHITECTURE.md is worse than none, in a repo this deliberate about
  cross-references actually being accurate.

## Before calling something done

- Backend: `manage.py check`, `makemigrations --check --dry-run`,
  `manage.py test` (all runnable locally, no Docker needed).
- Frontend: tsc + oxlint + build (see above).
- Anything that touches the *running* stack's behavior (new migration, new
  env var, new endpoint, new management command): actually rebuild +
  redeploy and verify against the live containers — curl the endpoint,
  query via `manage.py shell`, check `docker compose logs` — rather than
  stopping at "the diff looks right."
