"""DRF views for Director Mode. Same minimal-validation style as
generation/api.py (plain dict/request.FILES checks, *_serializer classes
for drf-spectacular docs only, not real (de)serialization) -- see that
module's own docstring for the reasoning, followed here for consistency.
"""

from __future__ import annotations

from pathlib import Path
import tempfile

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.response import Response

from generation.api import _MAX_REFERENCE_AUDIO, _MAX_REFERENCE_IMAGES, _MAX_REFERENCE_VIDEO
from generation.models import GenerationJob, Mode, ReferenceAsset, RenderDuration
from generation.resolution import ASPECT_RATIO_VALUES, DEFAULT_ASPECT_RATIO, compute_resolution, is_valid_aspect_ratio
from integrations import assembly, comfyui, llm

from . import services
from .models import CONTINUATION_CAPABLE_MODES, Clip, ClipReferenceAsset, JobProjectTag, Project, ProjectResource


class ErrorResponseSerializer(serializers.Serializer):
    error = serializers.CharField()


class ProjectResourceSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kind = serializers.ChoiceField(choices=ProjectResource.Kind.choices)
    order = serializers.IntegerField()
    label = serializers.CharField(help_text="Human label if set, else the <Picture N>-style token.")
    token_label = serializers.CharField(
        help_text="The literal <Picture N>/<Video N>/<Audio N> token this resource maps to at "
        "render time -- use this (not `label`, which may be a human override) when writing "
        "prompt text or building an LLM reference_labels list."
    )
    url = serializers.CharField(allow_null=True)


class ClipReferenceSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kind = serializers.ChoiceField(choices=ReferenceAsset.Kind.choices)
    order = serializers.IntegerField()
    label = serializers.CharField()
    url = serializers.CharField(allow_null=True)


class ClipSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    project = serializers.IntegerField(source="project_id")
    order = serializers.IntegerField()
    continues_previous = serializers.BooleanField()
    mode = serializers.ChoiceField(choices=Mode.choices)
    prompt = serializers.CharField()
    improved_prompt = serializers.CharField()
    preset_id = serializers.IntegerField()
    duration_id = serializers.IntegerField()
    width = serializers.IntegerField()
    height = serializers.IntegerField()
    needs_render = serializers.BooleanField(help_text="The red-border dirty flag.")
    current_job_id = serializers.IntegerField(allow_null=True)
    current_job_status = serializers.CharField(allow_null=True)
    phase = serializers.CharField(allow_null=True, help_text="See GenerationJob.Phase -- null unless processing.")
    progress_current = serializers.IntegerField(allow_null=True)
    progress_total = serializers.IntegerField(allow_null=True)
    video_url = serializers.CharField(allow_null=True)
    thumbnail_url = serializers.CharField(allow_null=True)
    error_message = serializers.CharField(allow_null=True)
    references = ClipReferenceSerializer(many=True)


class ProjectSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    overarching_prompt = serializers.CharField()
    aspect_ratio = serializers.CharField(help_text="Applies to every Clip in the project -- not chosen per-clip.")
    quality_label = serializers.CharField(help_text="The shared quality tier every Clip's own preset is resolved from.")
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    clip_count = serializers.IntegerField(
        allow_null=True, help_text="Only set on the list endpoint (GET /projects/), null on a single-project fetch."
    )
    dirty_count = serializers.IntegerField(allow_null=True, help_text="How many of clip_count still need_render.")
    active_count = serializers.IntegerField(allow_null=True, help_text="How many clips are currently queued/processing.")
    eta_seconds = serializers.IntegerField(
        allow_null=True,
        help_text="Sum of estimated_render_seconds over every dirty clip's duration -- a rough "
        "\"time to finish this project if rendered now\", not a live queue position.",
    )


class ProjectDetailSerializer(ProjectSerializer):
    resources = ProjectResourceSerializer(many=True)
    clips = ClipSerializer(many=True)
    assembled_video_url = serializers.CharField(allow_null=True)
    script_text = serializers.CharField(
        allow_blank=True, help_text="The script/idea text last used by 'Generate from script', if any."
    )


class JobMembershipSerializer(serializers.Serializer):
    job_id = serializers.IntegerField()
    project_id = serializers.IntegerField()
    project_title = serializers.CharField(
        allow_blank=True, help_text="May be blank -- a Director project's own title is optional."
    )


class PlannedSceneSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=Mode.choices)
    continues_previous = serializers.BooleanField()
    duration_seconds = serializers.FloatField(
        required=False,
        allow_null=True,
        help_text="Requested clip length; matched to the nearest available RenderDuration when "
        "applied. Ignored for a scene that continues_previous -- it's locked to its run's own "
        "duration instead.",
    )
    prompt = serializers.CharField()
    notes = serializers.CharField(allow_blank=True, required=False)


class PlanRequestSerializer(serializers.Serializer):
    idea_text = serializers.CharField(help_text="A pasted script or loose idea to break into scenes.")


class PlanResponseSerializer(serializers.Serializer):
    scenes = PlannedSceneSerializer(many=True)


class ApplyPlanRequestSerializer(serializers.Serializer):
    scenes = PlannedSceneSerializer(many=True)
    replace = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Delete all existing clips first instead of appending after them.",
    )
    idea_text = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="The script/idea text these scenes were generated from, if any -- saved onto "
        "the project as Project.script_text for later review, purely informational.",
    )


class ReferenceCandidateSerializer(serializers.Serializer):
    name = serializers.CharField(help_text="Short human label, e.g. \"Mara\" or \"Mara's voice\".")
    kind = serializers.ChoiceField(choices=["image", "audio"])
    description = serializers.CharField(
        help_text="Ready-to-use t2i (kind=image) or t2a (kind=audio) generation prompt for this "
        "one reference asset -- submit verbatim as a job's raw_prompt."
    )


class ExtractReferencesResponseSerializer(serializers.Serializer):
    candidates = ReferenceCandidateSerializer(many=True)


class ContinuityCheckResponseSerializer(serializers.Serializer):
    report = serializers.CharField(help_text="The AI's plain-text (markdown-ish) review, unparsed.")


