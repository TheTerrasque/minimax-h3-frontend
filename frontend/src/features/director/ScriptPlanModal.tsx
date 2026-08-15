import { useEffect, useRef, useState } from "react";
import {
  useApplyPlan,
  useConvertToReference,
  useCreateProjectResource,
  useExtractReferences,
  usePlanFromScript,
} from "../../api/directorQueries";
import { useCreateJob, useJob, usePresets } from "../../api/queries";
import type { PlannedScene, ProjectResource, ReferenceCandidate } from "../../api/directorTypes";
import { MODE_LABELS } from "../../api/types";
import type { RenderPreset } from "../../api/types";

interface ScriptPlanModalProps {
  projectId: number;
  hasExistingClips: boolean;
  // Shown so the user knows what's already available to mention by name in
  // their idea text -- the LLM gets these (with their token + label)
  // automatically either way (see backend director/api.py's plan_project()),
  // this is just so the user isn't guessing what "Picture 1" refers to.
  // Non-empty here implies every generated scene will be forced to a
  // reference clip -- see project_requires_reference_mode().
  projectResources: ProjectResource[];
  // Whether ProjectResourcesPanel would actually accept a new resource
  // right now (false once any non-r2v clip exists) -- gates the reference
  // suggestion/upload options below so neither points at a dead end.
  canAddResources: boolean;
  // Used as the aspect ratio for any image reference generated from a
  // suggestion below -- audio references ignore this (see
  // ReferenceCandidateRow, forced to 1:1 server-side regardless).
  projectAspectRatio: string;
  // Pre-fills the textarea with the project's last-saved script, if any
  // (see backend Project.script_text) -- lets "Generate from script" be
  // reopened to review/regenerate from what was used before instead of
  // starting from a blank box.
  initialIdeaText: string;
  onClose: () => void;
}

// The first non-draft active preset's first active duration -- used as a
// sensible default for a reference-image/voice job the user didn't get a
// quality/duration picker for (see ReferenceCandidateRow). Falls back to
// the first preset at all if every one is a draft tier.
function pickDefaultDurationId(presets: RenderPreset[] | undefined): number | null {
  if (!presets || presets.length === 0) return null;
  const preset = presets.find((p) => !p.is_draft) ?? presets[0];
  return preset.durations[0]?.id ?? null;
}

interface ReferenceCandidateRowProps {
  projectId: number;
  candidate: ReferenceCandidate;
  aspectRatio: string;
  imageDurationId: number | null;
  audioDurationId: number | null;
  // False once the project has a non-reference clip -- generating the
  // asset itself still works, but attaching it as a ProjectResource would
  // 400 (see backend director/api.py's project_resources()), so this
  // disables the action up front instead of letting the user wait through
  // a whole render only to hit that error at the very end.
  canAddResources: boolean;
  // Bumped by the parent's "Generate all" button -- any row still idle
  // starts generating when this changes (a ref-tracked previous value, not
  // a dependency-array trick, so a row that's already done/in-progress
  // doesn't restart just because another row's click bumped the counter).
  generateAllSignal: number;
}

