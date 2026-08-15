// TanStack Query hooks for Director Mode -- same one-hook-per-endpoint
// convention as api/queries.ts, kept in its own file since this is a
// large, mostly-separate feature area (see backend director/api.py).

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "./client";
import type {
  Clip,
  ClipReference,
  JobMembership,
  PlannedScene,
  Project,
  ProjectDetail,
  ProjectResource,
  ReferenceCandidate,
} from "./directorTypes";
import type { Mode, ReferenceKind } from "./types";

const ACTIVE_JOB_STATUSES = new Set(["queued", "processing"]);

function projectHasActiveClip(project: ProjectDetail | undefined): boolean {
  return !!project?.clips.some((c) => c.current_job_status && ACTIVE_JOB_STATUSES.has(c.current_job_status));
}

export function useDirectorProjects() {
  return useQuery({
    queryKey: ["director-projects"],
    queryFn: () => apiFetch<Project[]>("/director/projects/"),
    // Polls while any project has a render in flight, so the list
    // screen's progress/ETA summary (see ProjectListScreen) stays live --
    // same shape as useDirectorProject's per-project polling.
    refetchInterval: (query) => (query.state.data?.some((p) => (p.active_count ?? 0) > 0) ? 4000 : false),
  });
}

export function useDirectorProject(projectId: number | null) {
  return useQuery({
    queryKey: ["director-project", projectId],
    queryFn: () => apiFetch<ProjectDetail>(`/director/projects/${projectId}/`),
    enabled: projectId != null,
    // Polls while any clip is actively rendering -- same shape as
    // api/queries.ts's useJobs()/useJob(), just keyed off the whole
    // project's clips instead of a single job.
    refetchInterval: (query) => (projectHasActiveClip(query.state.data) ? 4000 : false),
  });
}

// Which Director project each of the user's jobs already belongs to, if
// any -- see backend director/api.py's job_memberships(). Used by the main
// Generate page's job modal to show "part of <project>" vs. offering
// "Create Director project" -- see useCreateProjectFromJob() below.
export function useJobMemberships() {
  return useQuery({
    queryKey: ["director-job-memberships"],
    queryFn: () => apiFetch<JobMembership[]>("/director/job_memberships/"),
  });
}

// Wraps an already-rendered, standalone job (from the main Generate page)
// as a new Director project with that job as its first, already-clean
// clip -- see backend director/services.py's create_project_from_job().
// Reuses the existing render rather than queuing a new one.
export function useCreateProjectFromJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (jobId: number) => apiFetch<ProjectDetail>(`/director/from_job/${jobId}/`, { method: "POST" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["director-projects"] });
      void queryClient.invalidateQueries({ queryKey: ["director-job-memberships"] });
    },
  });
}

export function useCreateDirectorProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { title?: string; overarchingPrompt?: string; aspectRatio?: string; qualityLabel?: string }) =>
      apiFetch<ProjectDetail>("/director/projects/", {
        method: "POST",
        body: JSON.stringify({
          title: input.title,
          overarching_prompt: input.overarchingPrompt,
          aspect_ratio: input.aspectRatio,
          quality_label: input.qualityLabel,
        }),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["director-projects"] });
    },
  });
}

export function useUpdateDirectorProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      projectId,
      title,
      overarchingPrompt,
      aspectRatio,
      qualityLabel,
      scriptText,
    }: {
      projectId: number;
      title?: string;
      overarchingPrompt?: string;
      // Project-wide -- changing either recomputes every clip's
      // preset/width/height and marks the whole project dirty.
      aspectRatio?: string;
      qualityLabel?: string;
      // Purely informational -- doesn't affect any clip.
      scriptText?: string;
    }) =>
      apiFetch<ProjectDetail>(`/director/projects/${projectId}/`, {
        method: "PATCH",
        body: JSON.stringify({
          title,
          overarching_prompt: overarchingPrompt,
          aspect_ratio: aspectRatio,
          quality_label: qualityLabel,
          script_text: scriptText,
        }),
      }),
    onSuccess: (_data, { projectId }) => {
      void queryClient.invalidateQueries({ queryKey: ["director-project", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["director-projects"] });
    },
  });
}

export function useDeleteDirectorProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, deleteRelatedJobs }: { projectId: number; deleteRelatedJobs?: boolean }) =>
      apiFetch<void>(`/director/projects/${projectId}/`, {
        method: "DELETE",
        body: JSON.stringify({ delete_related_jobs: !!deleteRelatedJobs }),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["director-projects"] });
      // A "delete related jobs" deletion removes GenerationJob rows too --
      // the main Generate page's own lists need to drop them as well.
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      void queryClient.invalidateQueries({ queryKey: ["director-job-memberships"] });
    },
  });
}

export function useCreateProjectResource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      projectId,
      kind,
      file,
      label,
    }: {
      projectId: number;
      kind: ReferenceKind;
      file: File;
      label?: string;
    }) => {
      const form = new FormData();
      form.set("kind", kind);
      form.set("file", file);
      if (label) form.set("label", label);
      return apiFetch<ProjectResource>(`/director/projects/${projectId}/resources/`, {
        method: "POST",
        body: form,
      });
    },
    onSuccess: (_data, { projectId }) => {
      void queryClient.invalidateQueries({ queryKey: ["director-project", projectId] });
    },
  });
}