def _serialize_resource(resource: ProjectResource) -> dict:
    return {
        "id": resource.id,
        "kind": resource.kind,
        "order": resource.order,
        "label": resource.label or resource.token_label,
        "token_label": resource.token_label,
        "url": resource.file.url if resource.file else None,
    }


def _serialize_clip_reference(ref: ClipReferenceAsset) -> dict:
    return {"id": ref.id, "kind": ref.kind, "order": ref.order, "label": ref.label, "url": ref.file.url if ref.file else None}


def _serialize_clip(clip: Clip) -> dict:
    job = clip.current_job
    return {
        "id": clip.id,
        "project_id": clip.project_id,
        "order": clip.order,
        "continues_previous": clip.continues_previous,
        "mode": clip.mode,
        "prompt": clip.prompt,
        "improved_prompt": clip.improved_prompt,
        "preset_id": clip.preset_id,
        "duration_id": clip.duration_id,
        "width": clip.width,
        "height": clip.height,
        "needs_render": clip.needs_render,
        "current_job_id": job.id if job else None,
        "current_job_status": job.status if job else None,
        "phase": job.phase or None if job else None,
        "progress_current": job.progress_current if job else None,
        "progress_total": job.progress_total if job else None,
        "video_url": job.video_file.url if job and job.video_file else None,
        "thumbnail_url": job.thumbnail_file.url if job and job.thumbnail_file else None,
        "error_message": job.error_message if job else None,
        "references": [_serialize_clip_reference(r) for r in clip.references.all()],
    }


def _serialize_project(project: Project, *, detail: bool = False) -> dict:
    data = {
        "id": project.id,
        "title": project.title,
        "overarching_prompt": project.overarching_prompt,
        "aspect_ratio": project.aspect_ratio,
        "quality_label": project.quality_label,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        # Only present (non-None) when `project` came from projects()' GET
        # queryset, which annotates these -- see that view. A plain
        # Project.objects.get()/.filter() elsewhere (project_detail() etc.)
        # has no such attributes, so getattr's default keeps this branch
        # from blowing up there.
        "clip_count": getattr(project, "clip_count", None),
        "dirty_count": getattr(project, "dirty_count", None),
        "active_count": getattr(project, "active_count", None),
        "eta_seconds": getattr(project, "eta_seconds", None),
    }
    if detail:
        data["resources"] = [_serialize_resource(r) for r in project.resources.all()]
        data["clips"] = [_serialize_clip(c) for c in project.clips.select_related("current_job").all()]
        data["assembled_video_url"] = project.assembled_video_file.url if project.assembled_video_file else None
        data["script_text"] = project.script_text
    return data


def _get_project(request, project_id: int) -> Project:
    return get_object_or_404(Project, id=project_id, user=request.user)


def _get_clip(request, clip_id: int) -> Clip:
    return get_object_or_404(
        Clip.objects.select_related("project", "current_job"), id=clip_id, project__user=request.user
    )


@extend_schema(
    methods=["GET"],
    summary="List the user's Director projects",
    responses=ProjectSerializer(many=True),
    tags=["director"],
)
@extend_schema(
    methods=["POST"],
    summary="Create a Director project",
    responses={201: ProjectDetailSerializer},
    tags=["director"],
)
@api_view(["GET", "POST"])
def projects(request):
    if request.method == "GET":
        # filter=Q(...) on each aggregate is conditional aggregation (SQL
        # CASE WHEN inside the aggregate function) rather than a separate
        # JOIN per annotation, so combining Count/Sum here over the same
        # `clips` relation doesn't fan out/double-count -- see Django's
        # aggregation docs on filtered aggregates.
        active_statuses = [GenerationJob.Status.QUEUED, GenerationJob.Status.PROCESSING]
        qs = Project.objects.filter(user=request.user).annotate(
            clip_count=Count("clips", distinct=True),
            dirty_count=Count("clips", filter=Q(clips__needs_render=True), distinct=True),
            active_count=Count(
                "clips", filter=Q(clips__current_job__status__in=active_statuses), distinct=True
            ),
            eta_seconds=Sum(
                "clips__duration__estimated_render_seconds", filter=Q(clips__needs_render=True)
            ),
        )
        return Response([_serialize_project(p) for p in qs])

    title = request.data.get("title", "")
    overarching_prompt = request.data.get("overarching_prompt", "")

    aspect_ratio = request.data.get("aspect_ratio") or DEFAULT_ASPECT_RATIO
    if not is_valid_aspect_ratio(aspect_ratio):
        return Response({"error": f"aspect_ratio must be one of {ASPECT_RATIO_VALUES}, or a custom W:H ratio."}, status=400)

    available_labels = services.available_quality_labels()
    quality_label = request.data.get("quality_label") or (available_labels[0] if available_labels else "")
    if quality_label and quality_label not in available_labels:
        return Response({"error": f"quality_label must be one of {available_labels}."}, status=400)

    project = Project.objects.create(
        user=request.user,
        title=title,
        overarching_prompt=overarching_prompt,
        aspect_ratio=aspect_ratio,
        quality_label=quality_label,
    )
    return Response(_serialize_project(project, detail=True), status=201)


@extend_schema(
    summary="Which Director project each of the user's jobs belongs to, if any",
    description="Every GenerationJob ever created under one of the user's Director projects, as "
    "{job_id, project_id, project_title} rows -- backed by JobProjectTag, a permanent record set "
    "once when the job is created (see director/services.py's _build_job_for_clip()/"
    "create_project_from_job()) that outlives the job being superseded by a later re-render, "
    "unlike Clip.current_job (a single 'latest render' pointer that moves on and leaves the "
    "previous job looking like an untagged standalone one). Lets the main Generate page's "
    "queue/history show 'part of <project>' for a job that started inside Director, or offer "
    "'Create Director project' for a standalone one that isn't in this list. Kept as its own "
    "endpoint (rather than a field on GenerationJobSerializer) so the generation app never has to "
    "import anything from director -- see director/models.py's own layering note.",
    responses=JobMembershipSerializer(many=True),
    tags=["director"],
)
@api_view(["GET"])
def job_memberships(request):
    rows = JobProjectTag.objects.filter(project__user=request.user).values(
        "job_id", "project_id", "project__title"
    )
    return Response(
        [
            {"job_id": r["job_id"], "project_id": r["project_id"], "project_title": r["project__title"]}
            for r in rows
        ]
    )