// One suggested character/object/voice reference, with its own "Generate"
// button -- creates a t2i/t2a GenerationJob from the suggested prompt,
// polls it to completion, then attaches the result as a ProjectResource
// labeled with the candidate's name. Each row owns its own job/upload
// state so several can run independently (the shared render queue still
// serializes the actual jobs, same as anywhere else in the app).
function ReferenceCandidateRow({
  projectId,
  candidate,
  aspectRatio,
  imageDurationId,
  audioDurationId,
  canAddResources,
  generateAllSignal,
}: ReferenceCandidateRowProps) {
  const createJob = useCreateJob();
  const createResource = useCreateProjectResource();
  const [jobId, setJobId] = useState<number | null>(null);
  const [stage, setStage] = useState<"idle" | "rendering" | "uploading" | "done" | "error">("idle");
  const job = useJob(jobId);
  // Guards the upload effect below from firing twice for the same
  // finished job (e.g. a poll landing while the async upload is already
  // in flight) -- a ref rather than folding into `stage` so the effect's
  // own dependency on `stage` doesn't retrigger itself.
  const uploadStarted = useRef(false);
  const seenGenerateAllSignal = useRef(generateAllSignal);

  useEffect(() => {
    if (!job.data || stage !== "rendering" || uploadStarted.current) return;
    if (job.data.status === "cancelled") {
      setStage("error");
      return;
    }
    if (job.data.status !== "done") return;
    if (job.data.error_message || !job.data.video_url) {
      setStage("error");
      return;
    }
    uploadStarted.current = true;
    setStage("uploading");
    const videoUrl = job.data.video_url;
    void (async () => {
      try {
        const response = await fetch(videoUrl);
        const blob = await response.blob();
        // Matches integrations/media_post.py's actual output extension per
        // content type (t2i -> .png, t2a -> .mp3) so the backend's upload
        // path derives a sane extension -- see generation/models.py's
        // _random_upload_path().
        const filename = candidate.kind === "image" ? "reference.png" : "reference.mp3";
        const file = new File([blob], filename, { type: blob.type });
        await createResource.mutateAsync({ projectId, kind: candidate.kind, file, label: candidate.name });
        setStage("done");
      } catch {
        setStage("error");
      }
    })();
  }, [job.data, stage, candidate, projectId, createResource]);

  useEffect(() => {
    if (generateAllSignal === seenGenerateAllSignal.current) return;
    seenGenerateAllSignal.current = generateAllSignal;
    if (canAddResources && (stage === "idle" || stage === "error")) void handleGenerate();
    // handleGenerate is stable enough for this purpose (recreated each
    // render, but only ever invoked here in reaction to the signal
    // actually changing, not on every re-render) -- omitted from deps to
    // avoid re-running this effect on renders that don't change the signal.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [generateAllSignal]);

  async function handleGenerate() {
    const durationId = candidate.kind === "image" ? imageDurationId : audioDurationId;
    if (!durationId) return;
    uploadStarted.current = false;
    setStage("rendering");
    try {
      const created = await createJob.mutateAsync({
        mode: candidate.kind === "image" ? "t2i" : "t2a",
        durationId,
        aspectRatio,
        rawPrompt: candidate.description,
      });
      setJobId(created.id);
    } catch {
      setStage("error");
    }
  }

  const busy = stage === "rendering" || stage === "uploading";

  return (
    <li className="plan-resource-candidate">
      <div className="plan-resource-candidate-body">
        <strong>{candidate.name}</strong> <span className="hint">({candidate.kind})</span>
        <p className="hint">{candidate.description}</p>
      </div>
      <div className="plan-resource-candidate-action">
        {stage === "done" ? (
          <span className="hint">Added ✓</span>
        ) : (
          <button
            type="button"
            onClick={() => void handleGenerate()}
            disabled={busy || !canAddResources}
            title={canAddResources ? undefined : "Delete or convert this project's existing clips first."}
          >
            {stage === "rendering"
              ? "Generating…"
              : stage === "uploading"
                ? "Saving…"
                : stage === "error"
                  ? "Retry"
                  : "Generate"}
          </button>
        )}
        {stage === "error" && <p className="error">Failed.</p>}
      </div>
    </li>
  );
}

// "Generate from script" -- paste a script/idea, let the LLM propose an
// ordered scene sequence, review/edit it, then apply it as real clips (see
// backend director/api.py's plan_project/apply_plan). Two-step UI on
// purpose (propose, then a separate confirm) rather than creating clips
// straight from the LLM's reply -- an unreviewed AI-generated sequence is
// exactly the kind of thing a user should get to look at first.
export function ScriptPlanModal({
  projectId,
  hasExistingClips,
  projectResources,
  canAddResources,
  projectAspectRatio,
  initialIdeaText,
  onClose,
}: ScriptPlanModalProps) {
  const planFromScript = usePlanFromScript();
  const applyPlan = useApplyPlan();
  const extractReferences = useExtractReferences();
  const convertToReference = useConvertToReference();
  const imagePresets = usePresets("t2i");
  const audioPresets = usePresets("t2a");

  const [ideaText, setIdeaText] = useState(initialIdeaText);
  const [scenes, setScenes] = useState<PlannedScene[] | null>(null);
  const [replace, setReplace] = useState(false);
  const [candidates, setCandidates] = useState<ReferenceCandidate[] | null>(null);
  const [generateAllSignal, setGenerateAllSignal] = useState(0);

  const imageDurationId = pickDefaultDurationId(imagePresets.data);
  const audioDurationId = pickDefaultDurationId(audioPresets.data);

  async function handleGenerate() {
    if (!ideaText.trim()) return;
    const result = await planFromScript.mutateAsync({ projectId, ideaText });
    setScenes(result.scenes);
  }

  async function handleExtractReferences() {
    if (!ideaText.trim()) return;
    const result = await extractReferences.mutateAsync({ projectId, ideaText });
    setCandidates(result.candidates);
  }

  async function handleApply() {
    if (!scenes || scenes.length === 0) return;
    await applyPlan.mutateAsync({ projectId, scenes, replace, ideaText });
    onClose();
  }

  function updateScene(index: number, patch: Partial<PlannedScene>) {
    setScenes((prev) => (prev ? prev.map((s, i) => (i === index ? { ...s, ...patch } : s)) : prev));
  }

  function removeScene(index: number) {
    setScenes((prev) => (prev ? prev.filter((_, i) => i !== index) : prev));
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal modal-wide" onClick={(e) => e.stopPropagation()}>
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
          ×
        </button>
        <h2>Generate from script</h2>

        {!scenes ? (
          <>
            <p className="hint">
              Paste a script or a loose idea — the AI will break it into an ordered sequence of
              clips you can review and edit before anything is created.
            </p>
            {projectResources.length > 0 && (
              <div className="plan-resource-hint">
                <p className="hint">
                  This project has shared references, so every generated scene will be a reference
                  clip that can draw on them where relevant:
                </p>
                <ul className="plan-resource-list">
                  {projectResources.map((resource) => (
                    <li key={resource.id}>
                      <code>&lt;{resource.token_label}&gt;</code>
                      {resource.label !== resource.token_label && ` — ${resource.label}`}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {/* Always rendered regardless of projectResources.length -- a
                candidate generated here adds a resource and would otherwise
                flip that condition mid-review, hiding this whole section
                (and every still-ungenerated candidate) out from under the
                user right after their first successful click. */}
            <div className="plan-resource-hint">
              <p className="hint">
                {projectResources.length > 0
                  ? "Want references for more recurring characters/objects/voices? The AI can scan your script and suggest more below."
                  : "Without shared references, each generated clip is written from text alone, so a character's face, hair, and clothes -- and a voice's timbre -- can still drift across hard cuts even with a carefully written prompt. The AI can scan your script for recurring characters/objects/voices and generate a reference for each below, or you can add your own."}
              </p>
              {!canAddResources && (
                <div className="plan-reference-suggest">
                  <p className="hint">
                    This project's existing clips block actually attaching a new reference right
                    now -- convert them to reference mode first (keeps every clip and its own
                    references intact). You can still preview suggestions below in the meantime.
                  </p>
                  <button
                    type="button"
                    onClick={() => convertToReference.mutate(projectId)}
                    disabled={convertToReference.isPending}
                  >
                    {convertToReference.isPending ? "Converting…" : "Convert existing clips to reference mode"}
                  </button>
                  {convertToReference.isError && <p className="error">Couldn't convert. Try again.</p>}
                </div>
              )}
              <div className="plan-reference-suggest">
                <div className="modal-actions">
                  <button
                    type="button"
                    onClick={() => void handleExtractReferences()}
                    disabled={extractReferences.isPending || !ideaText.trim()}
                  >
                    {extractReferences.isPending ? "Analyzing…" : "Suggest references from this script"}
                  </button>
                  {candidates && candidates.length > 0 && (
                    <button
                      type="button"
                      onClick={() => setGenerateAllSignal((n) => n + 1)}
                      disabled={!canAddResources}
                      title={canAddResources ? undefined : "Delete or convert this project's existing clips first."}
                    >
                      Generate all
                    </button>
                  )}
                  {projectResources.length === 0 && canAddResources && (
                    <button type="button" className="link-button" onClick={onClose}>
                      Add references manually
                    </button>
                  )}
                </div>
                {extractReferences.isError && (
                  <p className="error">Couldn't analyze the script for references. Try again.</p>
                )}
                {candidates && candidates.length === 0 && (
                  <p className="hint">No recurring subjects worth a fixed reference were found.</p>
                )}
                {candidates && candidates.length > 0 && (
                  <ul className="plan-resource-candidate-list">
                    {candidates.map((candidate, index) => (
                      <ReferenceCandidateRow
                        key={index}
                        projectId={projectId}
                        candidate={candidate}
                        aspectRatio={projectAspectRatio}
                        imageDurationId={imageDurationId}
                        audioDurationId={audioDurationId}
                        canAddResources={canAddResources}
                        generateAllSignal={generateAllSignal}
                      />
                    ))}
                  </ul>
                )}
              </div>
            </div>
            <textarea
              rows={10}
              value={ideaText}
              onChange={(e) => setIdeaText(e.target.value)}
              placeholder="e.g. A lighthouse keeper spots a strange light out at sea..."
            />
            {planFromScript.isError && <p className="error">Couldn't generate a plan. Try again.</p>}
            <div className="modal-actions">
              <button
                type="button"
                className="button button-primary"
                onClick={() => void handleGenerate()}
                disabled={planFromScript.isPending || !ideaText.trim()}
              >
                {planFromScript.isPending ? "Generating…" : "Generate"}
              </button>
            </div>
          </>
        ) : (
          <>
            <p className="hint">
              Review the proposed scenes below — edit any prompt, remove ones you don't want, then
              apply. Nothing is created until you click Apply.
            </p>
            <ul className="plan-scene-list">
              {scenes.map((scene, index) => (
                <li key={index} className="plan-scene-card">
                  <div className="plan-scene-card-header">
                    <span className="plan-scene-number">Scene {index + 1}</span>
                    <span className="hint">{MODE_LABELS[scene.mode]}</span>
                    {scene.continues_previous && <span className="hint">continues previous</span>}
                    <label
                      className="plan-scene-duration"
                      title={
                        scene.continues_previous
                          ? "Locked to match the chained run's first scene -- edit that scene's duration instead."
                          : "Requested clip length in seconds; matched to the nearest available option."
                      }
                    >
                      <span className="hint">sec</span>
                      <input
                        type="number"
                        min={1}
                        step={1}
                        value={scene.duration_seconds ?? ""}
                        disabled={scene.continues_previous}
                        onChange={(e) =>
                          updateScene(index, { duration_seconds: e.target.value ? Number(e.target.value) : null })
                        }
                      />
                    </label>
                    <button type="button" className="link-button" onClick={() => removeScene(index)}>
                      Remove
                    </button>
                  </div>
                  {scene.notes && <p className="hint plan-scene-notes">{scene.notes}</p>}
                  <textarea
                    rows={4}
                    value={scene.prompt}
                    onChange={(e) => updateScene(index, { prompt: e.target.value })}
                  />
                </li>
              ))}
              {scenes.length === 0 && <p className="empty-state">No scenes left — remove the modal or generate again.</p>}
            </ul>

            {hasExistingClips && (
              <label className="clip-editor-continues-toggle">
                <input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} />
                Replace this project's existing clips instead of appending after them
              </label>
            )}

            {applyPlan.isError && <p className="error">Couldn't apply that plan. Try again.</p>}
            <div className="modal-actions">
              <button type="button" onClick={() => setScenes(null)} disabled={applyPlan.isPending}>
                Back
              </button>
              <button
                type="button"
                className="button button-primary"
                onClick={() => void handleApply()}
                disabled={applyPlan.isPending || scenes.length === 0}
              >
                {applyPlan.isPending ? "Applying…" : `Apply ${scenes.length} scene${scenes.length === 1 ? "" : "s"}`}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
