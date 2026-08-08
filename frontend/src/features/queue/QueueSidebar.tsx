import { useEffect, useRef, useState } from "react";
import { useJobs, useQueueEstimate } from "../../api/queries";
import { MODE_LABELS, type GenerationJob, type JobStatus } from "../../api/types";
import { displayTitle } from "./jobTitle";
import { JobProgressBar } from "./JobProgressBar";

const NOTIFY_STORAGE_KEY = "notifyOnJobDone";
const ACTIVE_STATUSES = new Set<JobStatus>(["queued", "processing"]);

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
  return displayTitle(job, 40);
}

function relativeTime(iso: string): string {
  const diffMin = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  return new Date(iso).toLocaleDateString();
}

function QueueThumb({ job }: { job: GenerationJob }) {
  if (job.status === "done" && job.video_url) {
    if (job.content_type === "video") {
      // Prefer the pre-generated poster image (a static <img>, cheap to
      // render N-per-list) -- fall back to the old <video> thumb only for
      // jobs rendered before thumbnail_url existed (or the rare
      // thumbnail-generation failure), never worse than before.
      if (job.thumbnail_url) return <img src={job.thumbnail_url} alt="" />;
      return <video src={job.video_url} muted preload="metadata" />;
    }
    if (job.content_type === "image") return <img src={job.video_url} alt="" />;
    // Audio has no useful tiny-thumbnail rendering (an <audio> element is a
    // full player, not an image) -- falls through to the placeholder below.
  }
  return (
    <span className="queue-entry-placeholder" aria-hidden="true">
      {job.status === "done" && job.video_url && job.content_type === "audio" ? "♪" : MODE_LABELS[job.mode][0]}
    </span>
  );
}

function QueueEntry({ job, onOpen }: { job: GenerationJob; onOpen: () => void }) {
  const failed = didJobFail(job);
  return (
    <li className={`queue-entry status-${job.status}`}>
      <button type="button" className="queue-entry-button" onClick={onOpen}>
        <span className="queue-entry-thumb">
          <QueueThumb job={job} />
        </span>
        <span className="queue-entry-body">
          <span className="queue-entry-title">{titleFor(job)}</span>
          <span className="queue-entry-meta">
            <span className="queue-entry-status-time">
              <span
                className={`job-status job-status-${job.status} ${failed ? "job-status-failed" : ""}`}
              >
                {failed ? "Failed" : STATUS_LABELS[job.status]}
              </span>
              <span className="queue-entry-time">{relativeTime(job.created_at)}</span>
              <span className="queue-entry-id">#{job.id}</span>
            </span>
            <span className="queue-entry-quality">{job.preset_label}</span>
          </span>
          {job.status === "processing" && <JobProgressBar job={job} />}
        </span>
      </button>
    </li>
  );
}

interface QueueSidebarProps {
  onOpenJob: (jobId: number) => void;
}

// Persisted in localStorage (not just React state) so the preference
// survives a reload -- there's no server-side user-settings model for it.
function useNotifyOnDone(): [boolean, (next: boolean) => void] {
  const [enabled, setEnabled] = useState(
    () => typeof Notification !== "undefined" && localStorage.getItem(NOTIFY_STORAGE_KEY) === "true",
  );

  function set(next: boolean) {
    if (next && typeof Notification !== "undefined" && Notification.permission === "default") {
      void Notification.requestPermission().then((permission) => {
        const granted = permission === "granted";
        localStorage.setItem(NOTIFY_STORAGE_KEY, String(granted));
        setEnabled(granted);
      });
      return;
    }
    localStorage.setItem(NOTIFY_STORAGE_KEY, String(next));
    setEnabled(next);
  }

  return [enabled, set];
}

export function QueueSidebar({ onOpenJob }: QueueSidebarProps) {
  const jobs = useJobs();
  const queueEstimate = useQueueEstimate(null);
  const [notifyOnDone, setNotifyOnDone] = useNotifyOnDone();

  // Tracks which job ids were active (queued/processing) as of the last
  // render, so a job disappearing from that set can be detected as a
  // queued->done transition -- comparing snapshots rather than trusting a
  // single job's status change event, since polling (useJobs) only hands us
  // periodic full-list snapshots, not transitions themselves.
  const previouslyActiveRef = useRef<Set<number>>(new Set());
  useEffect(() => {
    const current = jobs.data ?? [];
    const previouslyActive = previouslyActiveRef.current;
    if (notifyOnDone && typeof Notification !== "undefined" && Notification.permission === "granted") {
      for (const job of current) {
        if (previouslyActive.has(job.id) && !ACTIVE_STATUSES.has(job.status)) {
          const failed = didJobFail(job);
          new Notification(failed ? "Generation failed" : "Generation done", {
            body: titleFor(job),
            tag: `job-${job.id}`,
          });
        }
      }
    }
    previouslyActiveRef.current = new Set(
      current.filter((job) => ACTIVE_STATUSES.has(job.status)).map((job) => job.id),
    );
  }, [jobs.data, notifyOnDone]);

  return (
    <aside className="queue-sidebar">
      <h2>Queue</h2>
      <label className="queue-notify-toggle hint">
        <input
          type="checkbox"
          checked={notifyOnDone}
          onChange={(e) => setNotifyOnDone(e.target.checked)}
        />
        Notify me when a job is done
      </label>
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
