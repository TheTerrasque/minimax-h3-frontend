import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useCreateDirectorProject, useDirectorProjects } from "../../api/directorQueries";

function relativeTime(iso: string): string {
  const diffMin = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  return new Date(iso).toLocaleDateString();
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
          <li key={project.id}>
            <button
              type="button"
              className="director-project-card"
              onClick={() => navigate(`/director/${project.id}`)}
            >
              <span className="director-project-card-title">{project.title || `Project ${project.id}`}</span>
              <span className="director-project-card-meta">Updated {relativeTime(project.updated_at)}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
