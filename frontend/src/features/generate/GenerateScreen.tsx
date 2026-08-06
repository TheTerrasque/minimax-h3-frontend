import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  useChatReply,
  useConfig,
  useCreateJob,
  usePresets,
  useQueueEstimate,
  useRefinePrompt,
} from "../../api/queries";
import {
  CONTENT_TYPE_BY_MODE,
  MODE_LABELS,
  MODES_BY_CONTENT_TYPE,
  type ChatMessage,
  type ContentType,
  type GenerationJobDetail,
  type Mode,
  type ReferenceAsset,
} from "../../api/types";
import { ChatModal } from "./ChatModal";

const CONTENT_TYPES: ContentType[] = ["video", "image", "audio"];
const CONTENT_TYPE_LABELS: Record<ContentType, string> = {
  video: "Video",
  image: "Image",
  audio: "Audio",
};
// Both reuse the video pipeline at extreme settings rather than a purpose-
// built path (see backend's Mode docstring) -- results are noticeably less
// reliable than video's, so this is flagged rather than presented as an
// equally-supported option.
const EXPERIMENTAL_CONTENT_TYPES = new Set<ContentType>(["image", "audio"]);
const CONTENT_TYPE_NOUN: Record<ContentType, string> = {
  video: "video",
  image: "image",
  audio: "audio",
};

// Only r2v/r2i/r2a actually take reference uploads (see api.py's
// _MAX_REFERENCE_IMAGES/_MAX_REFERENCE_AUDIO below) -- i2v is its own
// separate first/last-frame flow, not a "reference" one.
const REFERENCE_FLOW_MODES: Mode[] = ["r2v", "r2i", "r2a"];

// Mirrors generation/api.py's _MAX_REFERENCE_IMAGES/_MAX_REFERENCE_AUDIO --
// what tasks.py actually wires into the ComfyUI workflow per mode (see
// ARCHITECTURE.md). r2i gets 0 reference audio -- a still frame extracted
// from the underlying render can't carry it, so offering the upload would
// just be confusing (see backend's own comment on this).
const MAX_REFERENCE_IMAGES: Record<Mode, number> = {
  t2v: 0,
  i2v: 2,
  r2v: 9,
  t2i: 0,
  r2i: 9,
  t2a: 0,
  r2a: 9,
};
const MAX_REFERENCE_AUDIO: Record<Mode, number> = {
  t2v: 0,
  i2v: 0,
  r2v: 3,
  t2i: 0,
  r2i: 0,
  t2a: 0,
  r2a: 3,
};

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
}

function presetLabel(preset: { label: string; megapixels: number; is_draft: boolean }): string {
  return `${preset.label} (${preset.megapixels}MP${preset.is_draft ? ", draft" : ""})`;
}

// Creates an object URL for a locally-staged file (thumbnail preview before
// upload) and revokes it on change/unmount -- object URLs otherwise leak for
// the page's lifetime.
function useObjectUrl(file: File | null): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!file) {
      setUrl(null);
      return;
    }
    const objectUrl = URL.createObjectURL(file);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [file]);
  return url;
}

function useObjectUrls(files: File[]): string[] {
  const [urls, setUrls] = useState<string[]>([]);
  useEffect(() => {
    const objectUrls = files.map((f) => URL.createObjectURL(f));
    setUrls(objectUrls);
    return () => {
      for (const u of objectUrls) URL.revokeObjectURL(u);
    };
  }, [files]);
  return urls;
}

function hasUrl(r: ReferenceAsset): r is ReferenceAsset & { url: string } {
  return r.url != null;
}

function gcd(a: number, b: number): number {
  return b === 0 ? a : gcd(b, a % b);
}

interface MatchedAspectRatio {
  value: string;
  label: string;
}