// Switches every non-reference clip in a project to r2v, in place -- see
// backend director/services.py's convert_clips_to_reference(). Lets a
// project that already has t2v/i2v clips start using shared references
// without deleting them first (useCreateProjectResource otherwise 400s
// while any non-reference clip exists).
export function useConvertToReference() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (projectId: number) =>
      apiFetch<Clip[]>(`/director/projects/${projectId}/convert_to_reference/`, { method: "POST" }),
    onSuccess: (_data, projectId) => {
      void queryClient.invalidateQueries({ queryKey: ["director-project", projectId] });
    },
  });
}

export function useDeleteProjectResource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ resourceId }: { projectId: number; resourceId: number }) =>
      apiFetch<void>(`/director/resources/${resourceId}/`, { method: "DELETE" }),
    onSuccess: (_data, { projectId }) => {
      void queryClient.invalidateQueries({ queryKey: ["director-project", projectId] });
    },
  });
}

export interface CreateClipInput {
  projectId: number;
  mode: Mode;
  durationId: number;
  continuesPrevious?: boolean;
  prompt?: string;
  referenceImages?: File[];
  referenceAudio?: File[];
  referenceVideo?: File[];
}

export function useCreateClip() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateClipInput) => {
      const form = new FormData();
      form.set("mode", input.mode);
      form.set("duration_id", String(input.durationId));
      if (input.continuesPrevious) form.set("continues_previous", "true");
      if (input.prompt) form.set("prompt", input.prompt);
      for (const f of input.referenceImages ?? []) form.append("reference_images", f);
      for (const f of input.referenceAudio ?? []) form.append("reference_audio", f);
      for (const f of input.referenceVideo ?? []) form.append("reference_video", f);
      return apiFetch<Clip>(`/director/projects/${input.projectId}/clips/`, { method: "POST", body: form });
    },
    onSuccess: (_data, { projectId }) => {
      void queryClient.invalidateQueries({ queryKey: ["director-project", projectId] });
    },
  });
}

export interface UpdateClipInput {
  projectId: number;
  clipId: number;
  prompt?: string;
  improvedPrompt?: string;
  continuesPrevious?: boolean;
  durationId?: number;
}

export function useUpdateClip() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: UpdateClipInput) => {
      const body: Record<string, unknown> = {};
      if (input.prompt !== undefined) body.prompt = input.prompt;
      if (input.improvedPrompt !== undefined) body.improved_prompt = input.improvedPrompt;
      if (input.continuesPrevious !== undefined) body.continues_previous = input.continuesPrevious;
      if (input.durationId !== undefined) body.duration_id = input.durationId;
      return apiFetch<Clip>(`/director/clips/${input.clipId}/`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
    },
    onSuccess: (_data, { projectId }) => {
      void queryClient.invalidateQueries({ queryKey: ["director-project", projectId] });
    },
  });
}

export function useDeleteClip() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ clipId }: { projectId: number; clipId: number }) =>
      apiFetch<void>(`/director/clips/${clipId}/`, { method: "DELETE" }),
    onSuccess: (_data, { projectId }) => {
      void queryClient.invalidateQueries({ queryKey: ["director-project", projectId] });
    },
  });
}

export function useAddClipReference() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ clipId, kind, file }: { projectId: number; clipId: number; kind: ReferenceKind; file: File }) => {
      const form = new FormData();
      form.set("kind", kind);
      form.set("file", file);
      return apiFetch<ClipReference>(`/director/clips/${clipId}/references/`, { method: "POST", body: form });
    },
    onSuccess: (_data, { projectId }) => {
      void queryClient.invalidateQueries({ queryKey: ["director-project", projectId] });
    },
  });
}

export function useDeleteClipReference() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ referenceId }: { projectId: number; referenceId: number }) =>
      apiFetch<void>(`/director/references/${referenceId}/`, { method: "DELETE" }),
    onSuccess: (_data, { projectId }) => {
      void queryClient.invalidateQueries({ queryKey: ["director-project", projectId] });
    },
  });
}

// Moves a clip's own reference up to a project-wide shared resource --
// see backend director/services.py's promote_clip_reference(). Lets a
// reference added to one clip (e.g. a character that turns out to recur)
// become addressable from every clip in the project without re-uploading
// it as a fresh shared resource.
export function usePromoteClipReference() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ referenceId }: { projectId: number; referenceId: number }) =>
      apiFetch<ProjectResource>(`/director/references/${referenceId}/promote/`, { method: "POST" }),
    onSuccess: (_data, { projectId }) => {
      void queryClient.invalidateQueries({ queryKey: ["director-project", projectId] });
    },
  });
}

