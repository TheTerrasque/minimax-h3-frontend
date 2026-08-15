import { useEffect, useState, type KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useCancelJob, useDeleteJob, useJob, useUpdateJob } from "../../api/queries";
import { useCreateProjectFromJob, useJobMemberships } from "../../api/directorQueries";
import { MODE_LABELS, type GenerationJobDetail } from "../../api/types";
import { displayTitle } from "./jobTitle";
import { JobProgressBar } from "./JobProgressBar";

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
}

// Actual measured render time once done; falls back to the estimate while
// still queued/processing (there's no finished_at yet to measure from).
function renderTimeLabel(job: GenerationJobDetail): string {
  if (job.started_at && job.finished_at) {
    const seconds = (new Date(job.finished_at).getTime() - new Date(job.started_at).getTime()) / 1000;
    return `${formatDuration(seconds)} (actual)`;
  }
  return `~${formatDuration(job.estimated_seconds)} (estimated)`;
}

// Resolution is fixed/meaningless for audio (visual output is discarded --
// see backend's Mode docstring) and duration is pinned near-zero for image
// (only frame 0 is kept), so each content type only shows the axis that
// actually means something for it.
function resolutionLabel(job: GenerationJobDetail): string {
  const resolution = `${job.width}×${job.height} (${job.aspect_ratio}, ${job.megapixels}MP)`;
  if (job.content_type === "video") return `${resolution} — ${job.duration_seconds}s`;
  if (job.content_type === "image") return resolution;
  return `${job.duration_seconds}s`;
}

interface JobModalProps {
  jobId: number;
  onClose: () => void;
  onRedo: (job: GenerationJobDetail) => void;
}

