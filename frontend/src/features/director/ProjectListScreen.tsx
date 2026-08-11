import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import {
  useCreateDirectorProject,
  useDeleteDirectorProject,
  useDirectorProjects,
  useUpdateDirectorProject,
} from "../../api/directorQueries";
import type { Project } from "../../api/directorTypes";

function relativeTime(iso: string): string {
  const diffMin = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  return new Date(iso).toLocaleDateString();
}

function formatEta(seconds: number): string {
  if (seconds < 60) return `~${Math.round(seconds)}s`;
  const minutes = Math.round(seconds / 60);
  return `~${minutes}m`;
}

function progressLabel(project: Project): string | null {
  const total = project.clip_count ?? 0;
  if (total === 0) return null;
  const dirty = project.dirty_count ?? 0;
  const done = total - dirty;
  const active = project.active_count ?? 0;
  const parts = [`${done}/${total} rendered`];
  if (active > 0) parts.push("rendering…");
  else if (dirty > 0 && project.eta_seconds) parts.push(`${formatEta(project.eta_seconds)} to finish`);
  return parts.join(" · ");
}

export function ProjectListScreen() {
  const navigate = useNavigate();
  const projects = useDirectorProjects();
  const createProject = useCreateDirectorProject();
  const [newTitle, setNewTitle] = useState("");

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    const project = await createProject.mutateAsync({ title: newTitle.trim() || undefined });
    navigate(`/director/${project.id}`);
  }

  return (
    <section className="screen director-list-screen">
      <h1>Director Mode</h1>
      <p className="hint">
        Sequence multiple clips into one long video, with continuity between scenes flagged as
        continuing the one before them.
      </p>

      <form className="director-new-project-form" onSubmit={(e) => void handleCreate(e)}>
        <input
          type="text"
          placeholder="New project title…"
          value={newTitle}
          onChange={(e) => setNewTitle(e.target.value)}
        />
        <button type="submit" className="button button-primary" disabled={createProject.isPending}>
          {createProject.isPending ? "Creating…" : "New project"}
        </button>
      </form>
      {createProject.isError && <p className="error">Couldn't create that project. Try again.</p>}

      {projects.isLoading && <p className="hint">Loading…</p>}
      {projects.isError && <p className="error">Couldn't load your projects.</p>}
      {projects.data?.length === 0 && (
        <p className="empty-state">No projects yet — create one above to start sequencing clips.</p>
      )}

      <ul className="director-project-list">
        {projects.data?.map((project) => (
          <ProjectCard key={project.id} project={project} onOpen={() => navigate(`/director/${project.id}`)} />
        ))}
      </ul>
    </section>
  );
}

function ProjectCard({ project, onOpen }: { project: Project; onOpen: () => void }) {
  const updateProject = useUpdateDirectorProject();
  const deleteProject = useDeleteDirectorProject();
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState(project.title);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  async function saveTitle() {
    setEditingTitle(false);
    const trimmed = titleDraft.trim();
    if (trimmed && trimmed !== project.title) {
      await updateProject.mutateAsync({ projectId: project.id, title: trimmed });
    } else {
      setTitleDraft(project.title);
    }
  }

  const progress = progressLabel(project);

  return (
    <li className="director-project-card">
      {editingTitle ? (
        <input
          type="text"
          className="director-project-card-title-input"
          autoFocus
          value={titleDraft}
          maxLength={200}
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => setTitleDraft(e.target.value)}
          onBlur={() => void saveTitle()}
          onKeyDown={(e) => {
            if (e.key === "Enter") e.currentTarget.blur();
            if (e.key === "Escape") {
              setTitleDraft(project.title);
              setEditingTitle(false);
            }
          }}
        />
      ) : (
        <button type="button" className="director-project-card-open" onClick={onOpen}>
          <span className="director-project-card-title">{project.title || `Project ${project.id}`}</span>
          <span className="director-project-card-meta">
            Updated {relativeTime(project.updated_at)}
            {progress && <> · {progress}</>}
          </span>
        </button>
      )}

      <div className="director-project-card-actions">
        {!editingTitle && (
          <button
            type="button"
            className="link-button"
            onClick={(e) => {
              e.stopPropagation();
              setTitleDraft(project.title);
              setEditingTitle(true);
            }}
          >
            Rename
          </button>
        )}
        {confirmingDelete ? (
          <>
            <span className="hint">Delete this project? This can't be undone.</span>
            <button
              type="button"
              className="button-danger"
              onClick={(e) => {
                e.stopPropagation();
                deleteProject.mutate(project.id);
              }}
              disabled={deleteProject.isPending}
            >
              {deleteProject.isPending ? "Deleting…" : "Yes, delete"}
            </button>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setConfirmingDelete(false);
              }}
            >
              Cancel
            </button>
          </>
        ) : (
          <button
            type="button"
            className="link-button"
            onClick={(e) => {
              e.stopPropagation();
              setConfirmingDelete(true);
            }}
          >
            Delete
          </button>
        )}
      </div>
      {deleteProject.isError && <p className="error">Couldn't delete that project. Try again.</p>}
    </li>
  );
}