@extend_schema(
    summary="Create a new Director project from an existing standalone job",
    description="Wraps an already-rendered job from the main Generate page as a new project's "
    "first clip -- reuses the existing render rather than queuing a new one. Only works for a "
    "successfully finished video-mode (t2v/i2v/r2v) job that isn't already part of a Director "
    "project (see job_memberships/ to check first).",
    responses={201: ProjectDetailSerializer, 400: ErrorResponseSerializer, 404: OpenApiResponse(description="Not found.")},
    tags=["director"],
)
@api_view(["POST"])
def create_project_from_job(request, job_id: int):
    job = get_object_or_404(GenerationJob, id=job_id, user=request.user)
    try:
        project = services.create_project_from_job(job)
    except services.PlanError as exc:
        return Response({"error": str(exc)}, status=400)
    return Response(_serialize_project(project, detail=True), status=201)


@extend_schema(
    methods=["GET"],
    summary="Get a Director project (with its resources and clips)",
    responses={200: ProjectDetailSerializer, 404: OpenApiResponse(description="Not found.")},
    tags=["director"],
)
@extend_schema(
    methods=["PATCH"],
    summary="Update a Director project",
    description="Changing overarching_prompt marks every Clip in the project dirty -- every "
    "Clip's render depends on it. Changing aspect_ratio/quality_label additionally recomputes "
    "every Clip's preset/width/height (see director/services.py's recompute_project_resolutions).",
    responses={200: ProjectDetailSerializer, 404: OpenApiResponse(description="Not found.")},
    tags=["director"],
)
@extend_schema(
    methods=["DELETE"],
    summary="Delete a Director project",
    description="Cleans up every file involved (clip/resource references, the project's own "
    "assembled export) before the row-level cascade removes the database rows. Pass "
    "delete_related_jobs=true to also delete every GenerationJob ever rendered under this "
    "project (see JobProjectTag) -- off by default, so those renders survive as ordinary "
    "untagged jobs in the main Generate page rather than being destroyed along with the project "
    "wrapper. A job currently queued/processing is never deleted either way.",
    responses={204: OpenApiResponse(description="Deleted."), 404: OpenApiResponse(description="Not found.")},
    tags=["director"],
)
@api_view(["GET", "PATCH", "DELETE"])
def project_detail(request, project_id: int):
    project = _get_project(request, project_id)

    if request.method == "DELETE":
        delete_related_jobs = str(request.data.get("delete_related_jobs", "")).lower() in ("1", "true", "yes", "on")
        services.delete_project(project, delete_related_jobs=delete_related_jobs)
        return Response(status=204)

    if request.method == "PATCH":
        dirty = False
        resolution_changed = False
        if "title" in request.data:
            project.title = request.data["title"]
        if "script_text" in request.data:
            project.script_text = request.data["script_text"]
        if "overarching_prompt" in request.data:
            project.overarching_prompt = request.data["overarching_prompt"]
            dirty = True
        if "aspect_ratio" in request.data:
            aspect_ratio = request.data["aspect_ratio"]
            if not is_valid_aspect_ratio(aspect_ratio):
                return Response(
                    {"error": f"aspect_ratio must be one of {ASPECT_RATIO_VALUES}, or a custom W:H ratio."},
                    status=400,
                )
            if aspect_ratio != project.aspect_ratio:
                project.aspect_ratio = aspect_ratio
                resolution_changed = True
        if "quality_label" in request.data:
            quality_label = request.data["quality_label"]
            available_labels = services.available_quality_labels()
            if quality_label not in available_labels:
                return Response({"error": f"quality_label must be one of {available_labels}."}, status=400)
            if quality_label != project.quality_label:
                project.quality_label = quality_label
                resolution_changed = True
        project.save()
        if resolution_changed:
            services.recompute_project_resolutions(project)
        if dirty or resolution_changed:
            services.mark_project_dirty(project)
        return Response(_serialize_project(project, detail=True))

    return Response(_serialize_project(project, detail=True))


@extend_schema(
    methods=["GET"], summary="List a project's resources", responses=ProjectResourceSerializer(many=True), tags=["director"]
)
@extend_schema(
    methods=["POST"],
    summary="Add a project resource (character sheet / voice / world reference)",
    description="Marks every Clip in the project dirty -- see project_detail's PATCH. Rejected "
    "if the project has any non-reference (t2v/i2v) clip -- only r2v clips can actually wire a "
    "shared resource into a render, so every clip must be r2v while one is attached.",
    responses={201: ProjectResourceSerializer, 400: ErrorResponseSerializer},
    tags=["director"],
)
@api_view(["GET", "POST"])
def project_resources(request, project_id: int):
    project = _get_project(request, project_id)

    if request.method == "GET":
        return Response([_serialize_resource(r) for r in project.resources.all()])

    kind = request.data.get("kind")
    if kind not in ProjectResource.Kind.values:
        return Response({"error": f"kind must be one of {ProjectResource.Kind.values}"}, status=400)
    if project.clips.exclude(mode=Mode.REFERENCE_TO_VIDEO).exists():
        return Response(
            {
                "error": "This project has non-reference clips -- every clip must be a reference "
                "clip while shared references are attached. Remove or delete them first."
            },
            status=400,
        )

    limits = {
        ProjectResource.Kind.IMAGE: _MAX_REFERENCE_IMAGES,
        ProjectResource.Kind.AUDIO: _MAX_REFERENCE_AUDIO,
        ProjectResource.Kind.VIDEO: _MAX_REFERENCE_VIDEO,
    }[kind]
    max_for_kind = limits[Mode.REFERENCE_TO_VIDEO]
    old_count = project.resources.filter(kind=kind).count()
    # Every clip in the project is r2v at this point (checked above), so
    # they all share the same limit -- find the clip that already has the
    # most of its own references of this kind, since that's the one a new
    # shared resource would push over first.
    worst_clip_count = max((c.references.filter(kind=kind).count() for c in project.clips.all()), default=0)
    if old_count + 1 + worst_clip_count > max_for_kind:
        return Response(
            {
                "error": f"Reference clips support at most {max_for_kind} {kind} reference(s) total "
                f"(shared + a clip's own) -- adding this would put at least one clip over that limit."
            },
            status=400,
        )

    file = request.FILES.get("file")
    if file is None:
        return Response({"error": "file is required."}, status=400)
    label = request.data.get("label", "")
    resource = ProjectResource.objects.create(project=project, kind=kind, order=old_count, file=file, label=label)
    # This resource's own new position is stable (appended at the end of
    # its kind), but every clip's own references of this kind just moved
    # one slot later in the combined numbering (see _combined_references())
    # -- keep any prompt text mentioning them pointing at the right thing.
    services.renumber_clip_reference_tokens(project, kind, old_count, old_count + 1)
    services.mark_project_dirty(project)
    return Response(_serialize_resource(resource), status=201)


