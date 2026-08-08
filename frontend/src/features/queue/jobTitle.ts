import { MODE_LABELS, type GenerationJob } from "../../api/types";

// A "title" should always read as a short label, never the thing that
// blows up JobModal's heading into several lines -- raw_prompt is an
// unbounded TextField (unlike job.title itself, capped server-side at 200,
// see PATCH /api/jobs/{id}/), so the fallback case below is the one that
// actually needs a length cap, not just wherever it happens to be shown.
const DEFAULT_MAX_LENGTH = 100;

// Shared by QueueSidebar (list rows, pass a shorter maxLength) and JobModal
// (heading + the edit-draft starting value) -- job.title is user-set (see
// useUpdateJobTitle), blank by default, in which case this falls back to
// the prompt itself.
export function displayTitle(job: GenerationJob, maxLength: number = DEFAULT_MAX_LENGTH): string {
  const title = job.title.trim();
  const fallback = title || job.raw_prompt.trim() || MODE_LABELS[job.mode];
  return fallback.length > maxLength ? `${fallback.slice(0, maxLength)}…` : fallback;
}
