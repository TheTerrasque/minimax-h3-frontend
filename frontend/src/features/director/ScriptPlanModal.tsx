import { useState } from "react";
import { useApplyPlan, usePlanFromScript } from "../../api/directorQueries";
import type { PlannedScene, ProjectResource } from "../../api/directorTypes";
import { MODE_LABELS } from "../../api/types";

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
  // Pre-fills the textarea with the project's last-saved script, if any
  // (see backend Project.script_text) -- lets "Generate from script" be
  // reopened to review/regenerate from what was used before instead of
  // starting from a blank box.
  initialIdeaText: string;
  onClose: () => void;
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
  initialIdeaText,
  onClose,
}: ScriptPlanModalProps) {
  const planFromScript = usePlanFromScript();
  const applyPlan = useApplyPlan();

  const [ideaText, setIdeaText] = useState(initialIdeaText);
  const [scenes, setScenes] = useState<PlannedScene[] | null>(null);
  const [replace, setReplace] = useState(false);

  async function handleGenerate() {
    if (!ideaText.trim()) return;
    const result = await planFromScript.mutateAsync({ projectId, ideaText });
    setScenes(result.scenes);
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