@extend_schema(
    summary="Convert every non-reference clip in a project to reference-to-video mode",
    description="Switches every t2v/i2v clip to r2v in place (no clip is deleted, and no "
    "existing clip reference is dropped -- r2v's own per-kind reference limits are >= every "
    "other mode's) and marks the whole project dirty. Lets a project with existing clips start "
    "using shared references without deleting them first, since POST resources/ otherwise "
    "rejects adding one while any non-reference clip exists.",
    responses={200: ClipSerializer(many=True), 400: ErrorResponseSerializer, 404: OpenApiResponse(description="Not found.")},
    tags=["director"],
)
@api_view(["POST"])
def convert_to_reference(request, project_id: int):
    project = _get_project(request, project_id)
    try:
        services.convert_clips_to_reference(project)
    except services.PlanError as exc:
        return Response({"error": str(exc)}, status=400)
    return Response([_serialize_clip(c) for c in project.clips.order_by("order")])


@extend_schema(
    summary="Delete a project resource",
    responses={204: OpenApiResponse(description="Deleted."), 404: OpenApiResponse(description="Not found.")},
    tags=["director"],
)
@api_view(["DELETE"])
def resource_detail(request, resource_id: int):
    resource = get_object_or_404(ProjectResource, id=resource_id, project__user=request.user)
    project = resource.project
    kind = resource.kind
    old_count = project.resources.filter(kind=kind).count()
    resource.file.delete(save=False)
    resource.delete()
    services.renumber_clip_reference_tokens(project, kind, old_count, old_count - 1)
    services.mark_project_dirty(project)
    return Response(status=204)


class CreateClipRequestSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=Mode.choices)
    duration_id = serializers.IntegerField()
    continues_previous = serializers.BooleanField(required=False, default=False)
    prompt = serializers.CharField(required=False, allow_blank=True)
    reference_images = serializers.ListField(child=serializers.FileField(), required=False)
    reference_audio = serializers.ListField(child=serializers.FileField(), required=False)
    reference_video = serializers.ListField(child=serializers.FileField(), required=False)


def _resolve_clip_duration_and_resolution(request, project: Project, mode: str, continues_previous: bool):
    """Returns (preset, duration, width, height) or a Response to return
    directly on validation failure -- same "value-or-Response" pattern
    generation/api.py's _validate_mode() uses. Quality/aspect ratio are
    project-wide (Project.quality_label/aspect_ratio) -- this Clip's own
    preset/width/height are just derived from them, not independently
    chosen at creation time.
    """
    preset = services.resolve_preset_for_mode(project.quality_label, mode)
    if preset is None:
        return Response({"error": f"No active render preset is configured for mode {mode!r}."}, status=400)

    if continues_previous:
        predecessor = project.clips.order_by("-order").first()
        if predecessor is None:
            return Response({"error": "continues_previous requires an existing predecessor clip."}, status=400)
        # Duration is locked to the predecessor's own value while
        # continuing, same as width/height -- real chain continuity
        # submits one duration for the whole run, see
        # services.resolve_clip_duration()'s docstring. duration_id from
        # the request is ignored here, not validated.
        return preset, predecessor.duration, predecessor.width, predecessor.height

    duration = RenderDuration.objects.filter(
        id=request.data.get("duration_id"), preset=preset, is_active=True
    ).first()
    if duration is None:
        return Response(
            {"error": "duration_id must reference an active duration option for this project's quality tier."},
            status=400,
        )
    width, height = compute_resolution(preset.megapixels, project.aspect_ratio)
    return preset, duration, width, height


@extend_schema(
    methods=["GET"], summary="List a project's clips, in order", responses=ClipSerializer(many=True), tags=["director"]
)
@extend_schema(
    methods=["POST"],
    summary="Append a clip to a project",
    request=CreateClipRequestSerializer,
    responses={201: ClipSerializer, 400: ErrorResponseSerializer},
    tags=["director"],
)
@api_view(["GET", "POST"])
def clips(request, project_id: int):
    project = _get_project(request, project_id)

    if request.method == "GET":
        return Response([_serialize_clip(c) for c in project.clips.select_related("current_job")])

    mode = request.data.get("mode")
    if mode not in Mode.values:
        return Response({"error": f"mode must be one of {Mode.values}"}, status=400)

    if mode != Mode.REFERENCE_TO_VIDEO and services.project_requires_reference_mode(project):
        return Response(
            {"error": "This project has shared references -- every clip must be a reference clip."},
            status=400,
        )

    continues_previous = str(request.data.get("continues_previous", "")).lower() in ("1", "true", "yes", "on")
    if continues_previous and mode not in CONTINUATION_CAPABLE_MODES:
        return Response(
            {"error": f"continues_previous is only supported for modes {sorted(CONTINUATION_CAPABLE_MODES)}."},
            status=400,
        )

    resolved = _resolve_clip_duration_and_resolution(request, project, mode, continues_previous)
    if isinstance(resolved, Response):
        return resolved
    preset, duration, width, height = resolved

    reference_images = request.FILES.getlist("reference_images")
    reference_audio = request.FILES.getlist("reference_audio")
    reference_video = request.FILES.getlist("reference_video")
    for files, limits, kind, label in (
        (reference_images, _MAX_REFERENCE_IMAGES, ReferenceAsset.Kind.IMAGE, "image"),
        (reference_audio, _MAX_REFERENCE_AUDIO, ReferenceAsset.Kind.AUDIO, "audio"),
        (reference_video, _MAX_REFERENCE_VIDEO, ReferenceAsset.Kind.VIDEO, "video"),
    ):
        # This project's shared resources (if any -- only possible when
        # mode is r2v, see the gate above) share the same per-kind budget
        # as this clip's own references -- see _combined_references().
        project_count = project.resources.filter(kind=kind).count()
        if project_count + len(files) > limits[mode]:
            return Response(
                {
                    "error": f"{Mode(mode).label} supports at most {limits[mode]} {label} reference(s) "
                    f"total (this project already provides {project_count} shared)."
                },
                status=400,
            )

    last_order = project.clips.order_by("-order").values_list("order", flat=True).first()
    next_order = 0 if last_order is None else last_order + 1

    with transaction.atomic():
        clip = Clip.objects.create(
            project=project,
            order=next_order,
            continues_previous=continues_previous,
            mode=mode,
            prompt=request.data.get("prompt", ""),
            preset=preset,
            duration=duration,
            width=width,
            height=height,
        )
        for order, file in enumerate(reference_images):
            ClipReferenceAsset.objects.create(clip=clip, kind=ReferenceAsset.Kind.IMAGE, order=order, file=file)
        for order, file in enumerate(reference_audio):
            ClipReferenceAsset.objects.create(clip=clip, kind=ReferenceAsset.Kind.AUDIO, order=order, file=file)
        for order, file in enumerate(reference_video):
            ClipReferenceAsset.objects.create(clip=clip, kind=ReferenceAsset.Kind.VIDEO, order=order, file=file)

    return Response(_serialize_clip(clip), status=201)