export function useReorderClip() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ clipId, order }: { projectId: number; clipId: number; order: number }) =>
      apiFetch<Clip[]>(`/director/clips/${clipId}/reorder/`, {
        method: "POST",
        body: JSON.stringify({ order }),
      }),
    onSuccess: (_data, { projectId }) => {
      void queryClient.invalidateQueries({ queryKey: ["director-project", projectId] });
    },
  });
}

// Inserts a new clip right after this one, continuing directly from it --
// see backend director/services.py's split_clip(). For a scene whose
// prompt tries to cover too much in one beat: the new clip starts as a
// copy of this one's prompt, left to pare down into two separate beats.
export function useSplitClip() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ clipId }: { projectId: number; clipId: number }) =>
      apiFetch<Clip[]>(`/director/clips/${clipId}/split/`, { method: "POST" }),
    onSuccess: (_data, { projectId }) => {
      void queryClient.invalidateQueries({ queryKey: ["director-project", projectId] });
    },
  });
}

export function useRenderClip() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      clipId,
      force,
    }: {
      projectId: number;
      clipId: number;
      // Re-render an already-clean clip (e.g. the result wasn't liked) --
      // normally a no-op otherwise. See backend director/api.py's
      // render_clip() for the caveat on continues_previous clips reusing
      // the same derived seed.
      force?: boolean;
    }) =>
      apiFetch<Clip>(`/director/clips/${clipId}/render/`, {
        method: "POST",
        body: force ? JSON.stringify({ force: true }) : undefined,
      }),
    onSuccess: (_data, { projectId }) => {
      void queryClient.invalidateQueries({ queryKey: ["director-project", projectId] });
    },
  });
}

export function useRenderAllDirty() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (projectId: number) =>
      apiFetch<Clip[]>(`/director/projects/${projectId}/render_all/`, { method: "POST" }),
    onSuccess: (_data, projectId) => {
      void queryClient.invalidateQueries({ queryKey: ["director-project", projectId] });
    },
  });
}

export function useCancelClip() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ clipId }: { projectId: number; clipId: number }) =>
      apiFetch<Clip>(`/director/clips/${clipId}/cancel/`, { method: "POST" }),
    onSuccess: (_data, { projectId }) => {
      void queryClient.invalidateQueries({ queryKey: ["director-project", projectId] });
    },
  });
}

export function useCancelAllRenders() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (projectId: number) =>
      apiFetch<Clip[]>(`/director/projects/${projectId}/cancel_all/`, { method: "POST" }),
    onSuccess: (_data, projectId) => {
      void queryClient.invalidateQueries({ queryKey: ["director-project", projectId] });
    },
  });
}

// One-shot AI review of every clip's prompt for continuity/consistency
// issues -- see backend director/api.py's check_continuity(). Purely
// informational, doesn't change anything, so no cache invalidation.
export function useCheckContinuity() {
  return useMutation({
    mutationFn: (projectId: number) =>
      apiFetch<{ report: string }>(`/director/projects/${projectId}/check_continuity/`, { method: "POST" }),
  });
}

// "Generate from script" -- see ScriptPlanModal. Preview-only: nothing is
// created until the result is reviewed/edited and sent to useApplyPlan().
export function usePlanFromScript() {
  return useMutation({
    mutationFn: ({ projectId, ideaText }: { projectId: number; ideaText: string }) =>
      apiFetch<{ scenes: PlannedScene[] }>(`/director/projects/${projectId}/plan/`, {
        method: "POST",
        body: JSON.stringify({ idea_text: ideaText }),
      }),
  });
}

// "Generate from script"'s reference-suggestion step -- see
// ScriptPlanModal. Preview-only, same as usePlanFromScript(): nothing is
// generated or attached until the user clicks "Generate" on an individual
// candidate (see useCreateJob()/useCreateProjectResource()).
export function useExtractReferences() {
  return useMutation({
    mutationFn: ({ projectId, ideaText }: { projectId: number; ideaText: string }) =>
      apiFetch<{ candidates: ReferenceCandidate[] }>(`/director/projects/${projectId}/extract_references/`, {
        method: "POST",
        body: JSON.stringify({ idea_text: ideaText }),
      }),
  });
}

export function useApplyPlan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      projectId,
      scenes,
      replace,
      ideaText,
    }: {
      projectId: number;
      scenes: PlannedScene[];
      replace: boolean;
      // Saved onto the project as script_text for later review -- see
      // backend director/api.py's apply_plan().
      ideaText?: string;
    }) =>
      apiFetch<Clip[]>(`/director/projects/${projectId}/plan/apply/`, {
        method: "POST",
        body: JSON.stringify({ scenes, replace, idea_text: ideaText }),
      }),
    onSuccess: (_data, { projectId }) => {
      void queryClient.invalidateQueries({ queryKey: ["director-project", projectId] });
    },
  });
}

export function useAssembleProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (projectId: number) =>
      apiFetch<ProjectDetail>(`/director/projects/${projectId}/assemble/`, { method: "POST" }),
    onSuccess: (_data, projectId) => {
      void queryClient.invalidateQueries({ queryKey: ["director-project", projectId] });
    },
  });
}
