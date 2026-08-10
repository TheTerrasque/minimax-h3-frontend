"""DRF views for Director Mode. Same minimal-validation style as
generation/api.py (plain dict/request.FILES checks, *_serializer classes
for drf-spectacular docs only, not real (de)serialization) -- see that
module's own docstring for the reasoning, followed here for consistency.
"""

from __future__ import annotations

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.response import Response

from generation.api import _MAX_REFERENCE_AUDIO, _MAX_REFERENCE_IMAGES, _MAX_REFERENCE_VIDEO
from generation.models import GenerationJob, Mode, ReferenceAsset, RenderDuration
from generation.resolution import ASPECT_RATIO_VALUES, compute_resolution, is_valid_aspect_ratio
from integrations import comfyui

from . import services
from .models import CONTINUATION_CAPABLE_MODES, Clip, ClipReferenceAsset, Project, ProjectResource


class ErrorResponseSerializer(serializers.Serializer):
    error = serializers.CharField()


class ProjectResourceSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kind = serializers.ChoiceField(choices=ProjectResource.Kind.choices)
    order = serializers.IntegerField()
    label = serializers.CharField(help_text="Human label if set, else the <Picture N>-style token.")
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
    aspect_ratio = serializers.CharField()
    width = serializers.IntegerField()
    height = serializers.IntegerField()
    needs_render = serializers.BooleanField(help_text="The red-border dirty flag.")
    current_job_id = serializers.IntegerField(allow_null=True)
    current_job_status = serializers.CharField(allow_null=True)
    video_url = serializers.CharField(allow_null=True)
    thumbnail_url = serializers.CharField(allow_null=True)
    error_message = serializers.CharField(allow_null=True)
    references = ClipReferenceSerializer(many=True)


class ProjectSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    overarching_prompt = serializers.CharField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class ProjectDetailSerializer(ProjectSerializer):
    resources = ProjectResourceSerializer(many=True)
    clips = ClipSerializer(many=True)


def _serialize_resource(resource: ProjectResource) -> dict:
    return {
        "id": resource.id,
        "kind": resource.kind,
        "order": resource.order,
        "label": resource.label or resource.token_label,
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
        "aspect_ratio": clip.aspect_ratio,
        "width": clip.width,
        "height": clip.height,
        "needs_render": clip.needs_render,
        "current_job_id": job.id if job else None,
        "current_job_status": job.status if job else None,
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
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }
    if detail:
        data["resources"] = [_serialize_resource(r) for r in project.resources.all()]
        data["clips"] = [_serialize_clip(c) for c in project.clips.select_related("current_job").all()]
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
    project = Project.objects.create(user=request.user, title=title, overarching_prompt=overarching_prompt)
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
    "Clip's render depends on it.",
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
        if "title" in request.data:
            project.title = request.data["title"]
        if "overarching_prompt" in request.data:
            project.overarching_prompt = request.data["overarching_prompt"]
            dirty = True
        project.save()
        if dirty:
            services.mark_project_dirty(project)
        return Response(_serialize_project(project, detail=True))

    return Response(_serialize_project(project, detail=True))


@extend_schema(
    methods=["GET"], summary="List a project's resources", responses=ProjectResourceSerializer(many=True), tags=["director"]
)
@extend_schema(
    methods=["POST"],
    summary="Add a project resource (character sheet / voice / world reference)",
    description="Marks every Clip in the project dirty -- see project_detail's PATCH.",
    responses={201: ProjectResourceSerializer},
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
    aspect_ratio = serializers.CharField(
        required=False,
        help_text="Ignored (inherited from the predecessor) when continues_previous is set.",
    )
    continues_previous = serializers.BooleanField(required=False, default=False)
    prompt = serializers.CharField(required=False, allow_blank=True)
    reference_images = serializers.ListField(child=serializers.FileField(), required=False)
    reference_audio = serializers.ListField(child=serializers.FileField(), required=False)
    reference_video = serializers.ListField(child=serializers.FileField(), required=False)


def _resolve_clip_duration_and_resolution(request, project: Project, mode: str, continues_previous: bool):
    """Returns (duration, aspect_ratio, width, height) or a Response to
    return directly on validation failure -- same "value-or-Response"
    pattern generation/api.py's _validate_mode() uses."""
    duration = (
        RenderDuration.objects.filter(
            id=request.data.get("duration_id"), preset__mode=mode, is_active=True, preset__is_active=True
        )
        .select_related("preset")
        .first()
    )
    if duration is None:
        return Response(
            {"error": "duration_id must reference an active duration option for this mode."}, status=400
        )

    if continues_previous:
        predecessor = project.clips.order_by("-order").first()
        if predecessor is None:
            return Response({"error": "continues_previous requires an existing predecessor clip."}, status=400)
        return duration, predecessor.aspect_ratio, predecessor.width, predecessor.height

    aspect_ratio = request.data.get("aspect_ratio")
    if not is_valid_aspect_ratio(aspect_ratio):
        return Response({"error": f"aspect_ratio must be one of {ASPECT_RATIO_VALUES}, or a custom W:H ratio."}, status=400)
    width, height = compute_resolution(duration.preset.megapixels, aspect_ratio)
    return duration, aspect_ratio, width, height


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

    continues_previous = str(request.data.get("continues_previous", "")).lower() in ("1", "true", "yes", "on")
    if continues_previous and mode not in CONTINUATION_CAPABLE_MODES:
        return Response(
            {"error": f"continues_previous is only supported for modes {sorted(CONTINUATION_CAPABLE_MODES)}."},
            status=400,
        )

    resolved = _resolve_clip_duration_and_resolution(request, project, mode, continues_previous)
    if isinstance(resolved, Response):
        return resolved
    duration, aspect_ratio, width, height = resolved

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
            preset=duration.preset,
            duration=duration,
            aspect_ratio=aspect_ratio,
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
    "directly-chained continuations (see director/services.py's mark_dirty_cascade).",
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
                setattr(clip, field, value)
                changed = True

        if "duration_id" in request.data or "aspect_ratio" in request.data:
            duration = clip.duration
            if "duration_id" in request.data:
                duration = (
                    RenderDuration.objects.filter(
                        id=request.data.get("duration_id"),
                        preset__mode=clip.mode,
                        is_active=True,
                        preset__is_active=True,
                    )
                    .select_related("preset")
                    .first()
                )
                if duration is None:
                    return Response(
                        {"error": "duration_id must reference an active duration option for this clip's mode."},
                        status=400,
                    )

            if clip.continues_previous:
                # width/height/aspect_ratio stay exactly as inherited at
                # creation -- MiniMaxH3ChainPlan's width/height apply to
                # every scene in a run (see extras.md#contex-loop), so
                # only duration/preset (length/steps) may change here, not
                # resolution.
                if "aspect_ratio" in request.data and request.data["aspect_ratio"] != clip.aspect_ratio:
                    return Response(
                        {"error": "aspect_ratio/resolution is locked to the predecessor while continues_previous is set."},
                        status=400,
                    )
                clip.preset = duration.preset
                clip.duration = duration
            else:
                aspect_ratio = request.data.get("aspect_ratio", clip.aspect_ratio)
                if not is_valid_aspect_ratio(aspect_ratio):
                    return Response(
                        {"error": f"aspect_ratio must be one of {ASPECT_RATIO_VALUES}, or a custom W:H ratio."},
                        status=400,
                    )
                width, height = compute_resolution(duration.preset.megapixels, aspect_ratio)
                clip.preset = duration.preset
                clip.duration = duration
                clip.aspect_ratio = aspect_ratio
                clip.width = width
                clip.height = height
            changed = True

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