@extend_schema(
    methods=["GET"],
    summary="Get a clip",
    responses={200: ClipSerializer, 404: OpenApiResponse(description="Not found.")},
    tags=["director"],
)
@extend_schema(
    methods=["PATCH"],
    summary="Edit a clip",
    description="Any render-affecting field change dirties this clip and cascades forward through "
    "directly-chained continuations (see director/services.py's mark_dirty_cascade). Quality "
    "(preset) and aspect ratio aren't editable here -- they're project-wide, see "
    "project_detail's PATCH -- only duration_id (length within the clip's already-resolved "
    "quality tier), prompt/improved_prompt, and continues_previous can change.",
    responses={200: ClipSerializer, 400: ErrorResponseSerializer, 404: OpenApiResponse(description="Not found.")},
    tags=["director"],
)
@extend_schema(
    methods=["DELETE"],
    summary="Delete a clip",
    responses={204: OpenApiResponse(description="Deleted."), 404: OpenApiResponse(description="Not found.")},
    tags=["director"],
)
@api_view(["GET", "PATCH", "DELETE"])
def clip_detail(request, clip_id: int):
    clip = _get_clip(request, clip_id)

    if request.method == "DELETE":
        if clip.current_job and clip.current_job.status == GenerationJob.Status.PROCESSING:
            return Response({"error": "Can't delete a clip that's currently rendering."}, status=409)
        for ref in clip.references.all():
            ref.file.delete(save=False)
        clip.delete()
        return Response(status=204)

    if request.method == "PATCH":
        editable_fields = {"prompt", "improved_prompt", "continues_previous"}
        changed = False
        resolution_may_change = False
        for field in editable_fields:
            if field in request.data:
                value = request.data[field]
                if field == "continues_previous":
                    value = str(value).lower() in ("1", "true", "yes", "on")
                    if value and clip.mode not in CONTINUATION_CAPABLE_MODES:
                        return Response(
                            {"error": f"continues_previous is only supported for modes {sorted(CONTINUATION_CAPABLE_MODES)}."},
                            status=400,
                        )
                    resolution_may_change = True
                setattr(clip, field, value)
                changed = True

        if "duration_id" in request.data:
            if clip.continues_previous:
                # Locked to the predecessor's own duration while
                # continuing -- real chain continuity submits one duration
                # for the whole run, see services.resolve_clip_duration()'s
                # docstring on why this isn't just cosmetic.
                return Response(
                    {"error": "duration is locked to the predecessor's while continues_previous is set."},
                    status=400,
                )
            # Quality (preset) is project-wide, not editable per-clip here
            # -- see Project.quality_label -- so this only ever swaps
            # length within the clip's already-resolved preset.
            duration = RenderDuration.objects.filter(
                id=request.data.get("duration_id"), preset=clip.preset, is_active=True
            ).first()
            if duration is None:
                return Response(
                    {"error": "duration_id must reference an active duration option for this clip's quality tier."},
                    status=400,
                )
            clip.duration = duration
            changed = True

        if resolution_may_change:
            # continues_previous just changed -- re-lock (or release)
            # width/height/duration to/from the immediate predecessor's
            # own values.
            clip.width, clip.height = services.resolve_clip_width_height(clip)
            clip.duration = services.resolve_clip_duration(clip)

        if changed:
            clip.save()
            services.mark_dirty_cascade(clip)
        return Response(_serialize_clip(clip))

    return Response(_serialize_clip(clip))


@extend_schema(
    summary="Add a reference (image/audio/video) to a clip",
    description="Dirties this clip and cascades forward, same as clip_detail's PATCH.",
    responses={201: ClipReferenceSerializer, 400: ErrorResponseSerializer, 404: OpenApiResponse(description="Not found.")},
    tags=["director"],
)
@api_view(["POST"])
def clip_references(request, clip_id: int):
    clip = _get_clip(request, clip_id)

    kind = request.data.get("kind")
    if kind not in ReferenceAsset.Kind.values:
        return Response({"error": f"kind must be one of {ReferenceAsset.Kind.values}"}, status=400)
    file = request.FILES.get("file")
    if file is None:
        return Response({"error": "file is required."}, status=400)

    limits = {
        ReferenceAsset.Kind.IMAGE: _MAX_REFERENCE_IMAGES,
        ReferenceAsset.Kind.AUDIO: _MAX_REFERENCE_AUDIO,
        ReferenceAsset.Kind.VIDEO: _MAX_REFERENCE_VIDEO,
    }[kind]
    max_for_mode = limits[clip.mode]
    # This project's shared resources (if any) share the same per-kind
    # budget as this clip's own references -- see _combined_references().
    project_count = clip.project.resources.filter(kind=kind).count()
    existing_count = clip.references.filter(kind=kind).count()
    if project_count + existing_count >= max_for_mode:
        return Response(
            {
                "error": f"{Mode(clip.mode).label} supports at most {max_for_mode} {kind} reference(s) "
                f"total (this project already provides {project_count} shared)."
            },
            status=400,
        )

    ref = ClipReferenceAsset.objects.create(clip=clip, kind=kind, order=existing_count, file=file)
    services.mark_dirty_cascade(clip)
    return Response(_serialize_clip_reference(ref), status=201)


