import { useEffect, useMemo, useRef, useState } from "react";
import { useJobs, usePresets, useQueueEstimate, useUpdateJob } from "../../api/queries";
import { useDirectorProjects, useJobMemberships } from "../../api/directorQueries";
import { MODE_LABELS, type GenerationJob, type JobStatus } from "../../api/types";
import { displayTitle } from "./jobTitle";
import { JobProgressBar } from "./JobProgressBar";

const NOTIFY_STORAGE_KEY = "notifyOnJobDone";
const ACTIVE_STATUSES = new Set<JobStatus>(["queued", "processing"]);

// "all" (no filter) / "none" (standalone jobs -- no Director project) / a
// stringified project id. Kept as a plain string (not a union) since it
// round-trips straight through a <select>'s value without parsing.
const PROJECT_FILTER_ALL = "all";
const PROJECT_FILTER_NONE = "none";
const QUALITY_FILTER_ALL = "all";

const STATUS_LABELS: Record<JobStatus, string> = {
  queued: "Queued",
  processing: "Processing…",
  done: "Done",
  cancelled: "Cancelled",
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
  const updateJob = useUpdateJob();
  return (
    <li className={`queue-entry status-${job.status}`}>
      {/* Siblings of queue-entry-button (not nested inside it -- a <button>
          can't legally contain another <button>), positioned over its
          thumbnail corner via .queue-entry-quick-actions below. */}
      <div className="queue-entry-quick-actions">
        <button
          type="button"
          className="queue-entry-quick-action queue-entry-quick-action-favorite"
          onClick={() => updateJob.mutate({ jobId: job.id, isFavorite: !job.is_favorite })}
          aria-pressed={job.is_favorite}
          title={job.is_favorite ? "Remove from favorites" : "Add to favorites"}
        >
          <span aria-hidden="true">{job.is_favorite ? "♥" : "♡"}</span>
        </button>
        <button
          type="button"
          className="queue-entry-quick-action queue-entry-quick-action-archive"
          onClick={() => updateJob.mutate({ jobId: job.id, isArchived: !job.is_archived })}
          aria-pressed={job.is_archived}
          title={job.is_archived ? "Unarchive" : "Archive"}
        >
          <span aria-hidden="true">🗄</span>
        </button>
      </div>
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
  const jobMemberships = useJobMemberships();
  const directorProjects = useDirectorProjects();
  // Canonical quality-tier ordering for the filter dropdown -- t2v's own
  // catalog order (RenderPreset.sort_order, see list_presets()), not
  // "whichever label a job happened to use most recently" (job.created_at
  // desc is GenerationJob's default ordering, so that's what a naive
  // first-seen-in-job-list order would actually produce -- a different,
  // effectively random-looking order every time the job history changes).
  // Admin keeps a label's sort_order in sync across every mode's row (see
  // RenderPreset's own docstring), so t2v's order should match every other
  // mode's for the same label.
  const qualityCatalogOrder = usePresets("t2v");
  const [notifyOnDone, setNotifyOnDone] = useNotifyOnDone();

  const [filtersOpen, setFiltersOpen] = useState(false);
  const [qualityFilter, setQualityFilter] = useState(QUALITY_FILTER_ALL);
  const [projectFilter, setProjectFilter] = useState(PROJECT_FILTER_ALL);
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [showArchived, setShowArchived] = useState(false);

  function resetFilters() {
    setQualityFilter(QUALITY_FILTER_ALL);
    setProjectFilter(PROJECT_FILTER_ALL);
    setFavoritesOnly(false);
    setShowArchived(false);
  }

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
        // Cancellation is always something the user themselves just did in
        // this same browser (see JobModal's Cancel button) -- a
        // notification for it would just be a redundant echo, unlike a
        // done/failed transition which can happen while they're away.
        if (previouslyActive.has(job.id) && !ACTIVE_STATUSES.has(job.status) && job.status !== "cancelled") {
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

  // Distinct quality labels actually present in the job list (not the
  // full admin catalog, which may offer tiers no job here was ever
  // rendered at), ordered to match the catalog's own sort_order -- see
  // qualityCatalogOrder above. A label the job list has that t2v's own
  // catalog doesn't (e.g. an image/audio-only tier, or one since retired)
  // sorts after every catalog-ordered one, in first-seen order.
  const qualityOptions = useMemo(() => {
    const present: string[] = [];
    for (const job of jobs.data ?? []) {
      if (job.preset_label && !present.includes(job.preset_label)) present.push(job.preset_label);
    }
    const catalogIndex = new Map((qualityCatalogOrder.data ?? []).map((preset, index) => [preset.label, index]));
    return [...present].sort((a, b) => {
      const aIndex = catalogIndex.get(a) ?? Number.MAX_SAFE_INTEGER;
      const bIndex = catalogIndex.get(b) ?? Number.MAX_SAFE_INTEGER;
      return aIndex - bIndex;
    });
  }, [jobs.data, qualityCatalogOrder.data]);

  const projectByJobId = useMemo(() => {
    const map = new Map<number, { project_id: number; project_title: string }>();
    for (const row of jobMemberships.data ?? []) {
      map.set(row.job_id, { project_id: row.project_id, project_title: row.project_title });
    }
    return map;
  }, [jobMemberships.data]);

  const filteredJobs = useMemo(() => {
    return (jobs.data ?? []).filter((job) => {
      if (!showArchived && job.is_archived) return false;
      if (favoritesOnly && !job.is_favorite) return false;
      if (qualityFilter !== QUALITY_FILTER_ALL && job.preset_label !== qualityFilter) return false;
      if (projectFilter === PROJECT_FILTER_NONE && projectByJobId.has(job.id)) return false;
      if (
        projectFilter !== PROJECT_FILTER_ALL &&
        projectFilter !== PROJECT_FILTER_NONE &&
        projectByJobId.get(job.id)?.project_id !== Number(projectFilter)
      ) {
        return false;
      }
      return true;
    });
  }, [jobs.data, showArchived, favoritesOnly, qualityFilter, projectFilter, projectByJobId]);

  const filtersActive =
    qualityFilter !== QUALITY_FILTER_ALL || projectFilter !== PROJECT_FILTER_ALL || favoritesOnly || showArchived;

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

      <div className="queue-filters-header">
        <button
          type="button"
          className="queue-filters-toggle"
          onClick={() => setFiltersOpen((v) => !v)}
          aria-expanded={filtersOpen}
        >
          <span aria-hidden="true">{filtersOpen ? "▾" : "▸"}</span> Filters
          {filtersActive && <span className="queue-filters-badge" title="Filters active" />}
        </button>
        {filtersActive && (
          <button type="button" className="link-button" onClick={resetFilters}>
            Reset
          </button>
        )}
      </div>

      {filtersOpen && (
        <div className="queue-filters">
          <label className="queue-filter-control">
            <span className="hint">Quality</span>
            <select value={qualityFilter} onChange={(e) => setQualityFilter(e.target.value)}>
              <option value={QUALITY_FILTER_ALL}>All</option>
              {qualityOptions.map((label) => (
                <option key={label} value={label}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="queue-filter-control">
            <span className="hint">Director project</span>
            <select value={projectFilter} onChange={(e) => setProjectFilter(e.target.value)}>
              <option value={PROJECT_FILTER_ALL}>All</option>
              <option value={PROJECT_FILTER_NONE}>No project</option>
              {directorProjects.data?.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.title || `Untitled project #${project.id}`}
                </option>
              ))}
            </select>
          </label>
          <label className="queue-filter-checkbox hint">
            <input type="checkbox" checked={favoritesOnly} onChange={(e) => setFavoritesOnly(e.target.checked)} />
            Favorites only
          </label>
          <label className="queue-filter-checkbox hint">
            <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
            Show archived
          </label>
        </div>
      )}

      {jobs.isLoading && <p className="hint">Loading…</p>}
      {jobs.isError && <p className="error">Couldn't load your jobs.</p>}
      {jobs.data?.length === 0 && <p className="empty-state">No jobs yet — queue one to see it here.</p>}
      {!!jobs.data?.length && filteredJobs.length === 0 && (
        <p className="empty-state">
          {filtersActive ? "No jobs match these filters." : "No jobs yet — queue one to see it here."}
        </p>
      )}
      {filtersActive && filteredJobs.length > 0 && (
        <p className="hint queue-filter-count">
          {filteredJobs.length} of {jobs.data?.length}
        </p>
      )}

      <ul className="queue-list">
        {filteredJobs.map((job) => (
          <QueueEntry key={job.id} job={job} onOpen={() => onOpenJob(job.id)} />
        ))}
      </ul>
    </aside>
  );
}
