// Shapes returned by backend/director/api.py -- same relationship to that
// module as api/types.ts has to generation/api.py.

import type { JobPhase, JobStatus, Mode, ReferenceKind } from "./types";

export interface ProjectResource {
  id: number;
  kind: ReferenceKind;
  order: number;
  label: string; // human label if set, else the <Picture N>-style token
  // The literal <Picture N>/<Video N>/<Audio N> token this resource maps
  // to at render time -- unlike `label`, never a human override. Use this
  // when building an LLM reference_labels list.
  token_label: string;
  url: string | null;
}

export interface ClipReference {
  id: number;
  kind: ReferenceKind;
  order: number;
  label: string;
  url: string | null;
}

export interface Clip {
  id: number;
  project_id: number;
  order: number;
  // Splice motion/audio continuity from whichever clip is immediately
  // before this one -- positional, not a stored predecessor id (see
  // backend director/models.py's Clip docstring): reordering the board
  // changes what a continuation box actually continues from.
  continues_previous: boolean;
  mode: Mode;
  prompt: string;
  improved_prompt: string;
  preset_id: number;
  duration_id: number;
  width: number;
  height: number;
  // The red-border dirty flag -- true whenever this clip's current render
  // (if any) no longer reflects its current content.
  needs_render: boolean;
  current_job_id: number | null;
  current_job_status: JobStatus | null;
  // Sub-state while current_job_status === "processing" -- null otherwise
  // (unlike GenerationJob.phase, which is "" rather than null when blank).
  phase: Exclude<JobPhase, ""> | null;
  progress_current: number | null;
  progress_total: number | null;
  video_url: string | null;
  thumbnail_url: string | null;
  error_message: string | null;
  references: ClipReference[];
}

export interface Project {
  id: number;
  title: string;
  overarching_prompt: string;
  // Applies to every Clip in the project -- not chosen per-clip (see
  // backend director/models.py's Project docstring).
  aspect_ratio: string;
  // The shared quality tier every Clip's own (per-mode) preset is
  // resolved from -- also project-wide, not chosen per-clip.
  quality_label: string;
  created_at: string;
  updated_at: string;
  // Only set by the list endpoint (useDirectorProjects) -- null on a
  // single-project fetch (useDirectorProject), which has the real `clips`
  // array to compute the same things from directly if ever needed.
  clip_count: number | null;
  dirty_count: number | null;
  active_count: number | null;
  eta_seconds: number | null;
}

export interface ProjectDetail extends Project {
  resources: ProjectResource[];
  clips: Clip[];
  // Set once POST .../assemble/ has run at least once -- see
  // ProjectBoard's Export button. Not auto-cleared by later clip edits, so
  // a present URL doesn't guarantee it reflects the project's current state.
  assembled_video_url: string | null;
}

// One proposed scene from POST .../plan/ -- a preview, not yet a real Clip.
// Editable client-side (see ScriptPlanModal) before being sent back to
// POST .../plan/apply/ to actually create clips from it.
export interface PlannedScene {
  mode: Mode;
  continues_previous: boolean;
  prompt: string;
  notes: string;
}
