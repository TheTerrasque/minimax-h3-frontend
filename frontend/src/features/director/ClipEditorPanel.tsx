import { useEffect, useState } from "react";
import {
  useAddClipReference,
  useCancelClip,
  useDeleteClip,
  useDeleteClipReference,
  useRenderClip,
  useUpdateClip,
} from "../../api/directorQueries";
import type { Clip } from "../../api/directorTypes";
import { useChatReply, useConfig, usePresets, useRefinePrompt } from "../../api/queries";
import {
  CONTINUATION_CAPABLE_MODES,
  MAX_REFERENCE_AUDIO,
  MAX_REFERENCE_IMAGES,
  MAX_REFERENCE_VIDEO,
  MODE_LABELS,
  REFERENCE_FLOW_MODES,
  type ChatMessage,
} from "../../api/types";
import { DropZone } from "../shared/DropZone";
import { ChatModal } from "../generate/ChatModal";
import { JobProgressBar } from "../queue/JobProgressBar";

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
}

interface ClipEditorPanelProps {
  projectId: number;
  clip: Clip;
  isFirstClip: boolean;
  onClose: () => void;
}

export function ClipEditorPanel({ projectId, clip, isFirstClip, onClose }: ClipEditorPanelProps) {
  const config = useConfig();
  const presets = usePresets(clip.mode);
  const updateClip = useUpdateClip();
  const deleteClip = useDeleteClip();
  const renderClip = useRenderClip();
  const cancelClip = useCancelClip();
  const addReference = useAddClipReference();
  const deleteReference = useDeleteClipReference();
  const refinePrompt = useRefinePrompt();
  const chatReply = useChatReply();

  const [promptDraft, setPromptDraft] = useState(clip.prompt);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");

  // Reset local UI state when this panel is reused for a different clip
  // (the parent keeps one panel instance around rather than remounting).
  useEffect(() => {
    setPromptDraft(clip.prompt);
    setConfirmingDelete(false);
    setChatOpen(false);
    setChatMessages([]);
  }, [clip.id, clip.prompt]);

  const canContinue = CONTINUATION_CAPABLE_MODES.has(clip.mode) && !isFirstClip;
  const referenceLabels = clip.references.map((r) => r.label);

  const currentPreset = presets.data?.find((p) => p.id === clip.preset_id) ?? null;
  // Quality (megapixels) is locked while continues_previous -- resolution
  // is inherited from the predecessor regardless of preset (see backend
  // director/api.py's clip_detail PATCH: MiniMaxH3ChainPlan's width/height
  // apply to every scene in a run, extras.md#contex-loop) -- so only this
  // clip's own preset's length options are offered, not a full tier switch.
  const availablePresets = clip.continues_previous ? (currentPreset ? [currentPreset] : []) : (presets.data ?? []);
  const durations = currentPreset?.durations ?? [];
  const selectedDuration = durations.find((d) => d.id === clip.duration_id) ?? null;
  const selectedDurationIndex = selectedDuration ? durations.indexOf(selectedDuration) : 0;

  const isBusy = clip.current_job_status === "queued" || clip.current_job_status === "processing";
  const failed = clip.current_job_status === "done" && !clip.video_url;

  async function savePrompt() {
    if (promptDraft === clip.prompt) return;
    await updateClip.mutateAsync({ projectId, clipId: clip.id, prompt: promptDraft });
  }

  async function handleRefine() {
    if (!promptDraft.trim()) return;
    const result = await refinePrompt.mutateAsync({ mode: clip.mode, rawPrompt: promptDraft, referenceLabels });
    await updateClip.mutateAsync({ projectId, clipId: clip.id, improvedPrompt: result.improved_prompt });
  }

  async function handleSendChat() {
    if (!chatInput.trim()) return;
    const content = chatInput.trim();
    const history = chatMessages;
    setChatMessages((prev) => [...prev, { role: "user", content }]);
    setChatInput("");
    try {
      const reply = await chatReply.mutateAsync({
        mode: clip.mode,
        history,
        content,
        rawPrompt: promptDraft,
        improvedPrompt: clip.improved_prompt,
        referenceLabels,
      });
      setChatMessages((prev) => [...prev, reply]);
    } catch {
      // chatReply.isError reflects this -- see the chat panel's own error message.
    }
  }

  async function handleUseAsPrompt(text: string) {
    setPromptDraft(text);
    await updateClip.mutateAsync({ projectId, clipId: clip.id, prompt: text, improvedPrompt: "" });
    setChatOpen(false);
  }

  async function handlePresetChange(presetId: number) {
    const preset = availablePresets.find((p) => p.id === presetId);
    const nextDuration = preset?.durations[0];
    if (nextDuration) await updateClip.mutateAsync({ projectId, clipId: clip.id, durationId: nextDuration.id });
  }

  async function handleDurationChange(durationId: number) {
    await updateClip.mutateAsync({ projectId, clipId: clip.id, durationId });
  }

  async function handleAspectRatioChange(aspectRatio: string) {
    await updateClip.mutateAsync({ projectId, clipId: clip.id, aspectRatio });
  }

  async function handleContinuesToggle(value: boolean) {
    await updateClip.mutateAsync({ projectId, clipId: clip.id, continuesPrevious: value });
  }

  async function handleDelete() {
    await deleteClip.mutateAsync({ projectId, clipId: clip.id });
    onClose();
  }

  const imageRefs = clip.references.filter((r) => r.kind === "image");
  const audioRefs = clip.references.filter((r) => r.kind === "audio");
  const videoRefs = clip.references.filter((r) => r.kind === "video");
  const maxImages = MAX_REFERENCE_IMAGES[clip.mode];
  const maxAudio = MAX_REFERENCE_AUDIO[clip.mode];
  const maxVideoRefs = MAX_REFERENCE_VIDEO[clip.mode];
  const isReferenceFlow = REFERENCE_FLOW_MODES.includes(clip.mode);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal modal-wide" onClick={(e) => e.stopPropagation()}>
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
          ×
        </button>
        <h2>
          Clip #{clip.order + 1} <span className="hint modal-mode-label">{MODE_LABELS[clip.mode]}</span>
        </h2>

        {clip.video_url ? (
          <video src={clip.video_url} controls className="modal-video" />
        ) : failed ? (
          <p className="error">Failed: {clip.error_message || "no output was produced."}</p>
        ) : isBusy ? (
          <>
            <p className="hint">{clip.current_job_status === "processing" ? "Rendering…" : "Queued…"}</p>
            {clip.current_job_status === "processing" && (
              <JobProgressBar
                job={{ phase: clip.phase ?? "", progress_current: clip.progress_current, progress_total: clip.progress_total }}
              />
            )}
          </>
        ) : (
          <p className="hint">Not rendered yet.</p>
        )}

        <fieldset className="prompt-fieldset">
          <legend>Prompt</legend>
          <textarea
            className="prompt-input"
            rows={6}
            value={promptDraft}
            onChange={(e) => setPromptDraft(e.target.value)}
            onBlur={() => void savePrompt()}
            placeholder="Describe this clip…"
          />
          {config.data?.llm_enabled && (
            <div className="prompt-actions">
              <button type="button" onClick={() => void handleRefine()} disabled={refinePrompt.isPending || !promptDraft.trim()}>
                {refinePrompt.isPending ? "Refining…" : "AI refine"}
              </button>
              <button type="button" onClick={() => setChatOpen(true)} disabled={chatOpen}>
                {chatOpen ? "Chat open" : "Chat with AI"}
              </button>
            </div>
          )}
          {refinePrompt.isError && <p className="error">AI refine failed. Try again.</p>}
          {clip.improved_prompt && (
            <div className="improved-prompt">
              <p className="hint">AI-refined version — this is what will actually be rendered:</p>
              <p className="improved-prompt-text">{clip.improved_prompt}</p>
              <div className="prompt-actions">
                <button type="button" onClick={() => void handleUseAsPrompt(clip.improved_prompt)}>
                  Edit as prompt
                </button>
                <button
                  type="button"
                  onClick={() => void updateClip.mutateAsync({ projectId, clipId: clip.id, improvedPrompt: "" })}
                >
                  Discard
                </button>
              </div>
            </div>
          )}
        </fieldset>

        <div className="toolbar">
          {clip.continues_previous ? (
            <p className="hint clip-editor-locked-note">
              Quality/resolution locked to the predecessor while continuing it — only length can change here.
            </p>
          ) : (
            <label className="toolbar-control">
              <span>Quality</span>
              <select
                value={currentPreset?.id ?? ""}
                onChange={(e) => void handlePresetChange(Number(e.target.value))}
                disabled={!availablePresets.length}
              >
                {availablePresets.map((preset) => (
                  <option key={preset.id} value={preset.id}>
                    {preset.label} ({preset.megapixels}MP{preset.is_draft ? ", draft" : ""})
                  </option>
                ))}
              </select>
            </label>
          )}

          <label className="toolbar-control toolbar-control-wide">
            <span>
              Length: {selectedDuration ? `${selectedDuration.duration_seconds}s` : "—"}
              {selectedDuration && ` (~${formatDuration(selectedDuration.estimated_render_seconds)} to render)`}
            </span>
            <input
              type="range"
              min={0}
              max={Math.max(durations.length - 1, 0)}
              step={1}
              value={selectedDurationIndex}
              disabled={durations.length < 2}
              onChange={(e) => {
                const d = durations[Number(e.target.value)];
                if (d) void handleDurationChange(d.id);
              }}
            />
          </label>

          {!clip.continues_previous && (
            <label className="toolbar-control">
              <span>Aspect ratio</span>
              <select value={clip.aspect_ratio} onChange={(e) => void handleAspectRatioChange(e.target.value)}>
                {config.data?.aspect_ratios.map((ratio) => (
                  <option key={ratio.value} value={ratio.value}>
                    {ratio.label}
                  </option>
                ))}
                {!config.data?.aspect_ratios.some((r) => r.value === clip.aspect_ratio) && (
                  <option value={clip.aspect_ratio}>{clip.aspect_ratio}</option>
                )}
              </select>
            </label>
          )}
        </div>

        <label className="clip-editor-continues-toggle">
          <input
            type="checkbox"
            checked={clip.continues_previous}
            disabled={!canContinue}
            onChange={(e) => void handleContinuesToggle(e.target.checked)}
          />
          Continue from the previous clip (motion/audio continuity)
          {!canContinue && (
            <span className="hint">
              {" "}
              — {isFirstClip ? "not available for the first clip" : `not available for ${MODE_LABELS[clip.mode]}`}
            </span>
          )}
        </label>

        {clip.mode === "i2v" && (
          <fieldset>
            <legend>Reference frames</legend>
            <p className="hint">
              {clip.continues_previous
                ? "Optional — defaults to the previous clip's last frame if left empty."
                : "Click, drag & drop, or paste an image into either slot."}
            </p>
            <div className="reference-row">
              {imageRefs[0] ? (
                <div className="file-slot">
                  <div className="ref-thumb-row">
                    <img src={imageRefs[0].url ?? ""} className="ref-thumb" alt="First frame" />
                    <button
                      type="button"
                      onClick={() => deleteReference.mutate({ projectId, referenceId: imageRefs[0].id })}
                    >
                      Remove
                    </button>
                  </div>
                </div>
              ) : (
                <DropZone
                  accept="image/*"
                  className="file-slot"
                  onFiles={(files) => addReference.mutate({ projectId, clipId: clip.id, kind: "image", file: files[0] })}
                >
                  First frame
                  <input
                    type="file"
                    accept="image/*"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) addReference.mutate({ projectId, clipId: clip.id, kind: "image", file });
                      e.target.value = "";
                    }}
                  />
                </DropZone>
              )}
              {imageRefs[0] &&
                (imageRefs[1] ? (
                  <div className="file-slot">
                    <div className="ref-thumb-row">
                      <img src={imageRefs[1].url ?? ""} className="ref-thumb" alt="Last frame" />
                      <button
                        type="button"
                        onClick={() => deleteReference.mutate({ projectId, referenceId: imageRefs[1].id })}
                      >
                        Remove
                      </button>
                    </div>
                  </div>
                ) : (
                  <DropZone
                    accept="image/*"
                    className="file-slot"
                    onFiles={(files) => addReference.mutate({ projectId, clipId: clip.id, kind: "image", file: files[0] })}
                  >
                    Last frame (optional)
                    <input
                      type="file"
                      accept="image/*"
                      onChange={(e) => {
                        const file = e.target.files?.[0];
                        if (file) addReference.mutate({ projectId, clipId: clip.id, kind: "image", file });
                        e.target.value = "";
                      }}
                    />
                  </DropZone>
                ))}
            </div>
          </fieldset>
        )}

        {isReferenceFlow && (
          <>
            {([
              ["image", imageRefs, maxImages, "image/*", "Reference images"],
              ["audio", audioRefs, maxAudio, "audio/*", "Reference audio"],
              ["video", videoRefs, maxVideoRefs, "video/*", "Reference videos"],
            ] as const).map(([kind, refs, max, accept, label]) =>
              max > 0 ? (
                <fieldset key={kind}>
                  <legend>
                    {label} ({refs.length}/{max})
                  </legend>
                  {refs.length > 0 && (
                    <ul className="reference-list">
                      {refs.map((ref) => (
                        <li key={ref.id} className="reference-item">
                          <span>{ref.label}</span>
                          <button type="button" onClick={() => deleteReference.mutate({ projectId, referenceId: ref.id })}>
                            Remove
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                  {refs.length < max && (
                    <DropZone
                      accept={accept}
                      className="file-slot"
                      onFiles={(files) => addReference.mutate({ projectId, clipId: clip.id, kind, file: files[0] })}
                    >
                      Add {kind}
                      <input
                        type="file"
                        accept={accept}
                        onChange={(e) => {
                          const file = e.target.files?.[0];
                          if (file) addReference.mutate({ projectId, clipId: clip.id, kind, file });
                          e.target.value = "";
                        }}
                      />
                    </DropZone>
                  )}
                </fieldset>
              ) : null,
            )}
          </>
        )}

        <div className="modal-actions">
          {clip.video_url && (
            <a href={clip.video_url} download className="button button-primary">
              <span aria-hidden="true">⬇</span> Download
            </a>
          )}
          {isBusy ? (
            <button type="button" className="button-danger" onClick={() => cancelClip.mutate({ projectId, clipId: clip.id })} disabled={cancelClip.isPending}>
              <span aria-hidden="true">⏹</span> {cancelClip.isPending ? "Cancelling…" : "Cancel render"}
            </button>
          ) : (
            <button
              type="button"
              className="button button-primary"
              onClick={() => renderClip.mutate({ projectId, clipId: clip.id })}
              disabled={renderClip.isPending || !clip.needs_render}
            >
              {renderClip.isPending ? "Starting…" : clip.needs_render ? "Render" : "Rendered"}
            </button>
          )}
          {confirmingDelete ? (
            <>
              <span className="hint">Delete this clip? This can't be undone.</span>
              <button type="button" className="button-danger" onClick={() => void handleDelete()} disabled={deleteClip.isPending}>
                {deleteClip.isPending ? "Deleting…" : "Yes, delete"}
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
              disabled={isBusy}
              title={isBusy ? "Can't delete a clip that's currently rendering." : undefined}
            >
              <span aria-hidden="true">🗑</span> Delete
            </button>
          )}
        </div>
        {renderClip.isError && <p className="error">Couldn't start that render. Try again.</p>}
        {deleteClip.isError && <p className="error">Couldn't delete that clip. Try again.</p>}

        {chatOpen && (
          <ChatModal
            messages={chatMessages}
            input={chatInput}
            onInputChange={setChatInput}
            onSend={() => void handleSendChat()}
            isPending={chatReply.isPending}
            isError={chatReply.isError}
            onUseAsPrompt={(text) => void handleUseAsPrompt(text)}
            onClose={() => setChatOpen(false)}
          />
        )}
      </div>
    </div>
  );
}
