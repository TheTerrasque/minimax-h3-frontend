import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useCreateDirectorProject, useDeleteDirectorProject, useDirectorProjects } from "../../api/directorQueries";
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
  if (seconds < 60) return `~${Math.round(seconds)}s left`;
  const minutes = Math.round(seconds / 60);
  return `~${minutes}m left`;
}

function progressLabel(project: Project, done: number, total: number, active: number): string {
  const parts = [`${done}/${total} rendered`];
  if (active > 0) parts.push("rendering…");
  else if (project.eta_seconds) parts.push(formatEta(project.eta_seconds));
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
  const deleteProject = useDeleteDirectorProject();
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const total = project.clip_count ?? 0;
  const dirty = project.dirty_count ?? 0;
  const active = project.active_count ?? 0;
  const done = total - dirty;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;

  return (
    <li className="director-project-card">
      <button type="button" className="director-project-card-open" onClick={onOpen}>
        <span className="director-project-card-title">{project.title || `Project ${project.id}`}</span>
        <span className="director-project-card-meta">
          Updated {relativeTime(project.updated_at)}
          {total > 0 && <> · {progressLabel(project, done, total, active)}</>}
        </span>
        {total > 0 && (
          <div className="job-progress-track director-project-progress-track">
            <div className="job-progress-fill" style={{ width: `${pct}%` }} />
          </div>
        )}
      </button>

      <div className="director-project-card-actions">
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
