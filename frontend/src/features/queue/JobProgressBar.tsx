import type { GenerationJob, JobPhase } from "../../api/types";

const PHASE_LABELS: Record<Exclude<JobPhase, "">, string> = {
  preparing: "Preparing…",
  rendering: "Rendering…",
  finishing: "Finishing…",
};

interface JobProgressBarProps {
  job: Pick<GenerationJob, "phase" | "progress_current" | "progress_total">;
}

// Renders ComfyUI's three real execution phases (see backend/integrations/
// comfyui.py's stream_execution_progress()) as a label + a background-fill
// progress bar: filled left-to-right by sampler step during "rendering" (the
// only phase with a known step count), an indeterminate sliding bar during
// "preparing"/"finishing" (no step count to show, but still communicates
// "actively working" rather than looking stalled).
export function JobProgressBar({ job }: JobProgressBarProps) {
  if (!job.phase) return null;
  const pct =
    job.phase === "rendering" && job.progress_total
      ? Math.min(100, Math.round(((job.progress_current ?? 0) / job.progress_total) * 100))
      : null;
  return (
    <div className="job-progress">
      <div className="job-progress-label">
        {PHASE_LABELS[job.phase]}
        {pct != null && ` ${pct}%`}
      </div>
      <div className={`job-progress-track ${pct == null ? "job-progress-indeterminate" : ""}`}>
        <div className="job-progress-fill" style={pct != null ? { width: `${pct}%` } : undefined} />
      </div>
    </div>
  );
}