export function JobModal({ jobId, onClose, onRedo }: JobModalProps) {
  const navigate = useNavigate();
  const job = useJob(jobId);
  const deleteJob = useDeleteJob();
  const cancelJob = useCancelJob();
  const updateJob = useUpdateJob();
  const jobMemberships = useJobMemberships();
  const createProjectFromJob = useCreateProjectFromJob();
  const [showAiPrompt, setShowAiPrompt] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");

  // Reset per-job UI state in case this modal instance is reused for a
  // different job rather than remounted (e.g. clicking straight from one
  // job to another without closing in between).
  useEffect(() => {
    setConfirmingDelete(false);
    setEditingTitle(false);
  }, [jobId]);

  async function handleDelete() {
    await deleteJob.mutateAsync(jobId);
    onClose();
  }

  async function handleCancel() {
    await cancelJob.mutateAsync(jobId);
  }

  async function handleCreateProject() {
    const project = await createProjectFromJob.mutateAsync(jobId);
    onClose();
    navigate(`/director/${project.id}`);
  }

  const directorMembership = jobMemberships.data?.find((m) => m.job_id === jobId);
  // Mirrors backend director/services.py's create_project_from_job() own
  // eligibility check (video-mode, successfully finished) -- content_type
  // "video" is exactly Director's own t2v/i2v/r2v mode set, see
  // generation/models.py's CONTENT_TYPE_BY_MODE.
  const canCreateDirectorProject =
    job.data?.content_type === "video" && job.data.status === "done" && !!job.data.video_url;

  function startEditingTitle() {
    if (!job.data) return;
    setTitleDraft(displayTitle(job.data));
    setEditingTitle(true);
  }

  async function saveTitle() {
    setEditingTitle(false);
    if (!job.data || titleDraft.trim() === job.data.title.trim()) return;
    await updateJob.mutateAsync({ jobId, title: titleDraft.trim() });
  }

  function toggleFavorite() {
    if (!job.data) return;
    updateJob.mutate({ jobId, isFavorite: !job.data.is_favorite });
  }

  function toggleArchived() {
    if (!job.data) return;
    updateJob.mutate({ jobId, isArchived: !job.data.is_archived });
  }

  function handleTitleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") e.currentTarget.blur(); // triggers onBlur -> saveTitle
    if (e.key === "Escape") setEditingTitle(false);
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
          ×
        </button>

        {job.isLoading && <p className="hint">Loading…</p>}
        {job.isError && <p className="error">Couldn't load this job.</p>}

        {job.data && (
          <>
            {editingTitle ? (
              <input
                type="text"
                className="modal-title-input"
                autoFocus
                value={titleDraft}
                maxLength={200}
                onChange={(e) => setTitleDraft(e.target.value)}
                onBlur={saveTitle}
                onKeyDown={handleTitleKeyDown}
              />
            ) : (
              <h2
                className="modal-title-editable"
                onClick={startEditingTitle}
                title="Click to rename"
              >
                {displayTitle(job.data)}
              </h2>
            )}
            <p className="hint modal-mode-label">
              {MODE_LABELS[job.data.mode]}
              <button
                type="button"
                className="link-button job-favorite-toggle"
                onClick={toggleFavorite}
                aria-pressed={job.data.is_favorite}
                title={job.data.is_favorite ? "Remove from favorites" : "Add to favorites"}
              >
                <span aria-hidden="true">{job.data.is_favorite ? "♥" : "♡"}</span>
              </button>
              {job.data.is_archived && <span className="hint"> — Archived</span>}
            </p>

            {job.data.status === "done" && job.data.video_url ? (
              job.data.content_type === "video" ? (
                <video src={job.data.video_url} controls className="modal-video" />
              ) : job.data.content_type === "image" ? (
                <img src={job.data.video_url} alt="" className="modal-image" />
              ) : (
                <audio src={job.data.video_url} controls className="modal-audio" />
              )
            ) : job.data.status === "done" ? (
              <p className="error">Failed: {job.data.error_message || "no output was produced."}</p>
            ) : job.data.status === "cancelled" ? (
              <p className="hint">Cancelled.</p>
            ) : (
              <>
                <p className="hint">
                  {job.data.status === "processing" ? "Processing…" : "Queued…"}
                  {job.data.expected_finish_time &&
                    ` Expected done by ${new Date(job.data.expected_finish_time).toLocaleTimeString()}.`}
                </p>
                {job.data.status === "processing" && <JobProgressBar job={job.data} />}
              </>
            )}

            <dl className="modal-details">
              <dt>Prompt</dt>
              <dd>{job.data.raw_prompt}</dd>
              {job.data.improved_prompt && (
                <>
                  <dt>
                    <button
                      type="button"
                      className="link-button"
                      onClick={() => setShowAiPrompt((v) => !v)}
                      aria-expanded={showAiPrompt}
                    >
                      AI-refined prompt {showAiPrompt ? "▾" : "▸"}
                    </button>
                  </dt>
                  {showAiPrompt && <dd>{job.data.improved_prompt}</dd>}
                </>
              )}
              <dt>
                {job.data.content_type === "video"
                  ? "Resolution & length"
                  : job.data.content_type === "image"
                    ? "Resolution"
                    : "Length"}
              </dt>
              <dd>{resolutionLabel(job.data)}</dd>
              <dt>Render time</dt>
              <dd>{renderTimeLabel(job.data)}</dd>
              {directorMembership && (
                <>
                  <dt>Director project</dt>
                  <dd>
                    <button
                      type="button"
                      className="link-button"
                      onClick={() => navigate(`/director/${directorMembership.project_id}`)}
                    >
                      {directorMembership.project_title || "Untitled project"}
                    </button>
                  </dd>
                </>
              )}
            </dl>

            <div className="modal-actions">
              {job.data.video_url && (
                <a href={job.data.video_url} download className="button button-primary">
                  <span aria-hidden="true">⬇</span> Download
                </a>
              )}
              <button type="button" onClick={() => onRedo(job.data)}>
                <span aria-hidden="true">↻</span> Redo
              </button>
              <button type="button" onClick={toggleArchived}>
                <span aria-hidden="true">🗄</span> {job.data.is_archived ? "Unarchive" : "Archive"}
              </button>
              {canCreateDirectorProject && !directorMembership && (
                <button
                  type="button"
                  onClick={() => void handleCreateProject()}
                  disabled={createProjectFromJob.isPending}
                  title="Starts a new Director project with this already-rendered clip as its first scene -- no re-render needed."
                >
                  {createProjectFromJob.isPending ? "Creating…" : "Create Director project"}
                </button>
              )}
              {(job.data.status === "queued" || job.data.status === "processing") && (
                <button
                  type="button"
                  className="button-danger"
                  onClick={handleCancel}
                  disabled={cancelJob.isPending}
                >
                  <span aria-hidden="true">⏹</span>{" "}
                  {cancelJob.isPending ? "Cancelling…" : "Cancel job"}
                </button>
              )}
              {confirmingDelete ? (
                <>
                  <span className="hint">Delete this job? This can't be undone.</span>
                  <button
                    type="button"
                    className="button-danger"
                    onClick={handleDelete}
                    disabled={deleteJob.isPending}
                  >
                    {deleteJob.isPending ? "Deleting…" : "Yes, delete"}
                  </button>
                  <button type="button" onClick={() => setConfirmingDelete(false)}>
                    Cancel
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  className="button-danger"
                  onClick={() => setConfirmingDelete(true)}
                  disabled={job.data.status === "processing"}
                  title={
                    job.data.status === "processing"
                      ? "Can't delete a job that's currently processing."
                      : undefined
                  }
                >
                  <span aria-hidden="true">🗑</span> Delete
                </button>
              )}
            </div>
            {deleteJob.isError && <p className="error">Couldn't delete that job. Try again.</p>}
            {cancelJob.isError && <p className="error">Couldn't cancel that job. Try again.</p>}
            {createProjectFromJob.isError && <p className="error">Couldn't create a project from this job. Try again.</p>}
          </>
        )}
      </div>
    </div>
  );
}
