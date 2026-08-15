// Shapes returned by backend/generation/api.py and backend/accounts/api.py --
// see ARCHITECTURE.md's "Backend apps" section for what each field means.

export type Mode = "t2v" | "i2v" | "r2v" | "t2i" | "r2i" | "t2a" | "r2a";

export const MODE_LABELS: Record<Mode, string> = {
  t2v: "Video from text",
  i2v: "Provide first frame",
  r2v: "Provide references",
  t2i: "Image from text",
  r2i: "Provide references",
  t2a: "Audio from text",
  r2a: "Provide references",
};

// Which tab/output kind a mode belongs to -- see backend/generation/models.py's
// Mode docstring: image/audio modes reuse the video workflows and derive
// their actual output via ffmpeg, so despite the name GenerationJob.video_url
// isn't always a video -- content_type says which <video>/<img>/<audio> tag
// to render it with.
export type ContentType = "video" | "image" | "audio";

export const CONTENT_TYPE_BY_MODE: Record<Mode, ContentType> = {
  t2v: "video",
  i2v: "video",
  r2v: "video",
  t2i: "image",
  r2i: "image",
  t2a: "audio",
  r2a: "audio",
};

export const MODES_BY_CONTENT_TYPE: Record<ContentType, Mode[]> = {
  video: ["t2v", "i2v", "r2v"],
  image: ["t2i", "r2i"],
  audio: ["t2a", "r2a"],
};

// Only r2v/r2i/r2a actually take reference uploads (see backend
// generation/api.py's _MAX_REFERENCE_IMAGES/_MAX_REFERENCE_AUDIO below) --
// i2v is its own separate first/last-frame flow, not a "reference" one.
export const REFERENCE_FLOW_MODES: Mode[] = ["r2v", "r2i", "r2a"];

// Mirrors generation/api.py's _MAX_REFERENCE_IMAGES -- what tasks.py
// actually wires into the ComfyUI workflow per mode (see ARCHITECTURE.md).
export const MAX_REFERENCE_IMAGES: Record<Mode, number> = {
  t2v: 0,
  i2v: 2,
  r2v: 9,
  t2i: 0,
  r2i: 9,
  t2a: 0,
  r2a: 9,
};

// Mirrors generation/api.py's _MAX_REFERENCE_AUDIO. r2i gets 0 -- a still
// frame extracted from the underlying render can't carry it, so offering
// the upload would just be confusing (see backend's own comment on this).
export const MAX_REFERENCE_AUDIO: Record<Mode, number> = {
  t2v: 0,
  i2v: 0,
  r2v: 3,
  t2i: 0,
  r2i: 0,
  t2a: 0,
  r2a: 3,
};

// Mirrors generation/api.py's _MAX_REFERENCE_VIDEO -- a reference video's
// audio track rides along with the same upload, so it isn't tracked as a
// separate limit.
export const MAX_REFERENCE_VIDEO: Record<Mode, number> = {
  t2v: 0,
  i2v: 0,
  r2v: 3,
  t2i: 0,
  r2i: 0,
  t2a: 0,
  r2a: 3,
};

// Mirrors backend director/models.py's CONTINUATION_CAPABLE_MODES --
// Director Mode's Clip.continues_previous is only offered for modes whose
// sampler node produces a conditioning+latent pair MiniMaxH3ChainContext
// can splice continuity into (see extras.md#contex-loop).
export const CONTINUATION_CAPABLE_MODES = new Set<Mode>(["i2v", "r2v"]);

export interface AspectRatioOption {
  value: string; // e.g. "16:9" -- pass as GenerationJob.aspect_ratio
  label: string; // e.g. "16:9 (Widescreen)"
}

