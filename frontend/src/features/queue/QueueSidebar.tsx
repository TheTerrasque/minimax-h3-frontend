import { useJobs, useQueueEstimate } from "../../api/queries";
import { MODE_LABELS, type GenerationJob, type JobStatus } from "../../api/types";

const STATUS_LABELS: Record<JobStatus, string> = {
  queued: "Queued",
  processing: "Processing…",
  done: "Done",
};

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
}

// "done" covers both success and failure (see api/types.ts) -- video_url's
// presence is what actually distinguishes them for display purposes.
function didJobFail(job: GenerationJob): boolean {
  return job.status === "done" && !job.video_url;
}

function titleFor(job: GenerationJob): string {
  const trimmed = job.raw_prompt.trim();
  if (!trimmed) return MODE_LABELS[job.mode];
  return trimmed.length > 40 ? `${trimmed.slice(0, 40)}…` : trimmed;
}

function relativeTime(iso: string): string {
  const diffMin = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  return new Date(iso).toLocaleDateString();
}

function QueueEntry({ job, onOpen }: { job: GenerationJob; onOpen: () => void }) {
  const failed = didJobFail(job);
  return (
    <li className={`queue-entry status-${job.status}`}>
      <button type="button" className="queue-entry-button" onClick={onOpen}>
        <span className="queue-entry-thumb">
          {job.status === "done" && job.video_url ? (
            <video src={job.video_url} muted preload="metadata" />
          ) : (
            <span className="queue-entry-placeholder" aria-hidden="true">
              {MODE_LABELS[job.mode][0]}
            </span>
          )}
        </span>
        <span className="queue-entry-body">
          <span className="queue-entry-title">{titleFor(job)}</span>
          <span className="queue-entry-meta">
            <span
              className={`job-status job-status-${job.status} ${failed ? "job-status-failed" : ""}`}
            >
              {failed ? "Failed" : STATUS_LABELS[job.status]}
            </span>
            <span className="queue-entry-time">{relativeTime(job.created_at)}</span>
          </span>
        </span>
      </button>
    </li>
  );
}

interface QueueSidebarProps {
  onOpenJob: (jobId: number) => void;
}

export function QueueSidebar({ onOpenJob }: QueueSidebarProps) {
  const jobs = useJobs();
  const queueEstimate = useQueueEstimate(null);

  return (
    <aside className="queue-sidebar">
      <h2>Queue</h2>
      {queueEstimate.data && (
        <p className="hint queue-backlog">
          Backlog:{" "}
          {queueEstimate.data.seconds_ahead > 0
            ? `~${formatDuration(queueEstimate.data.seconds_ahead)}`
            : "none"}
        </p>
      )}

      {jobs.isLoading && <p className="hint">Loading…</p>}
      {jobs.isError && <p className="error">Couldn't load your jobs.</p>}
      {jobs.data?.length === 0 && <p className="empty-state">No jobs yet — queue one to see it here.</p>}

      <ul className="queue-list">
        {jobs.data?.map((job) => (
          <QueueEntry key={job.id} job={job} onOpen={() => onOpenJob(job.id)} />
        ))}
      </ul>
    </aside>
  );
}
