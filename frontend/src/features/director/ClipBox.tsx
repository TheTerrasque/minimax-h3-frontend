import type { Clip } from "../../api/directorTypes";
import { MODE_LABELS } from "../../api/types";
import { JobProgressBar } from "../queue/JobProgressBar";

function statusClass(clip: Clip): string {
  if (clip.current_job_status === "queued" || clip.current_job_status === "processing") return "clip-box-active";
  if (clip.current_job_status === "done" && !clip.video_url) return "clip-box-failed";
  if (clip.needs_render) return clip.current_job_id ? "clip-box-dirty" : "clip-box-new";
  return "clip-box-clean";
}

function statusLabel(clip: Clip): string {
  if (clip.current_job_status === "queued") return "Queued";
  if (clip.current_job_status === "processing") return "Rendering…";
  if (clip.current_job_status === "done" && !clip.video_url) return "Failed";
  if (clip.needs_render) return clip.current_job_id ? "Needs re-render" : "Not rendered";
  return "Up to date";
}

interface ClipBoxProps {
  clip: Clip;
  isFirst: boolean;
  isLast: boolean;
  onOpen: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
}

export function ClipBox({ clip, isFirst, isLast, onOpen, onMoveUp, onMoveDown }: ClipBoxProps) {
  return (
    <div className="clip-box-wrap">
      {clip.continues_previous && <div className="clip-chain-connector" aria-hidden="true" />}
      <div className={`clip-box ${statusClass(clip)}`}>
        <div className="clip-box-order-row">
          <span className="clip-box-order">#{clip.order + 1}</span>
          <div className="clip-box-move-buttons">
            <button type="button" disabled={isFirst} onClick={onMoveUp} aria-label="Move earlier" title="Move earlier">
              ◀
            </button>
            <button type="button" disabled={isLast} onClick={onMoveDown} aria-label="Move later" title="Move later">
              ▶
            </button>
          </div>
        </div>
        <button type="button" className="clip-box-thumb" onClick={onOpen}>
          {clip.thumbnail_url ? (
            <img src={clip.thumbnail_url} alt="" />
          ) : clip.video_url ? (
            <video src={clip.video_url} muted preload="metadata" />
          ) : (
            <span className="clip-box-placeholder">{MODE_LABELS[clip.mode][0]}</span>
          )}
        </button>
        <button type="button" className="clip-box-body" onClick={onOpen}>
          <span className="clip-box-mode">{MODE_LABELS[clip.mode]}</span>
          <span className="clip-box-prompt">{clip.prompt || <em>No prompt yet</em>}</span>
          <span className={`clip-box-status clip-box-status-${statusClass(clip)}`}>{statusLabel(clip)}</span>
          {clip.current_job_status === "processing" && (
            <JobProgressBar job={{ phase: clip.phase ?? "", progress_current: clip.progress_current, progress_total: clip.progress_total }} />
          )}
        </button>
      </div>
    </div>
  );
}
