// TanStack Query hooks for Director Mode -- same one-hook-per-endpoint
// convention as api/queries.ts, kept in its own file since this is a
// large, mostly-separate feature area (see backend director/api.py).

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "./client";
import type { Clip, ClipReference, PlannedScene, Project, ProjectDetail, ProjectResource } from "./directorTypes";
import type { Mode, ReferenceKind } from "./types";

const ACTIVE_JOB_STATUSES = new Set(["queued", "processing"]);

function projectHasActiveClip(project: ProjectDetail | undefined): boolean {
  return !!project?.clips.some((c) => c.current_job_status && ACTIVE_JOB_STATUSES.has(c.current_job_status));
}

export function useDirectorProjects() {
  return useQuery({
    queryKey: ["director-projects"],
    queryFn: () => apiFetch<Project[]>("/director/projects/"),
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
    }: {
      projectId: number;
      title?: string;
      overarchingPrompt?: string;
      // Project-wide -- changing either recomputes every clip's
      // preset/width/height and marks the whole project dirty.
      aspectRatio?: string;
      qualityLabel?: string;
    }) =>
      apiFetch<ProjectDetail>(`/director/projects/${projectId}/`, {
        method: "PATCH",
        body: JSON.stringify({
          title,
          overarching_prompt: overarchingPrompt,
          aspect_ratio: aspectRatio,
          quality_label: qualityLabel,
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
    mutationFn: (projectId: number) => apiFetch<void>(`/director/projects/${projectId}/`, { method: "DELETE" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["director-projects"] });
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

export function useRenderClip() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ clipId }: { projectId: number; clipId: number }) =>
      apiFetch<Clip>(`/director/clips/${clipId}/render/`, { method: "POST" }),
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

export function useApplyPlan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, scenes, replace }: { projectId: number; scenes: PlannedScene[]; replace: boolean }) =>
      apiFetch<Clip[]>(`/director/projects/${projectId}/plan/apply/`, {
        method: "POST",
        body: JSON.stringify({ scenes, replace }),
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
