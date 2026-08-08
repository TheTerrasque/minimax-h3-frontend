import { MODE_LABELS, type GenerationJob } from "../../api/types";

// Shared by QueueSidebar (list rows, further truncated there) and JobModal
// (full-length, editable) -- job.title is user-set (see useUpdateJobTitle),
// blank by default, in which case this falls back to the prompt itself.
export function displayTitle(job: GenerationJob): string {
  const title = job.title.trim();
  if (title) return title;
  const prompt = job.raw_prompt.trim();
  return prompt || MODE_LABELS[job.mode];
}
