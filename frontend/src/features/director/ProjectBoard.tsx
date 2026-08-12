import { useEffect, useState, type KeyboardEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  useAssembleProject,
  useCancelAllRenders,
  useCheckContinuity,
  useCreateClip,
  useDirectorProject,
  useRenderAllDirty,
  useReorderClip,
  useUpdateClip,
  useUpdateDirectorProject,
} from "../../api/directorQueries";
import { useConfig, usePresets, useRefinePrompt } from "../../api/queries";
import { CONTINUATION_CAPABLE_MODES, MODE_LABELS } from "../../api/types";
import type { Clip } from "../../api/directorTypes";
import { ClipBox } from "./ClipBox";
import { ClipEditorPanel } from "./ClipEditorPanel";
import { ProjectResourcesPanel } from "./ProjectResourcesPanel";
import { ScriptPlanModal } from "./ScriptPlanModal";

// Director clips are scoped to the video-content modes only (see this
// feature's own purpose -- sequencing video clips); image/audio modes stay
// exclusive to the main Generate screen.
type NewClipMode = "t2v" | "i2v" | "r2v";

const NEW_CLIP_MODES: { mode: NewClipMode; label: string }[] = [
  { mode: "t2v", label: "+ Text clip" },
  { mode: "i2v", label: "+ Image clip" },
  { mode: "r2v", label: "+ Reference clip" },
];

// Truncated so a large project's context doesn't balloon every refine/chat
// call's token usage -- this is meant to give the LLM a sense of what's
// already happened, not the full text of every earlier clip.
const PREVIOUS_CLIP_PROMPT_MAX_CHARS = 200;

// A short summary of every clip before `beforeOrder`, for AI refine/chat's
// extra_context (see ClipEditorPanel's previousClipsContext prop) -- the
// raw (not AI-refined) prompt, since that's the shorter, more direct
// version of what the user actually asked for.
function buildPreviousClipsContext(clips: Clip[], beforeOrder: number): string {
  const previous = clips.filter((c) => c.order < beforeOrder);
  if (previous.length === 0) return "";
  const lines = previous.map((c) => {
    const prompt = c.prompt.trim();
    const truncated =
      prompt.length > PREVIOUS_CLIP_PROMPT_MAX_CHARS ? `${prompt.slice(0, PREVIOUS_CLIP_PROMPT_MAX_CHARS)}…` : prompt;
    const tag = `${MODE_LABELS[c.mode]}${c.continues_previous ? ", continues previous" : ""}`;
    return `${c.order + 1}. (${tag}) ${truncated || "(empty prompt)"}`;
  });
  return `Previous clips in this project, in order:\n${lines.join("\n")}`;
}