@extend_schema(
    summary="Delete a clip reference",
    responses={204: OpenApiResponse(description="Deleted."), 404: OpenApiResponse(description="Not found.")},
    tags=["director"],
)
@api_view(["DELETE"])
def clip_reference_detail(request, reference_id: int):
    ref = get_object_or_404(ClipReferenceAsset, id=reference_id, clip__project__user=request.user)
    clip = ref.clip
    ref.file.delete(save=False)
    ref.delete()
    services.mark_dirty_cascade(clip)
    return Response(status=204)


@extend_schema(
    summary="Promote a clip's own reference to a project-wide shared resource",
    description="Moves this reference from being visible only to its own clip's render to being "
    "addressable by every clip in the project (see director/models.py's ProjectResource vs "
    "ClipReferenceAsset) -- for a reference added to one clip that turns out to recur elsewhere. "
    "Same preconditions as POST project_resources/: every clip in the project must already be a "
    "reference clip, and moving it mustn't push any other clip's own reference count of this kind "
    "over its per-kind limit. The originating clip's own prompt/improved_prompt text isn't "
    "rewritten (see services.promote_clip_reference()'s docstring) -- its <Picture N>/etc mentions "
    "of this reference may need a manual touch-up afterward.",
    responses={201: ProjectResourceSerializer, 400: ErrorResponseSerializer, 404: OpenApiResponse(description="Not found.")},
    tags=["director"],
)
@api_view(["POST"])
def promote_clip_reference(request, reference_id: int):
    ref = get_object_or_404(ClipReferenceAsset, id=reference_id, clip__project__user=request.user)
    clip = ref.clip
    project = clip.project

    if project.clips.exclude(mode=Mode.REFERENCE_TO_VIDEO).exists():
        return Response(
            {
                "error": "This project has non-reference clips -- every clip must be a reference "
                "clip before a reference can be made shared. Convert them first."
            },
            status=400,
        )

    limits = {
        ReferenceAsset.Kind.IMAGE: _MAX_REFERENCE_IMAGES,
        ReferenceAsset.Kind.AUDIO: _MAX_REFERENCE_AUDIO,
        ReferenceAsset.Kind.VIDEO: _MAX_REFERENCE_VIDEO,
    }[ref.kind]
    max_for_kind = limits[Mode.REFERENCE_TO_VIDEO]
    new_project_count = project.resources.filter(kind=ref.kind).count() + 1
    # Every *other* clip's own reference count of this kind is untouched by
    # the move -- only the source clip loses one, which frees up exactly
    # the slot the shared pool just gained for it (see
    # services.promote_clip_reference()'s docstring), so it can't be the
    # one that overflows here.
    worst_other_clip_count = max(
        (c.references.filter(kind=ref.kind).count() for c in project.clips.exclude(id=clip.id)), default=0
    )
    if new_project_count + worst_other_clip_count > max_for_kind:
        return Response(
            {
                "error": f"Reference clips support at most {max_for_kind} {ref.kind} reference(s) "
                f"total (shared + a clip's own) -- moving this one would put at least one other "
                f"clip over that limit."
            },
            status=400,
        )

    resource = services.promote_clip_reference(ref)
    return Response(_serialize_resource(resource), status=201)


@extend_schema(
    summary="Split a clip into two, chained via continues_previous",
    description="Inserts a new clip immediately after this one, continuing directly from it -- "
    "for a scene whose prompt tries to cover too much at once. The new clip starts as a copy of "
    "this clip's own prompt (pare each half down to its own beat) and inherits this clip's own "
    "duration/width/height, per MiniMax H3's continuity model. Every later clip's position shifts "
    "by one; if the clip that used to sit immediately after this one continues_previous, it's "
    "dirtied too, since what it positionally continues from has changed.",
    responses={201: ClipSerializer(many=True), 400: ErrorResponseSerializer, 404: OpenApiResponse(description="Not found.")},
    tags=["director"],
)
@api_view(["POST"])
def split_clip(request, clip_id: int):
    clip = _get_clip(request, clip_id)
    try:
        services.split_clip(clip)
    except services.PlanError as exc:
        return Response({"error": str(exc)}, status=400)
    return Response(
        [_serialize_clip(c) for c in Clip.objects.filter(project_id=clip.project_id).order_by("order")],
        status=201,
    )


@extend_schema(
    summary="Reorder a clip within its project",
    description="Renumbers every sibling clip and always dirties the moved clip -- chain "
    "semantics are positional (continues_previous means 'continue from whichever clip is now "
    "immediately before me'), so a reorder can change what it actually continues from.",
    responses={200: ClipSerializer(many=True), 400: ErrorResponseSerializer, 404: OpenApiResponse(description="Not found.")},
    tags=["director"],
)
@api_view(["POST"])
def reorder_clip(request, clip_id: int):
    clip = _get_clip(request, clip_id)
    new_order = request.data.get("order")
    if not isinstance(new_order, int):
        return Response({"error": "order (integer) is required."}, status=400)

    with transaction.atomic():
        siblings = list(
            Clip.objects.select_for_update()
            .filter(project_id=clip.project_id)
            .exclude(id=clip.id)
            .order_by("order")
        )
        new_order = max(0, min(new_order, len(siblings)))
        siblings.insert(new_order, clip)
        for index, sibling in enumerate(siblings):
            if sibling.order != index:
                sibling.order = index
                sibling.save(update_fields=["order"])
        services.mark_dirty_cascade(clip)

    return Response([_serialize_clip(c) for c in Clip.objects.filter(project_id=clip.project_id).order_by("order")])


