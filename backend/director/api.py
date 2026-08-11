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
from .models import CONTINUATION_CAPABLE_MODES, Clip, ClipReferenceAsset, Project, ProjectResource


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


class ProjectDetailSerializer(ProjectSerializer):
    resources = ProjectResourceSerializer(many=True)
    clips = ClipSerializer(many=True)
    assembled_video_url = serializers.CharField(allow_null=True)


class PlannedSceneSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=Mode.choices)
    continues_previous = serializers.BooleanField()
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
    }
    if detail:
        data["resources"] = [_serialize_resource(r) for r in project.resources.all()]
        data["clips"] = [_serialize_clip(c) for c in project.clips.select_related("current_job").all()]
        data["assembled_video_url"] = project.assembled_video_file.url if project.assembled_video_file else None
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
        return Response([_serialize_project(p) for p in Project.objects.filter(user=request.user)])

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
    responses={204: OpenApiResponse(description="Deleted."), 404: OpenApiResponse(description="Not found.")},
    tags=["director"],
)
@api_view(["GET", "PATCH", "DELETE"])
def project_detail(request, project_id: int):
    project = _get_project(request, project_id)

    if request.method == "DELETE":
        project.delete()
        return Response(status=204)

    if request.method == "PATCH":
        dirty = False
        resolution_changed = False
        if "title" in request.data:
            project.title = request.data["title"]
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
    file = request.FILES.get("file")
    if file is None:
        return Response({"error": "file is required."}, status=400)
    label = request.data.get("label", "")
    order = project.resources.filter(kind=kind).count()
    resource = ProjectResource.objects.create(project=project, kind=kind, order=order, file=file, label=label)
    services.mark_project_dirty(project)
    return Response(_serialize_resource(resource), status=201)


@extend_schema(
    summary="Delete a project resource",
    responses={204: OpenApiResponse(description="Deleted."), 404: OpenApiResponse(description="Not found.")},
    tags=["director"],
)
@api_view(["DELETE"])
def resource_detail(request, resource_id: int):
    resource = get_object_or_404(ProjectResource, id=resource_id, project__user=request.user)
    project = resource.project
    resource.file.delete(save=False)
    resource.delete()
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

    duration = RenderDuration.objects.filter(
        id=request.data.get("duration_id"), preset=preset, is_active=True
    ).first()
    if duration is None:
        return Response(
            {"error": "duration_id must reference an active duration option for this project's quality tier."},
            status=400,
        )

    if continues_previous:
        predecessor = project.clips.order_by("-order").first()
        if predecessor is None:
            return Response({"error": "continues_previous requires an existing predecessor clip."}, status=400)
        return preset, duration, predecessor.width, predecessor.height

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
    for files, limits, label in (
        (reference_images, _MAX_REFERENCE_IMAGES, "image"),
        (reference_audio, _MAX_REFERENCE_AUDIO, "audio"),
        (reference_video, _MAX_REFERENCE_VIDEO, "video"),
    ):
        if len(files) > limits[mode]:
            return Response({"error": f"{Mode(mode).label} supports at most {limits[mode]} {label} reference(s)."}, status=400)

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
            # width/height to/from the immediate predecessor's own values.
            clip.width, clip.height = services.resolve_clip_width_height(clip)

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
    existing_count = clip.references.filter(kind=kind).count()
    if existing_count >= max_for_mode:
        return Response(
            {"error": f"{Mode(clip.mode).label} supports at most {max_for_mode} {kind} reference(s)."}, status=400
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
    try:
        services.render_clip(clip)
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
    job = clip.current_job
    if job is None or job.status not in (GenerationJob.Status.QUEUED, GenerationJob.Status.PROCESSING):
        return Response({"error": "This clip isn't queued or processing -- nothing to cancel."}, status=409)

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

    clip.refresh_from_db()
    return Response(_serialize_clip(clip))


@extend_schema(
    summary="Generate a proposed clip sequence from a script/idea (preview only, not saved)",
    description="Turns a pasted script/idea into an ordered list of proposed scenes via the "
    "configured LLM -- nothing is created yet. Review/edit the result client-side, then POST it "
    "to plan/apply/ to actually create clips from it.",
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
    if services.project_requires_reference_mode(project):
        return Response(
            {
                "error": "This project has shared references -- \"Generate from script\" doesn't "
                "support reference clips yet. Add clips manually instead."
            },
            status=400,
        )

    idea_text = request.data.get("idea_text", "")
    if not idea_text.strip():
        return Response({"error": "idea_text is required."}, status=400)

    resource_labels = [r.label or r.token_label for r in project.resources.all()]
    try:
        raw_scenes = llm.plan_scenes(
            idea_text, resource_labels=resource_labels, extra_context=project.overarching_prompt
        )
    except llm.LLMError as exc:
        return Response({"error": str(exc)}, status=502)

    scenes = services.normalize_planned_scenes(raw_scenes)
    if not scenes:
        return Response({"error": "The AI didn't return any usable scenes -- try rephrasing."}, status=502)
    return Response({"scenes": scenes})


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

    try:
        services.apply_planned_scenes(project, scenes, replace=replace)
    except services.PlanError as exc:
        return Response({"error": str(exc)}, status=400)

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
