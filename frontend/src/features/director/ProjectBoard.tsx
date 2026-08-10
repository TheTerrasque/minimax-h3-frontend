import { useEffect, useState, type KeyboardEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  useAssembleProject,
  useCreateClip,
  useDirectorProject,
  useRenderAllDirty,
  useReorderClip,
  useUpdateDirectorProject,
} from "../../api/directorQueries";
import { usePresets } from "../../api/queries";
import { CONTINUATION_CAPABLE_MODES } from "../../api/types";
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

  const project = useDirectorProject(Number.isNaN(projectId) ? null : projectId);
  const updateProject = useUpdateDirectorProject();
  const createClip = useCreateClip();
  const reorderClip = useReorderClip();
  const renderAllDirty = useRenderAllDirty();
  const assembleProject = useAssembleProject();

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
    if (!project.data) return;
    const presets = presetsByMode[mode].data;
    const duration = presets?.[0]?.durations[0];
    if (!duration) return;
    const lastClip = project.data.clips[project.data.clips.length - 1];
    const continuesPrevious = !!lastClip && CONTINUATION_CAPABLE_MODES.has(mode);
    const clip = await createClip.mutateAsync({
      projectId,
      mode,
      durationId: duration.id,
      aspectRatio: continuesPrevious ? undefined : "16:9",
      continuesPrevious,
    });
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

  function handleTitleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") e.currentTarget.blur();
    if (e.key === "Escape") setEditingTitle(false);
  }

  const dirtyCount = project.data?.clips.filter((c) => c.needs_render).length ?? 0;
  const selectedClip = project.data?.clips.find((c) => c.id === selectedClipId) ?? null;
  const canAssemble = !!project.data?.clips.length && project.data.clips.every((c) => c.video_url && !c.needs_render);

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

          <ProjectResourcesPanel project={project.data} />

          <div className="director-board-actions">
            <button type="button" onClick={() => setPlanModalOpen(true)}>
              Generate from script…
            </button>
            <button
              type="button"
              className="button button-primary"
              onClick={() => renderAllDirty.mutate(projectId)}
              disabled={renderAllDirty.isPending || dirtyCount === 0}
            >
              {renderAllDirty.isPending ? "Starting…" : `Render all dirty (${dirtyCount})`}
            </button>
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
              {NEW_CLIP_MODES.map(({ mode, label }) => (
                <button type="button" key={mode} onClick={() => void handleAddClip(mode)} disabled={createClip.isPending}>
                  {label}
                </button>
              ))}
            </div>
          </div>
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
          onClose={() => setSelectedClipId(null)}
        />
      )}

      {planModalOpen && (
        <ScriptPlanModal
          projectId={projectId}
          hasExistingClips={!!project.data?.clips.length}
          onClose={() => setPlanModalOpen(false)}
        />
      )}
    </section>
  );
}