@extend_schema(
    summary="Render a clip (and any dirty continuation predecessors it depends on)",
    description="Normally a no-op if the clip isn't dirty. Pass force=true to re-render an "
    "already-clean clip anyway (e.g. the result wasn't liked) -- for a continues_previous clip "
    "this resumes the same chain run/scene, which reuses the same derived seed, so an unchanged "
    "prompt is likely to reproduce a near-identical result rather than a different one.",
    responses={
        200: ClipSerializer,
        404: OpenApiResponse(description="Not found."),
        409: OpenApiResponse(ErrorResponseSerializer, description="A clip in the chain already has a job in flight."),
    },
    tags=["director"],
)
@api_view(["POST"])
def render_clip(request, clip_id: int):
    clip = _get_clip(request, clip_id)
    force = str(request.data.get("force", "")).lower() in ("1", "true", "yes", "on")
    try:
        services.render_clip(clip, force=force)
    except services.RenderConflict as exc:
        return Response({"error": str(exc)}, status=409)
    clip.refresh_from_db()
    return Response(_serialize_clip(clip))


@extend_schema(
    summary="Render every dirty clip in a project",
    responses={200: ClipSerializer(many=True), 404: OpenApiResponse(description="Not found.")},
    tags=["director"],
)
@api_view(["POST"])
def render_all_dirty(request, project_id: int):
    project = _get_project(request, project_id)
    services.render_all_dirty(project)
    return Response([_serialize_clip(c) for c in project.clips.select_related("current_job")])


def _cancel_clip_job(clip: Clip) -> bool:
    """Cancels `clip`'s in-flight job, if it has one -- a queued job is
    cancelled directly, a processing one is flagged and stopped
    best-effort (same semantics as generation/api.py's cancel_job). Also
    clears render_chain_target so an in-flight chain stops advancing past
    this clip. Returns False (no-op) if there was nothing to cancel, so
    callers can tell a real cancellation from a skip -- see cancel_clip()'s
    409 and cancel_all()'s summary count.
    """
    job = clip.current_job
    if job is None or job.status not in (GenerationJob.Status.QUEUED, GenerationJob.Status.PROCESSING):
        return False

    with transaction.atomic():
        Clip.objects.filter(pk=clip.pk).update(render_chain_target=None)
        if job.status == GenerationJob.Status.QUEUED:
            job.status = GenerationJob.Status.CANCELLED
            job.error_message = "Cancelled by user."
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "error_message", "finished_at"])
        else:
            job.cancel_requested = True
            job.save(update_fields=["cancel_requested"])

    if job.status == GenerationJob.Status.PROCESSING and job.comfyui_prompt_id:
        comfyui.cancel_prompt(job.comfyui_prompt_id)
    return True


@extend_schema(
    summary="Cancel a clip's in-flight render",
    description="Same semantics as generation/api.py's cancel_job -- a queued job is cancelled "
    "directly, a processing one is flagged and stopped best-effort. Also clears any pending "
    "chain-render target, so an in-flight chain stops advancing past this clip.",
    responses={
        200: ClipSerializer,
        404: OpenApiResponse(description="Not found."),
        409: OpenApiResponse(ErrorResponseSerializer, description="Nothing to cancel."),
    },
    tags=["director"],
)
@api_view(["POST"])
def cancel_clip(request, clip_id: int):
    clip = _get_clip(request, clip_id)
    if not _cancel_clip_job(clip):
        return Response({"error": "This clip isn't queued or processing -- nothing to cancel."}, status=409)
    clip.refresh_from_db()
    return Response(_serialize_clip(clip))


@extend_schema(
    summary="Cancel every in-flight render in a project",
    description="Cancels every clip currently queued or processing, and clears every clip's "
    "pending chain-render target project-wide -- stops \"Render all dirty\" mid-flight, including "
    "any chain still auto-advancing. Clips that were already idle are left untouched (not an "
    "error -- this is a broad \"stop everything\" action, not a precise one).",
    responses={200: ClipSerializer(many=True), 404: OpenApiResponse(description="Not found.")},
    tags=["director"],
)
@api_view(["POST"])
def cancel_all(request, project_id: int):
    project = _get_project(request, project_id)
    for clip in project.clips.select_related("current_job"):
        _cancel_clip_job(clip)
    return Response([_serialize_clip(c) for c in project.clips.select_related("current_job")])


@extend_schema(
    summary="Ask the AI to review every clip's prompt for continuity/consistency issues",
    description="Sends every clip's prompt (in order, with mode/continues_previous) to the "
    "configured LLM and returns its plain-text report -- doesn't change anything, purely "
    "informational.",
    responses={
        200: ContinuityCheckResponseSerializer,
        404: OpenApiResponse(description="Not found."),
        502: OpenApiResponse(ErrorResponseSerializer, description="The LLM request itself failed."),
        503: OpenApiResponse(ErrorResponseSerializer, description="No LLM is configured."),
    },
    tags=["director"],
)
@api_view(["POST"])
def check_continuity(request, project_id: int):
    project = _get_project(request, project_id)
    if not settings.LLM_ENABLED:
        return Response({"error": "No LLM is configured."}, status=503)

    clips = [
        {"order": c.order, "mode": c.mode, "continues_previous": c.continues_previous, "prompt": c.prompt}
        for c in project.clips.order_by("order")
    ]
    try:
        report = llm.check_project_continuity(project.overarching_prompt, clips)
    except llm.LLMError as exc:
        return Response({"error": str(exc)}, status=502)
    return Response({"report": report})


