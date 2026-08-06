import { useDeleteJob, useJob } from "../../api/queries";
import { MODE_LABELS, type GenerationJobDetail } from "../../api/types";

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

interface JobModalProps {
  jobId: number;
  onClose: () => void;
  onRedo: (job: GenerationJobDetail) => void;
}

export function JobModal({ jobId, onClose, onRedo }: JobModalProps) {
  const job = useJob(jobId);
  const deleteJob = useDeleteJob();

  async function handleDelete() {
    await deleteJob.mutateAsync(jobId);
    onClose();
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
            <h2>{MODE_LABELS[job.data.mode]}</h2>

            {job.data.status === "done" && job.data.video_url ? (
              <video src={job.data.video_url} controls className="modal-video" />
            ) : job.data.status === "done" ? (
              <p className="error">Failed: {job.data.error_message || "no video was produced."}</p>
            ) : (
              <p className="hint">
                {job.data.status === "processing" ? "Processing…" : "Queued…"}
                {job.data.expected_finish_time &&
                  ` Expected done by ${new Date(job.data.expected_finish_time).toLocaleTimeString()}.`}
              </p>
            )}

            <dl className="modal-details">
              <dt>Prompt</dt>
              <dd>{job.data.raw_prompt}</dd>
              {job.data.improved_prompt && (
                <>
                  <dt>AI-refined prompt</dt>
                  <dd>{job.data.improved_prompt}</dd>
                </>
              )}
              <dt>Resolution &amp; length</dt>
              <dd>
                {job.data.width}×{job.data.height} ({job.data.aspect_ratio}, {job.data.megapixels}MP) —{" "}
                {job.data.duration_seconds}s
              </dd>
              <dt>Render time</dt>
              <dd>{renderTimeLabel(job.data)}</dd>
            </dl>

            <div className="modal-actions">
              {job.data.video_url && (
                <a href={job.data.video_url} download className="button">
                  Download
                </a>
              )}
              <button type="button" onClick={() => onRedo(job.data)}>
                Redo
              </button>
              <button
                type="button"
                onClick={handleDelete}
                disabled={job.data.status === "processing" || deleteJob.isPending}
                title={
                  job.data.status === "processing"
                    ? "Can't delete a job that's currently processing."
                    : undefined
                }
              >
                {deleteJob.isPending ? "Deleting…" : "Delete"}
              </button>
            </div>
            {deleteJob.isError && <p className="error">Couldn't delete that job. Try again.</p>}
          </>
        )}
      </div>
    </div>
  );
}
