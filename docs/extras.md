# ComfyUI extras

Optional third-party ComfyUI custom-node integrations for the MiniMax H3
workflows this project drives. These aren't part of ComfyUI's official
day-0 MiniMax H3 support (see the [Required ComfyUI
models](../README.md#required-comfyui-models) section of the README) — they're
independent community projects that add extra nodes on top of it.

**These are unaudited third-party Python packages that ComfyUI loads and
executes.** Review a project's source (not just its README) before installing
it into your ComfyUI instance, the same way you would for any other
`custom_nodes` package. This project verified that each repo below is real
and active (cross-checked against an independent
[`awesome-minimax-H3`](https://github.com/wildminder/awesome-minimax-H3) list
and, for Spectrum, an independent
[comfyui-wiki.com news post](https://comfyui-wiki.com/en/news/2026-08-03-comfyui-spectrum-minimax-h3))
but has not audited any of their code.

**Spectrum** and **Contex Loop** (the latter backing Director Mode's clip
continuation, not a `COMFYUI_EXTRAS` toggle — see
[below](#contex-loop--integrated-director-mode)) are actually wired into
this app; Turbo and the older, separate Motion Context project are
documented for reference but not integrated — see [Why only one
COMFYUI_EXTRAS extra is wired up right
now](#why-only-one-comfyui_extras-extra-is-wired-up-right-now).

## Configuration

One env var, `COMFYUI_EXTRAS` (see `.env.example`), comma-separated
`slug` or `slug=N` tokens:

| Level | Meaning |
|---|---|
| *(slug absent)* | Off — not offered at all. |
| `slug` or `slug=0` | Optional — a toggle is shown to the user, unchecked by default. |
| `slug=1` | Optional — a toggle is shown, checked by default. |
| `slug=2` | Forced — always applied to every job, no toggle shown, not overridable per job. |

Only `spectrum` does anything right now, e.g. `COMFYUI_EXTRAS=spectrum=1`.
The level is enforced server-side (`generation/api.py::_resolve_use_spectrum`)
regardless of what a client sends.

### Checking what's actually installed

There's no live status page (see [Why only one extra is wired up right
now](#why-only-one-extra-is-wired-up-right-now)) — check from the CLI
instead, after setting `COMFYUI_EXTRAS` and before relying on it for a real
render:

```sh
docker compose exec backend python manage.py check_extras
```

For each configured extra, this hits the real ComfyUI instance's
`GET /object_info/<class_type>` and reports whether its node is actually
installed (ComfyUI answers `200 {}` for an unknown node type — it never
404s — so this checks the body, not the status code; confirmed against a
real instance). Also flags a `COMFYUI_EXTRAS` slug this app doesn't
recognize (almost always a typo), and a clear message if ComfyUI itself
isn't reachable at all.

## Spectrum — integrated

**[xmarre/ComfyUI-Spectrum-MiniMax-H3](https://github.com/xmarre/ComfyUI-Spectrum-MiniMax-H3)**
— spectral feature forecasting that skips some MiniMax H3 transformer
evaluations during sampling, to render faster. This is the actual subject of
the linked Reddit "45% lower sampler time" post (community reports put the
real-world range closer to ~24–30% depending on hardware/settings — see
Tradeoffs below).

### What it does

Fits a Chebyshev ridge model to the model's own recent hidden-feature history
and *forecasts* that feature on selected future sampler steps instead of
running the real transformer — every other part of the step (output heads,
video/audio reconstruction, sigma mapping) still executes normally. It's an
approximation, not a lossless shortcut.

Adds one node: **Spectrum Apply MiniMax H3** (`sampling/spectrum` category,
class type `SpectrumApplyMiniMaxH3`), a `MODEL → MODEL` wrapper meant to sit
right after the model loader (`... → Load Diffusion Model → [LoRA, if any] →
Spectrum Apply MiniMax H3 → guider/sampler`).

### Tradeoffs / known issues (from the project's own README)

- **Not bit-identical to native sampling.** Forecasted steps change the
  denoising trajectory. Two effects have been observed in exact-seed A/B
  testing: *trajectory deviations* (motion/pose/timing can diverge during
  fast or brief actions) and *localized quality degradation* (eyes, fingers,
  fine detail can become malformed or unstable when moving quickly or
  briefly visible). Either can occur alone or together.
- **Sampler allowlist.** Forecasting only applies for Euler, RES multistep,
  and RES multistep CFG++. Ancestral samplers and multi-GPU parallel
  sampling always run native (noise injection / unvalidated forecast
  transactions respectively).
- **Incompatible with EasyCache/LazyCache on the same model branch** — if
  both are attached, Spectrum logs a warning and stays inactive for that run
  rather than double-accelerating.
- **VRAM cost for history.** With `history_storage=vram` (not the default),
  retaining `max_history` snapshots can be multiple GiB at typical
  resolutions — `system_ram` (the default) avoids this at some transfer
  overhead cost.
- Requires native ComfyUI MiniMax H3 support introduced at ComfyUI commit
  `e377e263049f9338b4d12a3dd417b36ae62948ff` or later (including the
  `latent_shapes` argument on `outer_sample`); older ComfyUI revisions aren't
  supported.
- Adds no third-party Python dependency — only PyTorch and ComfyUI modules
  already present in a normal install.

### Install (in ComfyUI)

```sh
cd ComfyUI/custom_nodes
git clone https://github.com/xmarre/ComfyUI-Spectrum-MiniMax-H3.git
```

Restart ComfyUI. The node appears under `sampling/spectrum` as **Spectrum
Apply MiniMax H3**.

### How this app wires it in

- `COMFYUI_EXTRAS=spectrum[=N]` (see [Configuration](#configuration)).
- Frontend: a checkbox (or a static "always on" note at level 2) on the
  Generate screen, driven by `GET /api/config/`'s `spectrum_level`
  (`frontend/src/features/generate/GenerateScreen.tsx`).
- Backend: `GenerationJob.use_spectrum` is resolved and snapshotted at job
  creation (`generation/api.py::_resolve_use_spectrum`), then
  `backend/integrations/spectrum.py::apply_spectrum()` splices the node into
  the API-format workflow at render time
  (`generation/tasks.py::build_api_workflow()`) — it finds the workflow's
  sole `UNETLoader` node, rewires every existing reference to its output to
  the new Spectrum node instead, and wires the new node's `model` input back
  to the loader.
- Default node parameters are the project's own "preliminary default
  preset" verbatim: `blend_weight=0.5, degree=1, ridge_lambda=0.10,
  window_size=2.0, flex_window=0.75, warmup_steps=1, tail_actual_steps=1,
  max_history=8, history_storage=system_ram, bootstrap_first_forecast=true`.
  Not exposed as a per-job or admin-tunable setting yet — see [Why only one
  extra is wired up right now](#why-only-one-extra-is-wired-up-right-now).
- **Not accounted for**: `GenerationJob.estimated_seconds` (the number shown
  before queuing and used for the cross-user queue ETA, see
  `generation/queue.py`) is computed purely from the chosen preset/duration
  and does **not** shrink when Spectrum is on — the real render will be
  faster than the quoted estimate. The UI caption says as much rather than
  faking an adjustment.
- **Verify before first use**: the literal ComfyUI node class name
  (`SpectrumApplyMiniMaxH3`) is taken from the linked README and hasn't been
  confirmed against a live `/object_info` in this session. If it's ever
  wrong (e.g. a future release renames it), `apply_spectrum()` still
  succeeds — it's just building a dict — but ComfyUI's `/prompt` validation
  will reject the job with a clear unknown-node-type error, surfaced as the
  job's `error_message` like any other bad workflow.

**Tested with:** *not yet pinned — record the ComfyUI + Spectrum commit
hashes you validate this against here.* The project's own README documents
compatibility against ComfyUI commits `e377e263049f9338b4d12a3dd417b36ae62948ff`
(introduction) and `0dd9b154a1654fc699dcdc3af066c7cce096045a` (native-
equivalence CI), plus a community report confirming revision
`dc6291525112cb4246f864738e5bb4e2b85446da` on Windows 11 / ROCm 7.2.1 /
ComfyUI 0.30.0 — none of that was independently re-verified here.

## Contex Loop — integrated (Director Mode)

**[ethanfel/ComfyUI-MiniMaxH3-Contex-Loop](https://github.com/ethanfel/ComfyUI-MiniMaxH3-Contex-Loop)**
— chains MiniMax H3 clips together so motion and audio continue across the
join, the same problem the older, separate
[Motion Context](#motion-context--documented-not-integrated) project below
addresses, from a different author. Backs **Director Mode**'s clip
continuation (`Clip.continues_previous`, see `ARCHITECTURE.md`) — unlike
every other extra on this page, it isn't a `COMFYUI_EXTRAS`/`EXTRAS_CONFIG`
toggle: there's no per-job checkbox, it's attempted automatically whenever
a Director clip is flagged as continuing the one before it, with a
graceful fallback when it isn't installed (see below).

### What it does

The full extension is actually two layers:

- Its own **Plan → Loop Start → ... → Review Gate → Loop End → Assemble**
  node pipeline (`chain_nodes.py`), which runs a whole multi-scene chain as
  *one* ComfyUI graph submission and includes an interactive human-in-the-
  loop Review Gate (approve/retry/reroll) needing a live browser session
  against ComfyUI itself. **Not used by this app** — it would fight
  Director Mode's own job queue, UI, and per-clip regeneration/review
  model.
- Four lower-level nodes (`nodes.py`) this app's `integrations/
  motion_context.py` *attempts* to use directly, the same way
  `integrations/spectrum.py` splices Spectrum into a template (find
  node(s), rewire references, insert): **MiniMaxH3MotionContext** (pins
  the previous clip's frames/audio as conditioning), **MiniMaxH3LoopTrim**
  (removes the duplicated leading frames/audio the context node causes),
  and a **MiniMaxH3MotionContextSaveLatent** / **LoadLatent** pair
  (persists the previous clip's AV latent to a safetensors file on
  ComfyUI's own disk). **See "Verified against a real install" below —
  three of these four are not actually usable this way.**

### Verified against a real install

Installed and checked live against this deployment's ComfyUI
(`GET /object_info/<class_type>` for each): only **MiniMaxH3LoopTrim** is
actually registered as a usable ComfyUI node. `MiniMaxH3MotionContext`,
`MiniMaxH3MotionContextSaveLatent`, and `MiniMaxH3MotionContextLoadLatent`
all return `{}` (ComfyUI's "not installed" signal) — they exist as plain
Python classes in the extension's `nodes.py` source, but its `__init__.py`
never registers them in `NODE_CLASS_MAPPINGS`, so they aren't reachable as
standalone splice targets at all. The extension's actual public API for
this capability is the higher-level `chain_nodes.py` pipeline instead —
confirmed registered: `MiniMaxH3ChainPlan`, `MiniMaxH3ChainScenePromptEditor`,
`MiniMaxH3ChainLoopStart`, `MiniMaxH3ChainCurrent`, `MiniMaxH3ChainContext`,
`MiniMaxH3ChainSegmentSave`, `MiniMaxH3ChainReview`, `MiniMaxH3ChainLoopEnd`.

**Practical effect**: `apply_motion_context()`'s full-continuity branch can
never succeed as written — ComfyUI's `/prompt` validation rejects the
unregistered node types every time (the same graceful "job.error_message,
not a crash" failure `apply_spectrum()`'s own docstring describes for a
renamed node). This isn't silently broken, though: `is_available()` checks
for that same unregistered `MiniMaxH3MotionContext` class, so it correctly
reports unavailable and the [graceful fallback](#graceful-fallback) below
always engages instead — verified with a real two-clip render (t2v scene
start → i2v `continues_previous` clip): the second clip's job carried the
first clip's last frame as its `first_frame` reference, exactly as
designed, with `keep_comfyui_output=False` and no `continuation_params`
attempted.

**Genuine full continuity needs a rewrite against `chain_nodes.py`'s real
API** — a materially bigger integration than the current node-splice
approach, not yet done:

- `MiniMaxH3ChainLoopStart(plan, start_clip, scene_range)` does support
  rendering exactly one scene per submission (`scene_range="3"` limits it
  to scene 3 alone, no `MiniMaxH3ChainLoopEnd`/recursion required) — so a
  one-job-per-clip model, matching this app's existing queue, is possible
  in principle.
- But every submission — even a single scene — needs a full `H3_CHAIN_PLAN`
  (from `MiniMaxH3ChainPlan`'s `plan_json`: `prompt_prefix`/`defaults`/a
  `shots` array), and `start_clip > 1` "loads and validates the preceding
  segment checkpoint" against that same plan (audio hash/fingerprint
  checks per `H3_CHAIN_FORMAT_GUIDE.md`) — a much more rigid, plan-
  validated cross-job bookkeeping contract than this app's current
  freeform `filename_prefix`+`clip_index` scheme.
- `MiniMaxH3ChainContext` takes an opaque `H3_CHAIN_STATE` (from Loop
  Start/Current), not a plain `context_frames`/`context_audio` pair — the
  video-loading/checkpoint-loading logic is encapsulated inside that state
  object, not directly wireable the way this app's current
  `video_ref.add_load_video_node()` approach assumes.
- `MiniMaxH3ChainSegmentSave` does its own H.264 encoding + checkpoint
  save together (not a bare latent save) — would likely replace this
  app's own `CreateVideo`/`SaveVideo` handling for a continuation clip,
  not just add alongside it.

Tracked as follow-up work, not attempted in this pass — see
`ARCHITECTURE.md`'s Deferred section.

### Install (in ComfyUI)

```sh
cd ComfyUI/custom_nodes
git clone https://github.com/ethanfel/ComfyUI-MiniMaxH3-Contex-Loop.git
```

Restart ComfyUI and hard-refresh the browser if you ever use its own UI.
Optional ffmpeg on PATH (falls back to PyAV) — irrelevant to this app's own
usage, which never touches that extension's own Plan/Assemble nodes.

### How this app wires it in (today — see caveat above)

- `integrations/motion_context.py::apply_motion_context()` would splice
  the nodes above into a Director clip's workflow at render time
  (`generation/tasks.py::build_api_workflow()`'s `continuation_params`
  kwarg, set by `director/services.py::_build_job_for_clip()`) *if* those
  nodes were reachable. The intended design: every Director-rendered clip
  gets at least a SaveLatent node (so a *later* clip has a checkpoint to
  continue from if it turns out to want one); a clip with
  `continues_previous=True` additionally gets the full MotionContext/
  LoopTrim splice, fed from the *previous* clip's own rendered video,
  referenced **directly on ComfyUI's own machine** (via `LoadVideo`'s
  `"name [output]"` folder-paths annotation, see `integrations/video_ref.py`)
  rather than downloaded and re-uploaded through Django.
  `GenerationJob.keep_comfyui_output`/`comfyui_output_filename`/
  `_subfolder` exist to support this. None of it currently activates in
  practice — see above.

### Graceful fallback

Director Mode never requires this extension to be installed — it's
detected live (`integrations/motion_context.py::is_available()`, a cached
`GET /object_info/MiniMaxH3MotionContext` check, also surfaced to the
frontend via `GET /api/config/`'s `director_full_continuity_available`)
and degrades automatically rather than failing or disabling continuation:

- **Not installed at all**, or **the previous clip was rendered before it
  was installed** (so its ComfyUI-side output was never kept around to
  reference): a `continues_previous` clip falls back to feeding the
  previous clip's **last frame** in as an ordinary image reference (i2v's
  `first_frame`, or r2v's first `<Picture N>`) instead of true motion/audio
  continuity — a much weaker technique (no audio carries over, and motion
  restarts from a single still frame rather than flowing continuously) but
  still a real visual anchor, and it needs nothing beyond what this app
  already keeps (the rendered `video_file` every job downloads regardless
  — see `integrations/media_post.py::extract_last_frame()`).
- **Self-healing**: this is re-checked on every render, not just once —
  install the extension mid-project and the *next* clip you render
  (continuation or not) automatically starts using full continuity again,
  no re-render of earlier clips required.
- Director Mode itself is never disabled by the extension being absent —
  every other capability (multi-clip sequencing, dirty-cascade re-render,
  shared project prompt/resources) works identically either way; only the
  quality of a `continues_previous` join degrades.

## Turbo — documented, not integrated

**[Larryvrh/ComfyUI-MiniMax-H3-Turbo](https://github.com/Larryvrh/ComfyUI-MiniMax-H3-Turbo)**
— a turbo LoRA + custom sampler that renders MiniMax H3 in **4 sampling
steps** instead of ~20.

### What it does

Two nodes, meant to drop into the official t2v/i2v workflow:

| Node | What it does |
|---|---|
| **MiniMax-H3 Turbo LoRA** | `MODEL → MODEL`, applies the [turbo LoRA](https://huggingface.co/larryvrh/MiniMax-H3-Turbo-Lora), inserted between the model loader and sampler. |
| **MiniMax-H3 Turbo Sampler (4-step)** | `→ SAMPLER`, replaces whatever feeds `SamplerCustomAdvanced`'s `sampler` input. |

MiniMax H3 denoises video and audio on two different flow schedules (video
shift 12, audio shift 3); ComfyUI's stock samplers step both on one
schedule, which over-steps (distorts) the audio at only 4 steps. The custom
sampler steps each stream on its own schedule instead, so audio stays clean.

### Tradeoffs / known issues

- **Preview-quality LoRA.** The current checkpoint (`ckpt850`) is described
  by its own authors as the final checkpoint of its training round — sharp,
  but with known artifacts (plastic-looking skin, over-sharp grain);
  training is paused pending a fix.
- **`low_vram` tradeoff**: off (default) applies the LoRA at runtime —
  sharpest, more peak VRAM; on merges it into the weights — lowest peak VRAM
  but softer results on quantized (`int8`/`fp8`/pruned) bases, since the
  small LoRA update partly rounds away when folded into quantized weights.
- LoRA `strength` is a sharpness/artifact dial: raise it (~1.05–1.2) if
  results look blurry/ghosted, lower it (~0.8–0.95) if over-sharp/grainy.
- Works with any MiniMax H3 base (full or pruned/curve-quantized) — detects
  a pruned base automatically and re-injects time-conditioning for it.
- Any step count ≥ 4 is valid (more helps a little); the whole point of the
  extension is using exactly 4, so combining it with a preset that doesn't
  also drop `steps` to 4 loses most of the benefit.

### Install (in ComfyUI)

Via ComfyUI-Manager (search "MiniMax-H3 Turbo"), or manually:

```sh
cd ComfyUI/custom_nodes
git clone https://github.com/larryvrh/ComfyUI-MiniMax-H3-Turbo
```

Then download the `.safetensors` LoRA from
[larryvrh/MiniMax-H3-Turbo-Lora](https://huggingface.co/larryvrh/MiniMax-H3-Turbo-Lora)
into `ComfyUI/models/loras/`, and restart.

### Why not yet integrated

The Spectrum toggle above is the first cut of this app's "extras" support,
deliberately kept minimal (a plain per-job boolean, no general plugin
registry) to see how that shape holds up in practice before adding a second
extra. Turbo is a reasonable next candidate — it's the same kind of
per-job workflow splice Spectrum already is (see
`backend/integrations/spectrum.py` for the pattern: find a node by
`class_type`, rewire references, insert) — but also needs a `steps=4`
override baked into wherever it hooks in, since it isn't useful without
also dropping the sampler step count, which the current
preset/duration model doesn't have a clean per-job override for yet.

## Motion Context — documented, not integrated

**[NikoDemon80/ComfyUI-H3-Motion-Context](https://github.com/NikoDemon80/ComfyUI-H3-Motion-Context)**
— chains MiniMax H3 clips together so motion **and audio** continue across
the join, instead of each clip re-deciding content from a single still
frame. The same underlying problem
[Contex Loop](#contex-loop--integrated-director-mode) above (a different,
later project) solves and which this app actually integrates — this one
remains just documented for reference, not integrated; no reason to run
both.

### What it does

H3's keyframe system tags frames with a time coordinate and re-injects them
at every sampling step; ComfyUI's stock implementation only allows keyframe
anchors at the first/last frame. This project lifts that restriction (self-
testing its own math against ComfyUI's at every startup) so a *run* of
consecutive frames from the end of one clip can anchor the start of the
next — and separately carries the previous clip's *audio* onto the new
clip's own timeline (rather than through H3's reference mechanism, which the
model treats as "a separate clip that sounds similar," not a continuation).

Four nodes: **H3 Motion Context** (feeds previous-clip frames/audio into
generation), **H3 Motion Context Trim** (removes the duplicated head
frames/audio before concatenating), and an **H3 Motion Context Save/Load
Latent** pair (carries the previous clip's *latent* across separate runs,
since ComfyUI won't let you wire a sampler's own output back into its next
run directly — "circular connection").

### Tradeoffs / known issues

- **Audio quality degrades down a chain.** Each clip's audio is regenerated
  from the previous clip's *output*, so — like photocopying a photocopy —
  losses compound; the top end goes first, so a long chain gets noticeably
  duller/muffled even though timing/tempo stay locked. Wiring the
  `context_latent` input (instead of just decoded `context_audio`)
  eliminates one of the two loss sources (an extra audio-VAE round trip) but
  not the model's own regeneration smoothing.
- **A small constant ~10ms audio offset**, below lip-sync perceptibility,
  that doesn't grow down the chain.
- **Narrow testing**: verified on two material types (dense electronic
  music, spoken word) on one Windows machine, one resolution, one sampler.
- **Incompatible with step-skipping optimizers** on the same graph — the
  README explicitly calls out disabling `ComfyUI-Spectrum-MiniMax-H3` for
  Motion Context graphs, since pinned rows never evolve, which is a
  degenerate case for Spectrum's forecaster. (Confirms the two extensions
  are not meant to be combined.)
- **Licensing caveat from the project's own README**: "The H3 community
  license reportedly does not currently cover the EU, UK, Korea, or the
  US. Verify independently before building anything shipping on this."
  Not verified here — treat as a pointer to check, not a legal conclusion.

### Install (in ComfyUI)

Drop the folder into `ComfyUI/custom_nodes/` and restart. Watch the console
for `h3_motion_context: interior keyframe anchors enabled` /
`h3_motion_context: keyframe/ref coexistence enabled` — if a self-test fails
instead, the node refuses to run rather than silently rendering something
wrong.

### Why not yet integrated

Unlike Spectrum and Turbo, this isn't a per-job workflow patch at all — it's
a **stateful, multi-job feature**: "continue this specific previous clip."
This app didn't originally support that shape of feature at all (no
"continue from job X" concept anywhere in the data model — `GenerationJob`
had no notion of a parent job — or UI); that gap is what Director Mode was
designed to fill (see `ARCHITECTURE.md`), using
[Contex Loop](#contex-loop--integrated-director-mode) above rather than
this project. Nothing rules out wiring this one in too later if Contex
Loop ever turns out to have a dealbreaker Motion Context doesn't, but
there's no reason to maintain two integrations of the same underlying
capability today.

## Why only one COMFYUI_EXTRAS extra is wired up right now

This project went through a few design passes on how much "extras"
infrastructure to build up front — preset-level configuration (rejected: it
would duplicate a preset row per quality-tier × extras combination),
then a fuller plugin registry with admin-tunable per-extra time-estimate
profiles. The decision was to hold off on generalizing until there's a
second real extra to generalize *from* — so this first cut is a single
purpose-built boolean (`GenerationJob.use_spectrum`) and a single splice
function (`integrations/spectrum.py::apply_spectrum`), not a registry. If
Turbo (or another extra) gets wired in next, that's the point to factor out
the shared shape (node-splice helpers, a real registry, per-extra admin
tuning, time-estimate adjustment) rather than guessing at it now.