export function ProjectBoard() {
  const { projectId: projectIdParam } = useParams<{ projectId: string }>();
  // Number(undefined) and Number() of a malformed param both come out NaN --
  // still type `number` (never null/undefined), so this can be used
  // directly in function bodies declared later without TypeScript's
  // narrowing-doesn't-cross-function-declaration-boundaries limitation
  // (const projectId: number | null narrowed by an early-return guard
  // isn't seen as narrowed inside a nested `function` declared afterward,
  // only inline closures at the same scope depth) -- see the invalid-id
  // check below instead.
  const projectId = Number(projectIdParam);
  const navigate = useNavigate();
  const config = useConfig();

  const project = useDirectorProject(Number.isNaN(projectId) ? null : projectId);
  const updateProject = useUpdateDirectorProject();
  const createClip = useCreateClip();
  const reorderClip = useReorderClip();
  const renderAllDirty = useRenderAllDirty();
  const cancelAllRenders = useCancelAllRenders();
  const assembleProject = useAssembleProject();
  const checkContinuity = useCheckContinuity();
  const refinePrompt = useRefinePrompt();
  const updateClip = useUpdateClip();

  // Prefetched so "+ Add clip" can create one immediately with a sensible
  // default duration, without a request-then-wait step in between.
  const presetsByMode = {
    t2v: usePresets("t2v"),
    i2v: usePresets("i2v"),
    r2v: usePresets("r2v"),
  };

  const [selectedClipId, setSelectedClipId] = useState<number | null>(null);
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [promptDraft, setPromptDraft] = useState("");
  const [planModalOpen, setPlanModalOpen] = useState(false);
  const [continuityReportOpen, setContinuityReportOpen] = useState(false);
  const [refineAllProgress, setRefineAllProgress] = useState<{ done: number; total: number; failed: number } | null>(
    null,
  );

  // Deliberately narrower than "whenever project.data changes" -- this
  // project is polled every few seconds while a clip is rendering (see
  // useDirectorProject), and project.data is a fresh object reference on
  // every poll tick; depending on the whole object would clobber an
  // in-progress edit mid-typing. Only resync when actually switching
  // projects or when the server-side value itself changes (e.g. after
  // this same save round-trips, or another tab/client edits it).
  useEffect(() => {
    if (project.data) setPromptDraft(project.data.overarching_prompt);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.data?.id, project.data?.overarching_prompt]);

  if (Number.isNaN(projectId)) return <p className="error">Invalid project.</p>;

  async function handleAddClip(mode: NewClipMode) {
    const proj = project.data;
    if (!proj) return;
    const presets = presetsByMode[mode].data;
    // Quality is project-wide (proj.quality_label) -- match this mode's
    // own preset row for that shared label, same fallback the backend
    // uses if the label doesn't have a row for this mode (see
    // director/services.py's resolve_preset_for_mode()).
    const preset = presets?.find((p) => p.label === proj.quality_label) ?? presets?.[0];
    const duration = preset?.durations[0];
    if (!duration) return;
    const lastClip = proj.clips[proj.clips.length - 1];
    const continuesPrevious = !!lastClip && CONTINUATION_CAPABLE_MODES.has(mode);
    const clip = await createClip.mutateAsync({ projectId, mode, durationId: duration.id, continuesPrevious });
    setSelectedClipId(clip.id);
  }

  async function saveTitle() {
    setEditingTitle(false);
    if (!project.data || titleDraft.trim() === project.data.title.trim()) return;
    await updateProject.mutateAsync({ projectId, title: titleDraft.trim() });
  }

  async function savePrompt() {
    if (!project.data || promptDraft === project.data.overarching_prompt) return;
    await updateProject.mutateAsync({ projectId, overarchingPrompt: promptDraft });
  }

  async function handleAspectRatioChange(aspectRatio: string) {
    await updateProject.mutateAsync({ projectId, aspectRatio });
  }

  async function handleQualityChange(qualityLabel: string) {
    await updateProject.mutateAsync({ projectId, qualityLabel });
  }

  function handleTitleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") e.currentTarget.blur();
    if (e.key === "Escape") setEditingTitle(false);
  }

  async function handleCheckContinuity() {
    setContinuityReportOpen(true);
    await checkContinuity.mutateAsync(projectId);
  }

  // Re-runs AI refine for every clip that already has an AI-refined
  // prompt, using each clip's current context (project prompt, resources,
  // its own place in the sequence) -- for when the project prompt or a
  // shared reference changed after a clip was refined, leaving its
  // improved_prompt stale. Only touches clips that opted into AI refine in
  // the first place (a non-blank improved_prompt); a clip that's always
  // just used its raw prompt keeps doing that. Sequential, not parallel --
  // gentler on a self-hosted LLM server, and each clip's own extra_context
  // includes the clips before it, so there's no benefit to racing them.
  async function handleRefineAll() {
    const proj = project.data;
    if (!proj) return;
    const targets = proj.clips.filter((c) => c.improved_prompt.trim());
    if (targets.length === 0) return;
    setRefineAllProgress({ done: 0, total: targets.length, failed: 0 });
    let failed = 0;
    for (const clip of targets) {
      const referenceLabels = [...resourceLabels, ...clip.references.map((r) => r.label)];
      const extraContext = [proj.overarching_prompt, buildPreviousClipsContext(proj.clips, clip.order)]
        .filter(Boolean)
        .join("\n\n");
      try {
        const result = await refinePrompt.mutateAsync({
          mode: clip.mode,
          rawPrompt: clip.prompt,
          referenceLabels,
          extraContext,
          isContinuation: clip.continues_previous,
        });
        await updateClip.mutateAsync({ projectId, clipId: clip.id, improvedPrompt: result.improved_prompt });
      } catch {
        failed += 1;
      }
      setRefineAllProgress((prev) => (prev ? { ...prev, done: prev.done + 1, failed } : prev));
    }
  }

  const dirtyCount = project.data?.clips.filter((c) => c.needs_render).length ?? 0;
  const refinableCount = project.data?.clips.filter((c) => c.improved_prompt.trim()).length ?? 0;
  const activeCount =
    project.data?.clips.filter((c) => c.current_job_status === "queued" || c.current_job_status === "processing")
      .length ?? 0;
  const selectedClip = project.data?.clips.find((c) => c.id === selectedClipId) ?? null;
  const canAssemble = !!project.data?.clips.length && project.data.clips.every((c) => c.video_url && !c.needs_render);
  // Only r2v clips can actually wire a shared resource into a render (see
  // backend director/services.py's project_requires_reference_mode()) --
  // once the project has any, every clip must be one.
  const hasResources = !!project.data?.resources.length;
  const visibleClipModes = hasResources ? NEW_CLIP_MODES.filter((m) => m.mode === "r2v") : NEW_CLIP_MODES;
  const resourceLabels = project.data?.resources.map((r) => r.token_label) ?? [];
  const previousClipsContext = selectedClip && project.data ? buildPreviousClipsContext(project.data.clips, selectedClip.order) : "";

  return (
    <section className="director-board">
      <button type="button" className="link-button director-back-link" onClick={() => navigate("/director")}>
        ← All projects
      </button>

      {project.isLoading && <p className="hint">Loading…</p>}
      {project.isError && <p className="error">Couldn't load this project.</p>}

      {project.data && (
        <>
          {editingTitle ? (
            <input
              type="text"
              className="modal-title-input"
              autoFocus
              value={titleDraft}
              maxLength={200}
              onChange={(e) => setTitleDraft(e.target.value)}
              onBlur={() => void saveTitle()}
              onKeyDown={handleTitleKeyDown}
            />
          ) : (
            <h1
              className="modal-title-editable"
              onClick={() => {
                setTitleDraft(project.data.title);
                setEditingTitle(true);
              }}
              title="Click to rename"
            >
              {project.data.title || `Project ${project.data.id}`}
            </h1>
          )}

          <fieldset className="prompt-fieldset">
            <legend>Overarching prompt</legend>
            <p className="hint">
              Shared world/setting/character context — given to every clip's render, and marks
              every clip dirty when changed.
            </p>
            <textarea
              rows={3}
              value={promptDraft}
              onChange={(e) => setPromptDraft(e.target.value)}
              onBlur={() => void savePrompt()}
              placeholder="e.g. A cyberpunk city at night, neon-lit rain-slicked streets…"
            />
          </fieldset>

          <fieldset className="prompt-fieldset">
            <legend>Aspect ratio &amp; quality</legend>
            <p className="hint">
              Shared by every clip in the project (not chosen per-clip) — MiniMax H3's continuity
              model needs consistent resolution across a chain. Changing either marks every clip
              dirty.
            </p>
            <div className="toolbar">
              <label className="toolbar-control">
                <span>Aspect ratio</span>
                <select value={project.data.aspect_ratio} onChange={(e) => void handleAspectRatioChange(e.target.value)}>
                  {config.data?.aspect_ratios.map((ratio) => (
                    <option key={ratio.value} value={ratio.value}>
                      {ratio.label}
                    </option>
                  ))}
                  {!config.data?.aspect_ratios.some((r) => r.value === project.data.aspect_ratio) && (
                    <option value={project.data.aspect_ratio}>{project.data.aspect_ratio}</option>
                  )}
                </select>
              </label>
              <label className="toolbar-control">
                <span>Quality</span>
                <select value={project.data.quality_label} onChange={(e) => void handleQualityChange(e.target.value)}>
                  {presetsByMode.t2v.data?.map((preset) => (
                    <option key={preset.label} value={preset.label}>
                      {preset.label} ({preset.megapixels}MP{preset.is_draft ? ", draft" : ""})
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </fieldset>

          <ProjectResourcesPanel project={project.data} />

          <div className="director-board-actions">
            <button
              type="button"
              onClick={() => setPlanModalOpen(true)}
              title={
                project.data.script_text.trim()
                  ? "Reopens with the script last used to generate this project's clips"
                  : undefined
              }
            >
              Generate from script…
            </button>
            {config.data?.llm_enabled && (
              <button
                type="button"
                onClick={() => void handleCheckContinuity()}
                disabled={checkContinuity.isPending || project.data.clips.length === 0}
              >
                {checkContinuity.isPending ? "Checking…" : "Check continuity"}
              </button>
            )}
            {config.data?.llm_enabled && refinableCount > 0 && (
              <button
                type="button"
                onClick={() => void handleRefineAll()}
                disabled={!!refineAllProgress && refineAllProgress.done < refineAllProgress.total}
                title="Re-run AI refine for every clip that already has an AI-refined prompt -- useful after changing the overarching prompt or a shared reference."
              >
                {refineAllProgress && refineAllProgress.done < refineAllProgress.total
                  ? `Re-refining… (${refineAllProgress.done}/${refineAllProgress.total})`
                  : `Re-refine all (${refinableCount})`}
              </button>
            )}
            <button
              type="button"
              className="button button-primary"
              onClick={() => renderAllDirty.mutate(projectId)}
              disabled={renderAllDirty.isPending || dirtyCount === 0}
            >
              {renderAllDirty.isPending ? "Starting…" : `Render all dirty (${dirtyCount})`}
            </button>
            {activeCount > 0 && (
              <button
                type="button"
                className="button-danger"
                onClick={() => cancelAllRenders.mutate(projectId)}
                disabled={cancelAllRenders.isPending}
              >
                <span aria-hidden="true">⏹</span>{" "}
                {cancelAllRenders.isPending ? "Cancelling…" : `Cancel all (${activeCount})`}
              </button>
            )}
            <button
              type="button"
              onClick={() => assembleProject.mutate(projectId)}
              disabled={assembleProject.isPending || !canAssemble}
              title={canAssemble ? undefined : "Every clip must be rendered and up to date first."}
            >
              {assembleProject.isPending ? "Assembling…" : "Export"}
            </button>
            {project.data.assembled_video_url && (
              <a href={project.data.assembled_video_url} download className="button">
                <span aria-hidden="true">⬇</span> Download export
              </a>
            )}
          </div>
          {assembleProject.isError && <p className="error">Couldn't assemble the export. Try again.</p>}
          {refineAllProgress && refineAllProgress.done === refineAllProgress.total && (
            <p className={refineAllProgress.failed ? "error" : "hint"}>
              Re-refined {refineAllProgress.total - refineAllProgress.failed}/{refineAllProgress.total} clip
              {refineAllProgress.total === 1 ? "" : "s"}
              {refineAllProgress.failed ? ` — ${refineAllProgress.failed} failed, try again.` : "."}
            </p>
          )}

          <div className="director-timeline">
            {project.data.clips.map((clip, index) => (
              <ClipBox
                key={clip.id}
                clip={clip}
                isFirst={index === 0}
                isLast={index === project.data.clips.length - 1}
                onOpen={() => setSelectedClipId(clip.id)}
                onMoveUp={() => reorderClip.mutate({ projectId, clipId: clip.id, order: clip.order - 1 })}
                onMoveDown={() => reorderClip.mutate({ projectId, clipId: clip.id, order: clip.order + 1 })}
              />
            ))}

            <div className="director-add-clip">
              {visibleClipModes.map(({ mode, label }) => (
                <button type="button" key={mode} onClick={() => void handleAddClip(mode)} disabled={createClip.isPending}>
                  {label}
                </button>
              ))}
            </div>
          </div>
          {hasResources && (
            <p className="hint">
              This project has shared references — every clip must be a reference clip while
              they're attached.
            </p>
          )}
          {project.data.clips.length === 0 && (
            <p className="empty-state">No clips yet — add one above to start the sequence.</p>
          )}
        </>
      )}

      {selectedClip && (
        <ClipEditorPanel
          projectId={projectId}
          clip={selectedClip}
          isFirstClip={selectedClip.order === 0}
          overarchingPrompt={project.data?.overarching_prompt ?? ""}
          previousClipsContext={previousClipsContext}
          projectResourceLabels={resourceLabels}
          onClose={() => setSelectedClipId(null)}
        />
      )}

      {planModalOpen && (
        <ScriptPlanModal
          projectId={projectId}
          hasExistingClips={!!project.data?.clips.length}
          projectResources={project.data?.resources ?? []}
          initialIdeaText={project.data?.script_text ?? ""}
          onClose={() => setPlanModalOpen(false)}
        />
      )}

      {continuityReportOpen && (
        <div className="modal-overlay" onClick={() => setContinuityReportOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="modal-close"
              onClick={() => setContinuityReportOpen(false)}
              aria-label="Close"
            >
              ×
            </button>
            <h2>Continuity check</h2>
            {checkContinuity.isPending && <p className="hint">Reviewing every clip's prompt…</p>}
            {checkContinuity.isError && <p className="error">Couldn't run the continuity check. Try again.</p>}
            {checkContinuity.data && <p className="continuity-report">{checkContinuity.data.report}</p>}
          </div>
        </div>
      )}
    </section>
  );
}
