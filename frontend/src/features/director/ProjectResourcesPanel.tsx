import type { ProjectDetail } from "../../api/directorTypes";
import { useCreateProjectResource, useDeleteProjectResource } from "../../api/directorQueries";
import type { ReferenceKind } from "../../api/types";
import { DropZone } from "../shared/DropZone";

const KIND_ACCEPT: Record<ReferenceKind, string> = {
  image: "image/*",
  audio: "audio/*",
  video: "video/*",
};
const KIND_LABEL: Record<ReferenceKind, string> = {
  image: "Character sheet / reference image",
  audio: "Voice reference",
  video: "Reference video",
};

interface ProjectResourcesPanelProps {
  project: ProjectDetail;
}

// Shared world/character/voice references every Clip's render draws on
// (see backend director/models.py's ProjectResource) -- distinct from a
// Clip's own reference images, which only that one clip's render sees.
export function ProjectResourcesPanel({ project }: ProjectResourcesPanelProps) {
  const createResource = useCreateProjectResource();
  const deleteResource = useDeleteProjectResource();

  return (
    <fieldset className="director-resources-panel">
      <legend>Shared resources</legend>
      <p className="hint">
        Character sheets, voice references, and world/style images or clips every clip's render
        can draw on — insert their token (e.g. <code>&lt;Picture 1&gt;</code>) into a clip's prompt.
      </p>
      {project.resources.length > 0 && (
        <ul className="reference-list director-resource-list">
          {project.resources.map((resource) => (
            <li key={resource.id} className="reference-item">
              {resource.kind === "image" && resource.url && (
                <img src={resource.url} className="ref-thumb-sm" alt="" />
              )}
              <span>{resource.label}</span>
              <button
                type="button"
                onClick={() => deleteResource.mutate({ projectId: project.id, resourceId: resource.id })}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="director-resource-add-row">
        {(["image", "audio", "video"] as ReferenceKind[]).map((kind) => (
          <DropZone
            key={kind}
            accept={KIND_ACCEPT[kind]}
            className="file-slot"
            onFiles={(files) => createResource.mutate({ projectId: project.id, kind, file: files[0] })}
          >
            + {KIND_LABEL[kind]}
            <input
              type="file"
              accept={KIND_ACCEPT[kind]}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) createResource.mutate({ projectId: project.id, kind, file });
                e.target.value = "";
              }}
            />
          </DropZone>
        ))}
      </div>
      {createResource.isError && <p className="error">Couldn't add that resource. Try again.</p>}
    </fieldset>
  );
}