export interface AppConfig {
  llm_enabled: boolean;
  // Whether the backend will actually forward reference images to the LLM
  // as vision content when chatting -- worth checking before bothering to
  // upload them on every chat turn, see api/queries.ts's useChatReply().
  llm_vision_enabled: boolean;
  oidc_enabled: boolean;
  oidc_login_url: string | null;
  oidc_provider_name: string;
  // Doesn't affect render time (unlike RenderPreset.megapixels), so it's a
  // fixed enum from config rather than part of the preset/duration catalog.
  aspect_ratios: AspectRatioOption[];
  default_aspect_ratio: string;
  // null: not offered. 0: optional toggle, default off. 1: optional toggle,
  // default on. 2: forced on for every job, no toggle to show. See
  // extras.md#spectrum.
  spectrum_level: 0 | 1 | 2 | null;
}

export interface CurrentUser {
  authenticated: boolean;
  id?: number;
  username?: string;
  email?: string;
  // Whether this user can manage invites -- UX only (hides/shows the Admin
  // nav link and gates the /manage route client-side); the real boundary
  // is IsAdminUser on the invite endpoints themselves.
  is_staff?: boolean;
}

export interface Invite {
  id: number;
  token: string; // combine with location.origin to build /invite/<token>/
  email: string; // blank if not locked to one address
  note: string; // optional admin-facing note (e.g. who it's for), never shown to the invitee
  created_by: string | null; // username
  created_at: string;
  expires_at: string | null;
  is_redeemed: boolean;
  is_expired: boolean;
  redeemed_by: string | null; // username
  redeemed_at: string | null;
}

export interface RenderDuration {
  id: number; // pass as CreateJobRequest.duration_id
  duration_seconds: number;
  estimated_render_seconds: number;
}

// A "quality tier" -- megapixels (+ steps) determine render time, along
// with the chosen duration; a preset's `durations` are its selectable clip
// lengths, each independently estimated (not derived from a formula).
// Aspect ratio is orthogonal -- see AppConfig.aspect_ratios -- since it
// doesn't meaningfully affect render time for a fixed pixel count.
export interface RenderPreset {
  id: number;
  mode: Mode;
  label: string; // e.g. "Draft", "Standard", "High quality"
  megapixels: number;
  steps: number;
  is_draft: boolean;
  durations: RenderDuration[];
}

export interface QueueEstimate {
  seconds_ahead: number;
  additional_seconds: number;
  total_seconds: number;
  estimated_finish_time: string;
}

export type ReferenceKind = "image" | "video" | "audio";

export interface ReferenceAsset {
  id: number;
  kind: ReferenceKind;
  order: number;
  label: string;
  url: string | null;
}

// Jobs render strictly one at a time, FIFO (see backend/generation/tasks.py's
// process_queue()), so there's no "about to run" vs "running" distinction to
// make. "done" covers both success and failure -- check video_url/
// error_message to tell them apart. "cancelled" is its own terminal state
// (see useCancelJob) so the UI can show "Cancelled" instead of "Failed".
export type JobStatus = "queued" | "processing" | "done" | "cancelled";

// Sub-state of a "processing" job, per ComfyUI's own three real execution
// phases -- see backend/integrations/comfyui.py's stream_execution_progress().
// Blank/null while queued/done; only ever meaningful mid-render.
export type JobPhase = "" | "preparing" | "rendering" | "finishing";

export interface GenerationJob {
  id: number;
  mode: Mode;
  content_type: ContentType;
  status: JobStatus;
  raw_prompt: string;
  // User-editable label (see useUpdateJob) -- blank means the frontend
  // should fall back to raw_prompt, see features/queue/jobTitle.ts.
  title: string;
  // User-toggled "hearted" flag -- see useUpdateJob(). Purely a display/
  // filter aid, no effect on rendering.
  is_favorite: boolean;
  // Hides this job from the default queue/history view -- see
  // useUpdateJob(). The list endpoint always returns every job regardless
  // of this flag; filtering happens client-side (see QueueSidebar).
  is_archived: boolean;
  preset_id: number;
  // Quality tier label (e.g. "Draft", "Standard") -- RenderPreset.label, read
  // live server-side, see api.py's GenerationJobSerializer.
  preset_label: string;
  duration_id: number;
  megapixels: number;
  aspect_ratio: string;
  width: number;
  height: number;
  duration_seconds: number;
  estimated_seconds: number;
  // Whether this job used the Spectrum accelerator -- see extras.md#spectrum.
  // estimated_seconds above does NOT account for it.
  use_spectrum: boolean;
  video_url: string | null;
  // Small poster image for video-content-type jobs -- null for image/audio
  // jobs (video_url already works as a thumbnail for those) and for jobs
  // rendered before this field existed. Prefer this over video_url for
  // list-view thumbnails.
  thumbnail_url: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  // Set while queued/processing (computed by walking the FIFO queue); null
  // once done.
  expected_finish_time: string | null;
  phase: JobPhase;
  // Only set while phase === "rendering" -- sampler step reached / total steps.
  progress_current: number | null;
  progress_total: number | null;
}