// Reads an uploaded first-frame image's own pixel dimensions and reduces
// them to a compact "W:H" ratio string -- lets the aspect-ratio picker offer
// (and default to) the exact ratio of what's actually being animated,
// instead of forcing it into the nearest fixed preset. Backend's
// aspect_ratio column caps at 10 chars ("W:H") and resolution.
// is_valid_aspect_ratio() bounds each part to 4 digits, so a
// near-coprime-dimensioned image (rare, but possible) gets scaled down to
// fit rather than rejected.
async function computeImageAspectRatio(file: File): Promise<MatchedAspectRatio | null> {
  const url = URL.createObjectURL(file);
  try {
    const { width, height } = await new Promise<{ width: number; height: number }>((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve({ width: img.naturalWidth, height: img.naturalHeight });
      img.onerror = () => reject(new Error("Couldn't read image dimensions"));
      img.src = url;
    });
    if (!width || !height) return null;
    const divisor = gcd(width, height) || 1;
    let w = Math.round(width / divisor);
    let h = Math.round(height / divisor);
    if (w > 999 || h > 999) {
      const scale = 999 / Math.max(w, h);
      w = Math.max(1, Math.round(w * scale));
      h = Math.max(1, Math.round(h * scale));
    }
    const value = `${w}:${h}`;
    return { value, label: `${value} (match first frame)` };
  } catch {
    return null;
  } finally {
    URL.revokeObjectURL(url);
  }
}

// Re-downloads an already-uploaded reference (used by "Redo") and repacks
// it as a File -- a browser has no way to hand back the original File
// object for something already uploaded, but the bytes are still sitting
// at their (same-origin, session-cookie-authed) media URL, so refetching
// them is the only way to actually restore it rather than leaving the
// slot empty.
async function fetchAsFile(ref: ReferenceAsset & { url: string }): Promise<File> {
  const resp = await fetch(ref.url, { credentials: "include" });
  if (!resp.ok) throw new Error(`Failed to fetch ${ref.url}: ${resp.status}`);
  const blob = await resp.blob();
  const filename = ref.url.split("/").pop()?.split("?")[0] || ref.label;
  return new File([blob], filename, { type: blob.type });
}

interface GenerateScreenProps {
  redoJob: GenerationJobDetail | null;
  onRedoConsumed: () => void;
}

