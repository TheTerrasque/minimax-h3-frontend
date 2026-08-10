import { useState } from "react";
import { useApplyPlan, usePlanFromScript } from "../../api/directorQueries";
import type { PlannedScene } from "../../api/directorTypes";
import { MODE_LABELS } from "../../api/types";

interface ScriptPlanModalProps {
  projectId: number;
  hasExistingClips: boolean;
  onClose: () => void;
}

// "Generate from script" -- paste a script/idea, let the LLM propose an
// ordered scene sequence, review/edit it, then apply it as real clips (see
// backend director/api.py's plan_project/apply_plan). Two-step UI on
// purpose (propose, then a separate confirm) rather than creating clips
// straight from the LLM's reply -- an unreviewed AI-generated sequence is
// exactly the kind of thing a user should get to look at first.
export function ScriptPlanModal({ projectId, hasExistingClips, onClose }: ScriptPlanModalProps) {
  const planFromScript = usePlanFromScript();
  const applyPlan = useApplyPlan();

  const [ideaText, setIdeaText] = useState("");
  const [scenes, setScenes] = useState<PlannedScene[] | null>(null);
  const [replace, setReplace] = useState(false);

  async function handleGenerate() {
    if (!ideaText.trim()) return;
    const result = await planFromScript.mutateAsync({ projectId, ideaText });
    setScenes(result.scenes);
  }

  async function handleApply() {
    if (!scenes || scenes.length === 0) return;
    await applyPlan.mutateAsync({ projectId, scenes, replace });
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