export interface GenerationJobDetail extends GenerationJob {
  improved_prompt: string;
  error_message: string;
  references: ReferenceAsset[];
}

export type ChatRole = "user" | "assistant";

export interface ChatMessage {
  role: ChatRole;
  content: string;
}

// Admin-only "Quality & Duration" catalog editor (backend/generation/
// admin_api.py) -- a read model over the same RenderPreset/RenderDuration
// rows as RenderPreset above, but including inactive ones (so they can be
// re-enabled) and grouped by label ("quality level") across modes, which
// the user-facing /api/presets/ never needs to do.

export interface CatalogModePreset {
  preset_id: number;
  megapixels: number;
  steps: number;
  is_active: boolean;
}

export interface CatalogLevel {
  label: string;
  is_draft: boolean;
  // Admin-controlled display order (lower first) -- see useReorderQualityLevels().
  // catalog.levels is already server-sorted by this, so array order == display order.
  sort_order: number;
  // Only has a key for modes that actually have a RenderPreset row.
  modes: Partial<Record<Mode, CatalogModePreset>>;
}

export interface CatalogDurationTarget {
  id: number | null;
  is_active: boolean;
  estimated_render_seconds: number | null;
}

export interface CatalogDurationRow {
  duration_seconds: number;
  // Keyed by level label, then mode -- a target entry exists for every
  // (label, mode) that CatalogLevel.modes says exists, even when this
  // particular duration is inactive/absent for it.
  targets: Record<string, Partial<Record<Mode, CatalogDurationTarget>>>;
}

export interface QualityCatalog {
  modes: Mode[];
  levels: CatalogLevel[];
  durations: CatalogDurationRow[];
}

// POST /api/quality-durations/estimate/ -- fits real completed-job render
// times against workload (steps * megapixels * duration_seconds), pooled
// across every quality level of one mode at once (not one preset at a
// time) -- see admin_api.py's docstring for the reasoning ("a completed
// job on one level and a completed job on another level at the same
// duration land at different workload values, which is what lets the fit
// see the gap between levels"). fit_available is false when there isn't
// enough data yet (fewer than 2 distinct workload values among completed
// jobs) rather than an error.

export interface LinearFit {
  intercept: number;
  slope: number;
}

export interface PiecewiseFit {
  breakpoint_workload: number;
  segment_low: LinearFit;
  segment_high: LinearFit;
}

// One completed job's (duration, workload, actual render time) -- the raw
// data behind the fit, used to draw the estimate modal's charts.
export interface DurationEstimateSample {
  label: string;
  duration_seconds: number;
  workload: number;
  render_seconds: number;
}

export interface DurationEstimate {
  label: string;
  duration_seconds: number;
  current_estimate: number | null;
  fitted_estimate: number;
}

export interface DurationEstimateResponse {
  fit_available: boolean;
  sample_count: number;
  distinct_workloads: number;
  // Only present when fit_available.
  model?: "linear" | "piecewise";
  linear?: LinearFit;
  // Non-null only when model === "piecewise".
  piecewise?: PiecewiseFit | null;
  samples?: DurationEstimateSample[];
  estimates?: DurationEstimate[];
}