@extend_schema(
    summary="Generate a proposed clip sequence from a script/idea (preview only, not saved)",
    description="Turns a pasted script/idea into an ordered list of proposed scenes via the "
    "configured LLM -- nothing is created yet. Review/edit the result client-side, then POST it "
    "to plan/apply/ to actually create clips from it. If the project has shared references, every "
    "proposed scene is forced to be a reference clip and is written to use them where relevant.",
    request=PlanRequestSerializer,
    responses={
        200: PlanResponseSerializer,
        400: ErrorResponseSerializer,
        404: OpenApiResponse(description="Not found."),
        502: OpenApiResponse(ErrorResponseSerializer, description="The LLM request itself failed."),
        503: OpenApiResponse(ErrorResponseSerializer, description="No LLM is configured."),
    },
    tags=["director"],
)
@api_view(["POST"])
def plan_project(request, project_id: int):
    project = _get_project(request, project_id)
    if not settings.LLM_ENABLED:
        return Response({"error": "No LLM is configured."}, status=503)

    idea_text = request.data.get("idea_text", "")
    if not idea_text.strip():
        return Response({"error": "idea_text is required."}, status=400)

    require_reference_mode = services.project_requires_reference_mode(project)
    # token_label (not label) -- the LLM needs the literal token it must
    # write verbatim; a human label like "Alice -- character sheet" is
    # appended only as parenthetical context, see llm.plan_scenes()'s
    # require_reference_mode handling and DIRECTOR_PLAN_GUIDE_en.md's
    # section 3.5.
    resource_labels = [
        f"{r.token_label} ({r.label.strip()})" if r.label.strip() else r.token_label
        for r in project.resources.all()
    ]
    try:
        raw_scenes = llm.plan_scenes(
            idea_text,
            resource_labels=resource_labels,
            extra_context=project.overarching_prompt,
            require_reference_mode=require_reference_mode,
        )
    except llm.LLMError as exc:
        return Response({"error": str(exc)}, status=502)

    scenes = services.normalize_planned_scenes(raw_scenes, require_reference_mode=require_reference_mode)
    if not scenes:
        return Response({"error": "The AI didn't return any usable scenes -- try rephrasing."}, status=502)
    return Response({"scenes": scenes})


@extend_schema(
    summary="Suggest character/object/voice reference assets worth generating from a script/idea",
    description="Reads a script/idea and proposes recurring subjects (characters, objects, "
    "voices) that would benefit from a fixed reference image/audio sample, each with a "
    "ready-to-use generation prompt -- preview only, nothing is generated or created yet. "
    "Meant to run before plan/: attaching the resulting references first lets plan/ write every "
    "scene as a reference clip that draws on them, instead of re-describing the same subject "
    "from scratch (and inconsistently) in every scene's text.",
    request=PlanRequestSerializer,
    responses={
        200: ExtractReferencesResponseSerializer,
        400: ErrorResponseSerializer,
        404: OpenApiResponse(description="Not found."),
        502: OpenApiResponse(ErrorResponseSerializer, description="The LLM request itself failed."),
        503: OpenApiResponse(ErrorResponseSerializer, description="No LLM is configured."),
    },
    tags=["director"],
)
@api_view(["POST"])
def extract_references(request, project_id: int):
    project = _get_project(request, project_id)
    if not settings.LLM_ENABLED:
        return Response({"error": "No LLM is configured."}, status=503)

    idea_text = request.data.get("idea_text", "")
    if not idea_text.strip():
        return Response({"error": "idea_text is required."}, status=400)

    try:
        raw_candidates = llm.extract_reference_subjects(idea_text, extra_context=project.overarching_prompt)
    except llm.LLMError as exc:
        return Response({"error": str(exc)}, status=502)

    candidates = services.normalize_reference_candidates(raw_candidates)
    return Response({"candidates": candidates})


@extend_schema(
    summary="Apply a (possibly user-edited) planned scene list as real clips",
    description="Appends after the project's existing clips by default; pass replace=true to "
    "delete all existing clips first. Doesn't itself trigger any render -- use render_all/ "
    "afterward.",
    request=ApplyPlanRequestSerializer,
    responses={
        201: ClipSerializer(many=True),
        400: ErrorResponseSerializer,
        404: OpenApiResponse(description="Not found."),
    },
    tags=["director"],
)
@api_view(["POST"])
def apply_plan(request, project_id: int):
    project = _get_project(request, project_id)
    scenes = request.data.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return Response({"error": "scenes (non-empty array) is required."}, status=400)
    replace = bool(request.data.get("replace", False))
    idea_text = request.data.get("idea_text", "")

    try:
        services.apply_planned_scenes(project, scenes, replace=replace)
    except services.PlanError as exc:
        return Response({"error": str(exc)}, status=400)

    if idea_text.strip():
        project.script_text = idea_text
        project.save(update_fields=["script_text"])

    return Response([_serialize_clip(c) for c in project.clips.select_related("current_job")], status=201)


@extend_schema(
    summary="Assemble every clip into one downloadable video, in order",
    description="Concatenates every clip's rendered video, in board order, into one MP4 (see "
    "integrations/assembly.py) and stores it as the project's assembled_video_file, replacing "
    "any previous export. Requires every clip to have a rendered video and none to be dirty -- "
    "render everything first.",
    responses={
        200: ProjectDetailSerializer,
        400: ErrorResponseSerializer,
        404: OpenApiResponse(description="Not found."),
        409: OpenApiResponse(ErrorResponseSerializer, description="Some clip isn't rendered/clean yet."),
        502: OpenApiResponse(ErrorResponseSerializer, description="ffmpeg failed to assemble the clips."),
    },
    tags=["director"],
)
@api_view(["POST"])
def assemble_project(request, project_id: int):
    project = _get_project(request, project_id)
    clips = list(project.clips.select_related("current_job").order_by("order"))
    if not clips:
        return Response({"error": "This project has no clips yet."}, status=400)

    not_rendered = [c for c in clips if not (c.current_job and c.current_job.video_file)]
    if not_rendered:
        return Response({"error": f"{len(not_rendered)} clip(s) haven't rendered a video yet."}, status=409)
    dirty = [c for c in clips if c.needs_render]
    if dirty:
        return Response(
            {"error": f"{len(dirty)} clip(s) need re-render before exporting -- render everything first."},
            status=409,
        )

    with tempfile.TemporaryDirectory() as tmp:
        local_paths = []
        for index, clip in enumerate(clips):
            video_file = clip.current_job.video_file
            suffix = Path(video_file.name).suffix or ".mp4"
            local_path = Path(tmp) / f"clip_{index}{suffix}"
            video_file.open("rb")
            try:
                local_path.write_bytes(video_file.read())
            finally:
                video_file.close()
            local_paths.append(local_path)

        try:
            assembled_bytes = assembly.concat_videos(local_paths)
        except assembly.AssemblyError as exc:
            return Response({"error": str(exc)}, status=502)

    if project.assembled_video_file:
        project.assembled_video_file.delete(save=False)
    project.assembled_video_file.save(
        f"director_project_{project.id}_assembled.mp4", ContentFile(assembled_bytes), save=True
    )
    return Response(_serialize_project(project, detail=True))