export function GenerateScreen({ redoJob, onRedoConsumed }: GenerateScreenProps) {
  const config = useConfig();

  const [mode, setMode] = useState<Mode>("t2v");
  // Derived, not separate state -- mode is the single source of truth, and
  // every mode belongs to exactly one content type (see CONTENT_TYPE_BY_MODE).
  const contentType = CONTENT_TYPE_BY_MODE[mode];
  const presets = usePresets(mode);
  const [presetId, setPresetId] = useState<number | null>(null);
  const [durationId, setDurationId] = useState<number | null>(null);
  const [aspectRatio, setAspectRatio] = useState<string | null>(null);
  // Set by a "Redo" action (see JobModal) while waiting for `mode`'s presets
  // to load, so the matching preset/duration can be resolved once they do --
  // see the two effects below.
  const [pendingRedoDurationId, setPendingRedoDurationId] = useState<number | null>(null);

  const [rawPrompt, setRawPrompt] = useState("");
  const [improvedPrompt, setImprovedPrompt] = useState("");

  const [firstFrame, setFirstFrame] = useState<File | null>(null);
  const [lastFrame, setLastFrame] = useState<File | null>(null);
  // Bumped on removal to force the (uncontrolled) file input to remount --
  // otherwise its native DOM value stays set after we clear firstFrame/
  // lastFrame, so re-picking the same file again wouldn't fire onChange.
  const [firstFrameKey, setFirstFrameKey] = useState(0);
  const [lastFrameKey, setLastFrameKey] = useState(0);
  const [refImages, setRefImages] = useState<File[]>([]);
  const [referenceAudio, setReferenceAudio] = useState<File[]>([]);
  // The first frame's own aspect ratio, offered (and auto-selected) as an
  // extra option in the aspect-ratio picker -- see computeImageAspectRatio.
  const [matchedAspectRatio, setMatchedAspectRatio] = useState<MatchedAspectRatio | null>(null);

  const [chatOpen, setChatOpen] = useState(false);
  // Which redo's async reference-restore is currently "live" -- see the
  // redo effect below. Not a plain cleanup-based cancel flag: calling
  // onRedoConsumed() sets the redoJob prop back to null as part of the
  // very same effect run, which would otherwise look identical to the
  // effect being superseded and cancel the restore before its fetches
  // even resolve.
  const activeRedoIdRef = useRef<number | null>(null);
  // Entirely client-side -- never persisted until (if) the job it drafted
  // actually gets queued, see handleSubmit's chatTranscript. Refreshing the
  // page loses an unqueued chat, by design (a user request: no DB trail for
  // chat content that doesn't end up backing a real job).
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");

  const refinePrompt = useRefinePrompt();
  const chatReply = useChatReply();
  const createJob = useCreateJob();

  // Reset mode-specific state whenever the mode changes -- a preset/reference
  // picked for one mode isn't meaningful for another. (aspectRatio is NOT
  // mode-specific -- it's preserved across a mode switch.)
  useEffect(() => {
    setPresetId(null);
    setDurationId(null);
    setFirstFrame(null);
    setLastFrame(null);
    setRefImages([]);
    setReferenceAudio([]);
    setChatOpen(false);
    setChatMessages([]);
  }, [mode]);

  // "Redo" (see JobModal): reload the same mode/ratio/prompt, flag which
  // duration to resolve to once that mode's presets have (re)loaded, and
  // re-fetch the job's reference files from their (already-uploaded) media
  // URLs -- a File object itself can't be recovered client-side, but its
  // bytes are still sitting at ref.url, so re-downloading them and
  // repacking as a fresh File actually restores the slot instead of
  // leaving it empty.
  useEffect(() => {
    if (!redoJob) return;
    setMode(redoJob.mode);
    setAspectRatio(redoJob.aspect_ratio);
    setRawPrompt(redoJob.raw_prompt);
    setImprovedPrompt(redoJob.improved_prompt || "");
    setPendingRedoDurationId(redoJob.duration_id);

    const redoId = redoJob.id;
    activeRedoIdRef.current = redoId;
    (async () => {
      const images = redoJob.references.filter((r) => r.kind === "image").filter(hasUrl);
      const audioRefs = redoJob.references.filter((r) => r.kind === "audio").filter(hasUrl);
      images.sort((a, b) => a.order - b.order);
      audioRefs.sort((a, b) => a.order - b.order);

      try {
        if (redoJob.mode === "i2v") {
          const [first, last] = images;
          const [firstFile, lastFile] = await Promise.all([
            first ? fetchAsFile(first) : null,
            last ? fetchAsFile(last) : null,
          ]);
          if (activeRedoIdRef.current !== redoId) return; // superseded by a newer redo
          setFirstFrame(firstFile);
          setLastFrame(lastFile);
        } else if (REFERENCE_FLOW_MODES.includes(redoJob.mode)) {
          const [imageFiles, audioFiles] = await Promise.all([
            Promise.all(images.map(fetchAsFile)),
            Promise.all(audioRefs.map(fetchAsFile)),
          ]);
          if (activeRedoIdRef.current !== redoId) return; // superseded by a newer redo
          setRefImages(imageFiles);
          setReferenceAudio(audioFiles);
        }
      } catch (err) {
        // Best-effort: if a reference can't be re-fetched for some reason,
        // leave that slot empty rather than blocking the rest of the redo.
        console.error("Redo: failed to restore a reference file", err);
      }
    })();

    onRedoConsumed();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only re-run when a new redo job arrives
  }, [redoJob]);

  // Default to the first non-draft preset (megapixels tier) once presets
  // load -- skipped while a redo resolution is pending (that one picks a
  // specific preset instead, see below).
  useEffect(() => {
    if (presetId != null || !presets.data?.length || pendingRedoDurationId != null) return;
    const preferred = presets.data.find((p) => !p.is_draft) ?? presets.data[0];
    setPresetId(preferred.id);
  }, [presets.data, presetId, pendingRedoDurationId]);

  // Resolve a pending redo once this mode's presets have loaded: find the
  // tier that actually offers the requested duration.
  useEffect(() => {
    if (pendingRedoDurationId == null || !presets.data?.length) return;
    const match = presets.data.find((p) => p.durations.some((d) => d.id === pendingRedoDurationId));
    if (match) {
      setPresetId(match.id);
      setDurationId(pendingRedoDurationId);
    }
    setPendingRedoDurationId(null);
  }, [pendingRedoDurationId, presets.data]);

  const selectedPreset = presets.data?.find((p) => p.id === presetId) ?? null;
  const durations = selectedPreset?.durations ?? [];

  // Tracks the last-selected clip length in seconds -- separate from
  // durationId because once the preset (tier) changes, `durations` below
  // is already the *new* tier's list, which the *old* durationId was never
  // a member of; looking the old id up in the new array (an earlier bug)
  // always came back empty and silently fell back to the tier's first
  // option instead of actually preserving the user's chosen length.
  const lastDurationSecondsRef = useRef<number | null>(null);

  // Keeps the selected duration valid whenever the available list changes
  // (preset/tier switch, or a redo landing on a tier that doesn't offer
  // the exact id it targeted): keeps the same clip length if the new tier
  // offers it, otherwise picks the *nearest* available length rather than
  // always resetting to the tier's first (shortest) option.
  useEffect(() => {
    if (!durations.length) return;
    const current = durations.find((d) => d.id === durationId);
    if (current) {
      lastDurationSecondsRef.current = current.duration_seconds;
      return;
    }
    const target = lastDurationSecondsRef.current;
    const next =
      target == null
        ? durations[0]
        : durations.reduce((best, d) =>
            Math.abs(d.duration_seconds - target) < Math.abs(best.duration_seconds - target) ? d : best,
          );
    lastDurationSecondsRef.current = next.duration_seconds;
    setDurationId(next.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- durations is a fresh array each render
  }, [durations, durationId]);

  // Default the aspect ratio once config loads.
  useEffect(() => {
    if (aspectRatio != null || !config.data) return;
    setAspectRatio(config.data.default_aspect_ratio);
  }, [config.data, aspectRatio]);

  // i2v's first frame gets its own aspect-ratio option, auto-selected, so
  // the render actually matches what's being animated instead of forcing it
  // into the nearest fixed preset -- see computeImageAspectRatio.
  useEffect(() => {
    if (mode !== "i2v" || !firstFrame) {
      // Leaving i2v (or clearing the first frame, including handleSubmit's
      // post-queue reset) drops the matched option from the dropdown --
      // if it was still the selected value, aspectRatio would otherwise be
      // left pointing at a value with no matching <option>, which renders
      // as whatever the first preset happens to be ("1:1") rather than
      // actually reverting to the site default.
      if (matchedAspectRatio && aspectRatio === matchedAspectRatio.value) {
        setAspectRatio(config.data?.default_aspect_ratio ?? null);
      }
      setMatchedAspectRatio(null);
      return;
    }
    let cancelled = false;
    void computeImageAspectRatio(firstFrame).then((ratio) => {
      if (cancelled || !ratio) return;
      setMatchedAspectRatio(ratio);
      setAspectRatio(ratio.value);
    });
    return () => {
      cancelled = true;
    };
    // matchedAspectRatio/aspectRatio/config.data are read for their latest values (closure),
    // deliberately not watched -- otherwise a manual aspectRatio pick while firstFrame is
    // still set would get immediately clobbered back to the matched value.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, firstFrame]);

  const queueEstimate = useQueueEstimate(durationId);

  const referenceImages = useMemo<File[]>(() => {
    if (mode === "i2v") return [firstFrame, lastFrame].filter((f): f is File => f != null);
    if (REFERENCE_FLOW_MODES.includes(mode)) return refImages;
    return [];
  }, [mode, firstFrame, lastFrame, refImages]);

  const referenceLabels = useMemo(() => {
    const imageLabels = referenceImages.map((_, i) => `Picture ${i + 1}`);
    const audioLabels =
      MAX_REFERENCE_AUDIO[mode] > 0 ? referenceAudio.map((_, i) => `Audio ${i + 1}`) : [];
    return [...imageLabels, ...audioLabels];
  }, [referenceImages, referenceAudio, mode]);

  const firstFrameUrl = useObjectUrl(firstFrame);
  const lastFrameUrl = useObjectUrl(lastFrame);
  const refImageUrls = useObjectUrls(refImages);

  function insertToken(token: string) {
    setRawPrompt((prev) => (prev.trim() ? `${prev.trim()} ${token}` : token));
  }

  async function handleRefine() {
    if (!rawPrompt.trim()) return;
    const result = await refinePrompt.mutateAsync({
      mode,
      rawPrompt,
      referenceLabels,
      durationSeconds: selectedDuration?.duration_seconds,
      referenceImages: config.data?.llm_vision_enabled ? referenceImages : undefined,
    });
    setImprovedPrompt(result.improved_prompt);
  }

  function handleOpenChat() {
    setChatOpen(true);
  }

  async function handleSendChat() {
    if (!chatInput.trim()) return;
    const content = chatInput.trim();
    const history = chatMessages; // prior turns only -- the backend appends `content` itself
    setChatMessages((prev) => [...prev, { role: "user", content }]);
    setChatInput("");
    try {
      const reply = await chatReply.mutateAsync({
        mode,
        history,
        content,
        rawPrompt,
        improvedPrompt,
        durationSeconds: selectedDuration?.duration_seconds,
        referenceLabels,
        referenceImages: config.data?.llm_vision_enabled ? referenceImages : undefined,
      });
      setChatMessages((prev) => [...prev, reply]);
    } catch {
      // chatReply.isError reflects this -- see the chat panel's error message.
    }
  }

  function handleRemoveFirstFrame() {
    setFirstFrame(null);
    setFirstFrameKey((k) => k + 1);
  }

  function handleRemoveLastFrame() {
    setLastFrame(null);
    setLastFrameKey((k) => k + 1);
  }

  function handleRemoveRefImage(index: number) {
    setRefImages((prev) => prev.filter((_, i) => i !== index));
  }

  function handleRemoveRefAudio(index: number) {
    setReferenceAudio((prev) => prev.filter((_, i) => i !== index));
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!durationId || !aspectRatio || !rawPrompt.trim()) return;
    // Queuing just adds the job to the always-visible QueueSidebar (it
    // reactively refetches via useCreateJob's cache invalidation) -- no
    // modal, no navigation; the user stays on the form to queue another.
    await createJob.mutateAsync({
      mode,
      durationId,
      aspectRatio,
      rawPrompt,
      improvedPrompt: improvedPrompt || undefined,
      referenceImages,
      referenceAudio: MAX_REFERENCE_AUDIO[mode] > 0 ? referenceAudio : undefined,
      chatTranscript: chatMessages.length ? chatMessages : undefined,
    });
    setRawPrompt("");
    setImprovedPrompt("");
    setFirstFrame(null);
    setLastFrame(null);
    setRefImages([]);
    setReferenceAudio([]);
    setChatOpen(false);
    setChatMessages([]);
  }

  const maxImages = MAX_REFERENCE_IMAGES[mode];
  const maxAudio = MAX_REFERENCE_AUDIO[mode];
  const canSubmit =
    Boolean(durationId) && Boolean(aspectRatio) && rawPrompt.trim().length > 0 && !createJob.isPending;
  const selectedDuration = durations.find((d) => d.id === durationId) ?? null;
  const selectedDurationIndex = selectedDuration ? durations.indexOf(selectedDuration) : 0;

  return (
    <section className="generate-screen">
      <div className="tab-strip content-tabs" role="tablist" aria-label="Content type">
        {CONTENT_TYPES.map((ct) => (
          <button
            key={ct}
            type="button"
            className={`tab ${contentType === ct ? "selected" : ""}`}
            aria-selected={contentType === ct}
            onClick={() => setMode(MODES_BY_CONTENT_TYPE[ct][0])}
          >
            {CONTENT_TYPE_LABELS[ct]}
            {EXPERIMENTAL_CONTENT_TYPES.has(ct) && <span className="tab-badge">Experimental</span>}
          </button>
        ))}
      </div>

      {EXPERIMENTAL_CONTENT_TYPES.has(contentType) && (
        <p className="hint experimental-notice">
          {CONTENT_TYPE_LABELS[contentType]} generation is experimental — it reuses the video
          pipeline at extreme settings rather than a purpose-built path, and results are less
          consistent than video's.
        </p>
      )}

      <div className="tab-strip mode-tabs" role="tablist" aria-label="Generation mode">
        {MODES_BY_CONTENT_TYPE[contentType].map((m) => (
          <button
            key={m}
            type="button"
            className={`tab ${mode === m ? "selected" : ""}`}
            aria-selected={mode === m}
            onClick={() => setMode(m)}
          >
            {MODE_LABELS[m]}
          </button>
        ))}
      </div>

      <form onSubmit={handleSubmit} className="generate-form">
        <div className="toolbar">
          {presets.isError && <p className="error">Couldn't load presets.</p>}
          <label className="toolbar-control">
            <span>Quality</span>
            <select
              value={presetId ?? ""}
              onChange={(e) => setPresetId(Number(e.target.value))}
              disabled={!presets.data?.length}
            >
              {presets.data?.map((preset) => (
                <option key={preset.id} value={preset.id}>
                  {presetLabel(preset)}
                </option>
              ))}
            </select>
          </label>

          {contentType !== "audio" && (
            <label className="toolbar-control">
              <span>Aspect ratio</span>
              <select
                value={aspectRatio ?? ""}
                onChange={(e) => setAspectRatio(e.target.value)}
                disabled={!config.data?.aspect_ratios.length}
              >
                {matchedAspectRatio &&
                  !config.data?.aspect_ratios.some((r) => r.value === matchedAspectRatio.value) && (
                    <option value={matchedAspectRatio.value}>{matchedAspectRatio.label}</option>
                  )}
                {config.data?.aspect_ratios.map((ratio) => (
                  <option key={ratio.value} value={ratio.value}>
                    {ratio.label}
                  </option>
                ))}
              </select>
            </label>
          )}

          {contentType !== "image" && (
            <label className="toolbar-control toolbar-control-wide">
              <span>
                Length: {selectedDuration ? `${selectedDuration.duration_seconds}s` : "—"}
                {selectedDuration && (
                  <> (~{formatDuration(selectedDuration.estimated_render_seconds)} to render)</>
                )}
              </span>
              <input
                type="range"
                min={0}
                max={Math.max(durations.length - 1, 0)}
                step={1}
                value={selectedDurationIndex}
                disabled={durations.length < 2}
                onChange={(e) => setDurationId(durations[Number(e.target.value)]?.id ?? null)}
              />
            </label>
          )}
        </div>

        {mode === "i2v" && (
          <fieldset>
            <legend>Reference frames</legend>
            <div className="reference-row">
              <div className="file-slot">
                <label>
                  First frame
                  <input
                    key={firstFrameKey}
                    type="file"
                    accept="image/*"
                    onChange={(e) => setFirstFrame(e.target.files?.[0] ?? null)}
                  />
                </label>
                {firstFrameUrl && (
                  <div className="ref-thumb-row">
                    <img src={firstFrameUrl} className="ref-thumb" alt="First frame preview" />
                    <button type="button" onClick={handleRemoveFirstFrame}>
                      Remove
                    </button>
                  </div>
                )}
              </div>
              <div className="file-slot">
                <label>
                  Last frame (optional)
                  <input
                    key={lastFrameKey}
                    type="file"
                    accept="image/*"
                    onChange={(e) => setLastFrame(e.target.files?.[0] ?? null)}
                  />
                </label>
                {lastFrameUrl && (
                  <div className="ref-thumb-row">
                    <img src={lastFrameUrl} className="ref-thumb" alt="Last frame preview" />
                    <button type="button" onClick={handleRemoveLastFrame}>
                      Remove
                    </button>
                  </div>
                )}
              </div>
            </div>
          </fieldset>
        )}

        {REFERENCE_FLOW_MODES.includes(mode) && (
          <>
            <fieldset>
              <legend>
                Reference images ({refImages.length}/{maxImages})
              </legend>
              <p className="hint">Add images, then insert their token into your prompt.</p>
              {refImages.length > 0 && (
                <ul className="reference-list">
                  {refImages.map((file, i) => (
                    <li key={i} className="reference-item">
                      {refImageUrls[i] && (
                        <img src={refImageUrls[i]} className="ref-thumb-sm" alt="" />
                      )}
                      <span>
                        Picture {i + 1}: {file.name}
                      </span>
                      <button type="button" onClick={() => insertToken(`<Picture ${i + 1}>`)}>
                        Insert token
                      </button>
                      <button type="button" onClick={() => handleRemoveRefImage(i)}>
                        Remove
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {refImages.length < maxImages && (
                <label className="file-slot">
                  Add reference image
                  <input
                    type="file"
                    accept="image/*"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) setRefImages((prev) => [...prev, file]);
                      e.target.value = "";
                    }}
                  />
                </label>
              )}
            </fieldset>

            {maxAudio > 0 && (
              <fieldset>
                <legend>
                  Reference audio ({referenceAudio.length}/{maxAudio})
                </legend>
                <p className="hint">Add audio clips, then insert their token into your prompt.</p>
                {referenceAudio.length > 0 && (
                  <ul className="reference-list">
                    {referenceAudio.map((file, i) => (
                      <li key={i} className="reference-item">
                        <span>
                          Audio {i + 1}: {file.name}
                        </span>
                        <button type="button" onClick={() => insertToken(`<Audio ${i + 1}>`)}>
                          Insert token
                        </button>
                        <button type="button" onClick={() => handleRemoveRefAudio(i)}>
                          Remove
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                {referenceAudio.length < maxAudio && (
                  <label className="file-slot">
                    Add reference audio
                    <input
                      type="file"
                      accept="audio/*"
                      onChange={(e) => {
                        const file = e.target.files?.[0];
                        if (file) setReferenceAudio((prev) => [...prev, file]);
                        e.target.value = "";
                      }}
                    />
                  </label>
                )}
              </fieldset>
            )}
          </>
        )}

        <fieldset className="prompt-fieldset">
          <legend>Prompt</legend>
          <textarea
            className="prompt-input"
            rows={10}
            autoFocus
            value={rawPrompt}
            onChange={(e) => setRawPrompt(e.target.value)}
            placeholder={`Describe the ${CONTENT_TYPE_NOUN[contentType]} you want…`}
          />
          {config.data?.llm_enabled && (
            <div className="prompt-actions">
              <button
                type="button"
                onClick={handleRefine}
                disabled={refinePrompt.isPending || !rawPrompt.trim()}
              >
                {refinePrompt.isPending ? "Refining…" : "AI refine"}
              </button>
              <button type="button" onClick={handleOpenChat} disabled={chatOpen}>
                {chatOpen ? "Chat open" : "Chat with AI"}
              </button>
            </div>
          )}
          {refinePrompt.isError && <p className="error">AI refine failed. Try again.</p>}

          {improvedPrompt && (
            <div className="improved-prompt">
              <p className="hint">
                AI-refined version — this is what will actually be rendered unless you discard it:
              </p>
              <p className="improved-prompt-text">{improvedPrompt}</p>
              <div className="prompt-actions">
                <button type="button" onClick={() => setRawPrompt(improvedPrompt)}>
                  Edit as raw prompt
                </button>
                <button type="button" onClick={() => setImprovedPrompt("")}>
                  Discard
                </button>
              </div>
            </div>
          )}
        </fieldset>

        {queueEstimate.data && (
          <p className="hint">
            This render: ~{formatDuration(queueEstimate.data.additional_seconds)}.{" "}
            {queueEstimate.data.seconds_ahead > 0
              ? `Ahead of you in the queue: ~${formatDuration(queueEstimate.data.seconds_ahead)}.`
              : "Queue is empty."}{" "}
            Estimated done by {new Date(queueEstimate.data.estimated_finish_time).toLocaleTimeString()}.
          </p>
        )}

        {createJob.isError && <p className="error">Couldn't queue that job. Try again.</p>}

        <button type="submit" className="button button-primary" disabled={!canSubmit}>
          {createJob.isPending ? "Queuing…" : `Queue ${CONTENT_TYPE_NOUN[contentType]}`}
        </button>
      </form>

      {chatOpen && (
        <ChatModal
          messages={chatMessages}
          input={chatInput}
          onInputChange={setChatInput}
          onSend={() => void handleSendChat()}
          isPending={chatReply.isPending}
          isError={chatReply.isError}
          onUseAsPrompt={setImprovedPrompt}
          onClose={() => setChatOpen(false)}
        />
      )}
    </section>
  );
}
