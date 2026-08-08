"""DRF views for the generation app.

Health check, config flags, the LLM prompt-assist endpoints (one-shot refine
+ interactive chat), presets, queue ETA, and job create/list/detail all live
here. Validation is deliberately lightweight (plain dict/request.FILES
checks, no DRF Serializer classes doing real (de)serialization) -- matches
this project's existing minimal-scope style rather than a full ModelSerializer
layer; reference-image uploads ride the same POST /api/jobs/ call as job
creation (see jobs()) rather than a separate /api/jobs/{id}/references/ step,
since the frontend stages reference files client-side before submitting.

Every view carries an @extend_schema so the auto-generated OpenAPI docs
(config/urls.py: /api/schema/swagger-ui/) actually describe request/response
bodies -- drf-spectacular can't infer those from a plain @api_view function
that reads request.data by hand, so the *_serializer classes below exist
purely for documentation and aren't used for real validation.
"""

import json
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django_q.tasks import async_task
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from integrations import llm

from .models import (
    CONTENT_TYPE_BY_MODE,
    ContentType,
    GenerationJob,
    Mode,
    PromptChatMessage,
    PromptChatSession,
    ReferenceAsset,
    RenderDuration,
    RenderPreset,
)
from .queue import estimated_seconds_ahead, expected_finish_times
from .resolution import (
    ASPECT_RATIO_VALUES,
    ASPECT_RATIOS,
    DEFAULT_ASPECT_RATIO,
    compute_resolution,
    is_valid_aspect_ratio,
)


class HealthResponseSerializer(serializers.Serializer):
    status = serializers.CharField()


class AspectRatioSerializer(serializers.Serializer):
    value = serializers.CharField(help_text='e.g. "16:9" -- pass this as GenerationJob.aspect_ratio.')
    label = serializers.CharField(help_text='e.g. "16:9 (Widescreen)".')


class ConfigResponseSerializer(serializers.Serializer):
    llm_enabled = serializers.BooleanField(
        help_text="False when no LLM is configured -- hide all AI UI (refine button, chat) when so."
    )
    llm_vision_enabled = serializers.BooleanField(
        help_text="Whether chat actually forwards reference images to the LLM as vision content "
        "(settings.LLM_VISION_ENABLED) -- worth checking before uploading them on every chat turn."
    )
    oidc_enabled = serializers.BooleanField(
        help_text="False when no OIDC provider is configured -- e.g. early dev, or an install "
        "that only ever uses createsuperuser-created accounts."
    )
    oidc_login_url = serializers.CharField(
        allow_null=True, help_text="Absolute-path URL to redirect the browser to for OIDC login."
    )
    oidc_provider_name = serializers.CharField(help_text="Human-readable label for the login button.")
    aspect_ratios = AspectRatioSerializer(
        many=True,
        help_text="Fixed set of selectable aspect ratios (doesn't affect render time, unlike "
        "megapixels -- see RenderPreset -- so it isn't part of the preset/duration catalog).",
    )
    default_aspect_ratio = serializers.CharField(help_text="Value to preselect -- see aspect_ratios.")
    spectrum_level = serializers.IntegerField(
        allow_null=True,
        help_text="settings.SPECTRUM_LEVEL: null (not offered), 0 (optional, default off), "
        "1 (optional, default on), or 2 (forced -- every job uses it, no toggle to show). "
        "See extras.md#spectrum.",
    )


class ErrorResponseSerializer(serializers.Serializer):
    error = serializers.CharField()


class RefinePromptRequestSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=Mode.choices)
    raw_prompt = serializers.CharField()
    reference_labels = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text=(
            'Labels of reference assets staged for this prompt (e.g. ["Picture 1", "Picture 2"]) '
            "-- there's no GenerationJob yet at refine time, so the frontend passes whatever it's "
            "currently staging."
        ),
    )
    duration_seconds = serializers.FloatField(
        required=False,
        help_text="The currently-selected clip length, if any -- so the LLM keeps shot-cut "
        "timestamps within the actual video duration instead of guessing.",
    )
    reference_images = serializers.ListField(
        child=serializers.FileField(),
        required=False,
        help_text="Currently-staged reference images (e.g. i2v's first/last frame). Only "
        "actually sent to the LLM (as vision content) when settings.LLM_VISION_ENABLED; "
        "otherwise ignored.",
    )


class RefinePromptResponseSerializer(serializers.Serializer):
    improved_prompt = serializers.CharField()


class ChatMessageSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=PromptChatMessage.Role.choices)
    content = serializers.CharField()


class ChatRequestSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=Mode.choices)
    history = serializers.CharField(
        help_text='JSON-encoded array of {"role": "user"|"assistant", "content": str} -- the '
        "full prior conversation. Stateless: nothing is persisted server-side until this "
        "chat's transcript is actually attached to a queued job (see POST /api/jobs/'s "
        "chat_transcript field)."
    )
    content = serializers.CharField(help_text="The new user message.")
    raw_prompt = serializers.CharField(
        required=False, allow_blank=True,
        help_text="The user's current draft in the main prompt box, if any -- given to the "
        "LLM as context even before they've sent it as a chat message.",
    )
    improved_prompt = serializers.CharField(
        required=False, allow_blank=True,
        help_text="The currently-active AI-refined prompt, if any -- given to the LLM as "
        "separate, clearly-labeled context distinct from raw_prompt so it knows what the "
        "user is actually looking at right now.",
    )
    duration_seconds = serializers.FloatField(
        required=False,
        help_text="The currently-selected clip length, if any -- so the LLM keeps shot-cut "
        "timestamps within the actual video duration instead of guessing.",
    )
    reference_labels = serializers.ListField(child=serializers.CharField(), required=False)
    reference_images = serializers.ListField(
        child=serializers.FileField(),
        required=False,
        help_text="Currently-staged reference images. Only actually sent to the LLM (as vision "
        "content) when settings.LLM_VISION_ENABLED; otherwise ignored.",
    )


class RenderDurationSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="Pass this as CreateJobRequest.duration_id.")
    duration_seconds = serializers.FloatField()
    estimated_render_seconds = serializers.IntegerField()


class RenderPresetSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    mode = serializers.ChoiceField(choices=Mode.choices)
    label = serializers.CharField()
    megapixels = serializers.FloatField(help_text="Determines render time, along with duration.")
    steps = serializers.IntegerField()
    is_draft = serializers.BooleanField()
    durations = RenderDurationSerializer(
        many=True, help_text="Selectable clip lengths for this preset, each independently benchmarked."
    )


class QueueEstimateResponseSerializer(serializers.Serializer):
    seconds_ahead = serializers.IntegerField(
        help_text="Sum of estimated_seconds over every job currently queued/running, system-wide."
    )
    additional_seconds = serializers.IntegerField(
        help_text="The given duration option's own estimated render time."
    )
    total_seconds = serializers.IntegerField(help_text="seconds_ahead + additional_seconds.")
    estimated_finish_time = serializers.DateTimeField()


class ReferenceAssetSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kind = serializers.ChoiceField(choices=ReferenceAsset.Kind.choices)
    order = serializers.IntegerField()
    label = serializers.CharField(help_text='e.g. "Picture 1" -- the <Picture N> token usable in a prompt.')
    url = serializers.CharField(allow_null=True)


class CreateJobRequestSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=Mode.choices)
    duration_id = serializers.IntegerField(
        help_text="A RenderDuration id (see GET /api/presets/'s nested durations) -- this alone "
        "determines the preset (megapixels/steps) and clip length; aspect_ratio is separate."
    )
    aspect_ratio = serializers.CharField(
        help_text="One of GET /api/config/'s aspect_ratios values, or a custom \"W:H\" ratio "
        "(e.g. to match an uploaded first frame -- see is_valid_aspect_ratio()). Doesn't "
        "affect render time.",
    )
    raw_prompt = serializers.CharField()
    improved_prompt = serializers.CharField(required=False, allow_blank=True)
    reference_images = serializers.ListField(
        child=serializers.FileField(),
        required=False,
        help_text=(
            "Image reference files, order matters: i2v takes 0-2 (first = first frame, "
            "second = last frame); r2v takes 0-9, each becoming a <Picture N> token. "
            "t2v takes none. Video references aren't wired up yet."
        ),
    )
    reference_audio = serializers.ListField(
        child=serializers.FileField(),
        required=False,
        help_text="Standalone reference audio clips, r2v only, 0-3, each becoming an <Audio N> token.",
    )
    chat_transcript = serializers.CharField(
        required=False, allow_blank=True,
        help_text='JSON-encoded array of {"role", "content"} chat messages, if the user drafted '
        "this prompt via the chat feature -- persisted as a PromptChatSession/PromptChatMessage "
        "rows linked to this job (see generation/models.py) only now, at job-creation time. Chat "
        "itself (POST /api/prompt/chat/) never writes to the DB.",
    )


class GenerationJobSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    mode = serializers.ChoiceField(choices=Mode.choices)
    content_type = serializers.ChoiceField(
        choices=ContentType.choices,
        help_text="Derived from mode (see models.CONTENT_TYPE_BY_MODE) -- which of "
        "video_url's actual content (video/image/audio) the frontend should render "
        "it as; despite the field name, it's not always a video.",
    )
    status = serializers.ChoiceField(choices=GenerationJob.Status.choices)
    raw_prompt = serializers.CharField(
        help_text="Included at list level (not just detail) so the frontend can show a "
        "title without a second request per job."
    )
    title = serializers.CharField(
        allow_blank=True,
        help_text="User-editable label (see PATCH below) -- blank means the frontend should "
        "fall back to raw_prompt, see frontend/src/features/queue/jobTitle.ts.",
    )
    preset_id = serializers.IntegerField()
    duration_id = serializers.IntegerField()
    megapixels = serializers.FloatField()
    aspect_ratio = serializers.CharField()
    width = serializers.IntegerField()
    height = serializers.IntegerField()
    duration_seconds = serializers.FloatField()
    estimated_seconds = serializers.IntegerField()
    use_spectrum = serializers.BooleanField(
        help_text="Whether this job used the Spectrum accelerator -- see extras.md#spectrum. "
        "estimated_seconds above does NOT account for it."
    )
    video_url = serializers.CharField(allow_null=True)
    thumbnail_url = serializers.CharField(
        allow_null=True,
        help_text="Small poster image for video-content-type jobs (see media_post.extract_thumbnail) "
        "-- null for image/audio jobs (video_url itself already works as a thumbnail for those) "
        "and for jobs rendered before this field existed. Prefer this over video_url for list-view "
        "thumbnails -- it's a static image, not a <video> element per row.",
    )
    created_at = serializers.DateTimeField()
    started_at = serializers.DateTimeField(allow_null=True)
    finished_at = serializers.DateTimeField(allow_null=True)
    expected_finish_time = serializers.DateTimeField(
        allow_null=True,
        help_text="Only set while queued/processing -- computed by walking the FIFO queue "
        "(see generation/queue.py::expected_finish_times()); null once done.",
    )
    phase = serializers.ChoiceField(
        choices=GenerationJob.Phase.choices,
        allow_blank=True,
        help_text="Sub-state while status=processing (preparing/rendering/finishing), blank "
        "otherwise -- see integrations/comfyui.py's stream_execution_progress().",
    )
    progress_current = serializers.IntegerField(
        allow_null=True, help_text="Sampler step reached so far -- only set during phase=rendering."
    )
    progress_total = serializers.IntegerField(
        allow_null=True, help_text="Total sampler steps for this job -- only set during phase=rendering."
    )


class GenerationJobDetailSerializer(GenerationJobSerializer):
    improved_prompt = serializers.CharField()
    error_message = serializers.CharField()
    references = ReferenceAssetSerializer(many=True)


class UpdateJobTitleRequestSerializer(serializers.Serializer):
    title = serializers.CharField(
        allow_blank=True, max_length=200,
        help_text="Blank clears it (the frontend then falls back to showing raw_prompt).",
    )


@extend_schema(
    summary="Health check",
    responses=HealthResponseSerializer,
    tags=["meta"],
)
@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    return Response({"status": "ok"})


@extend_schema(
    summary="Frontend feature flags",
    description=(
        "Per features.md item 11: when no LLM is configured, none of the AI UI "
        "(refine button, chat) should be shown at all. The frontend is meant to check "
        "this once at boot."
    ),
    responses=ConfigResponseSerializer,
    tags=["meta"],
)
@api_view(["GET"])
@permission_classes([AllowAny])
def config(request):
    oidc_apps = settings.SOCIALACCOUNT_PROVIDERS.get("openid_connect", {}).get("APPS", [])
    oidc_enabled = bool(oidc_apps)
    return Response(
        {
            "llm_enabled": settings.LLM_ENABLED,
            "llm_vision_enabled": settings.LLM_VISION_ENABLED,
            "oidc_enabled": oidc_enabled,
            "oidc_login_url": (
                reverse("openid_connect_login", kwargs={"provider_id": settings.OIDC_PROVIDER_ID})
                if oidc_enabled
                else None
            ),
            "oidc_provider_name": oidc_apps[0]["name"] if oidc_apps else "OIDC",
            "aspect_ratios": [{"value": value, "label": label} for value, label in ASPECT_RATIOS],
            "default_aspect_ratio": DEFAULT_ASPECT_RATIO,
            "spectrum_level": settings.SPECTRUM_LEVEL,
        }
    )


def _validate_mode(data) -> str | Response:
    mode = data.get("mode")
    if mode not in Mode.values:
        return Response({"error": f"mode must be one of {Mode.values}"}, status=400)
    return mode


def _resolve_use_spectrum(requested: bool | None) -> bool:
    """Resolves GenerationJob.use_spectrum from settings.SPECTRUM_LEVEL plus
    what the client asked for -- the level's meaning is enforced here, not
    trusted from the client (see extras.md#spectrum for what each level
    means). `requested` must be None when the client didn't send
    use_spectrum at all, not just falsy -- a level-1 (default-on) toggle the
    user actually unchecked has to stay off, and that's only distinguishable
    from "field omitted" if the frontend always sends an explicit
    true/false rather than only including it when checked.
    """
    level = settings.SPECTRUM_LEVEL
    if level is None:
        return False
    if level == 2:
        return True
    if requested is None:
        return level == 1
    return requested


@extend_schema(
    summary="AI-refine a prompt (one-shot)",
    description=(
        'Backs the "AI refine" button: rewrites raw_prompt into the house prompt-writing '
        "guide's expected structure for the given mode. 503 if no LLM is configured."
    ),
    request=RefinePromptRequestSerializer,
    responses={
        200: RefinePromptResponseSerializer,
        400: ErrorResponseSerializer,
        502: OpenApiResponse(ErrorResponseSerializer, description="The LLM request itself failed."),
        503: OpenApiResponse(ErrorResponseSerializer, description="No LLM is configured."),
    },
    tags=["prompt-assist"],
)
@api_view(["POST"])
def refine_prompt(request):
    if not settings.LLM_ENABLED:
        return Response({"error": "No LLM is configured."}, status=503)

    mode = _validate_mode(request.data)
    if isinstance(mode, Response):
        return mode
    raw_prompt = request.data.get("raw_prompt", "")
    if not raw_prompt.strip():
        return Response({"error": "raw_prompt is required."}, status=400)
    reference_labels = request.data.getlist("reference_labels") or None
    duration_seconds_raw = request.data.get("duration_seconds")
    try:
        duration_seconds = float(duration_seconds_raw) if duration_seconds_raw else None
    except (TypeError, ValueError):
        duration_seconds = None

    reference_images = None
    if settings.LLM_VISION_ENABLED:
        reference_images = [
            (f.read(), f.content_type or "application/octet-stream")
            for f in request.FILES.getlist("reference_images")
        ] or None

    try:
        improved_prompt = llm.improve_prompt(
            mode,
            raw_prompt,
            reference_labels,
            duration_seconds=duration_seconds,
            reference_images=reference_images,
        )
    except llm.LLMError as exc:
        return Response({"error": str(exc)}, status=502)
    return Response({"improved_prompt": improved_prompt})


@extend_schema(
    summary="Get a chat reply (stateless)",
    description=(
        "No server-side persistence during the live conversation -- the caller (the frontend) "
        "keeps the full transcript in memory and resends it (`history`) with every call; nothing "
        "is written to the DB here at all. A PromptChatSession/PromptChatMessage trail only gets "
        "created if/when this chat's transcript is actually attached to a queued job, see "
        "POST /api/jobs/'s chat_transcript field. 503 if no LLM is configured."
    ),
    request=ChatRequestSerializer,
    responses={
        200: ChatMessageSerializer,
        400: ErrorResponseSerializer,
        502: OpenApiResponse(ErrorResponseSerializer, description="The LLM request itself failed."),
        503: OpenApiResponse(ErrorResponseSerializer, description="No LLM is configured."),
    },
    tags=["prompt-assist"],
)
@api_view(["POST"])
def chat_message(request):
    if not settings.LLM_ENABLED:
        return Response({"error": "No LLM is configured."}, status=503)

    mode = _validate_mode(request.data)
    if isinstance(mode, Response):
        return mode

    content = request.data.get("content", "")
    if not content.strip():
        return Response({"error": "content is required."}, status=400)

    try:
        history = json.loads(request.data.get("history") or "[]")
    except (TypeError, ValueError):
        return Response({"error": "history must be JSON-encoded."}, status=400)
    if not isinstance(history, list):
        return Response({"error": "history must be a JSON array."}, status=400)

    raw_prompt = request.data.get("raw_prompt", "")
    improved_prompt = request.data.get("improved_prompt", "")
    reference_labels = request.data.getlist("reference_labels") or None
    duration_seconds_raw = request.data.get("duration_seconds")
    try:
        duration_seconds = float(duration_seconds_raw) if duration_seconds_raw else None
    except (TypeError, ValueError):
        duration_seconds = None

    reference_images = None
    if settings.LLM_VISION_ENABLED:
        reference_images = [
            (f.read(), f.content_type or "application/octet-stream")
            for f in request.FILES.getlist("reference_images")
        ] or None

    full_history = [*history, {"role": PromptChatMessage.Role.USER, "content": content}]

    try:
        reply = llm.chat_reply(
            mode,
            full_history,
            reference_labels,
            raw_prompt=raw_prompt,
            reference_images=reference_images,
            improved_prompt=improved_prompt,
            duration_seconds=duration_seconds,
        )
    except llm.LLMError as exc:
        return Response({"error": str(exc)}, status=502)

    return Response({"role": PromptChatMessage.Role.ASSISTANT, "content": reply})


# Max image references a job of this mode can carry -- matches what
# generation/tasks.py's build_api_workflow() actually wires up today: t2v
# takes none, i2v takes first/last frame (2), r2v takes up to 9 (per live
# /object_info's ref_images autogrow max, see ARCHITECTURE.md). Video
# references aren't wired into tasks.py yet, so they're rejected here too
# rather than accepted and silently ignored at render time. t2i/t2a share
# t2v's underlying workflow (text flow, no references at all); r2i/r2a
# share r2v's (reference flow, same limits).
_MAX_REFERENCE_IMAGES = {
    Mode.TEXT_TO_VIDEO: 0,
    Mode.IMAGE_TO_VIDEO: 2,
    Mode.REFERENCE_TO_VIDEO: 9,
    Mode.TEXT_TO_IMAGE: 0,
    Mode.REFERENCE_TO_IMAGE: 9,
    Mode.TEXT_TO_AUDIO: 0,
    Mode.REFERENCE_TO_AUDIO: 9,
}

# Max standalone reference audio clips -- only MiniMaxH3ReferenceToVideo has
# a ref_audios input at all (see live /object_info: COMFY_AUTOGROW_V3,
# prefix "ref_audio_", max 3), so t2v/i2v get 0. r2i (image output) also
# gets 0 -- reference audio would render into the underlying video same as
# r2v, but a still frame extracted from it can't carry any of that, so
# offering the upload would just be confusing. r2a keeps the full 3 -- a
# reference clip actually can (and is meant to) shape the generated audio.
_MAX_REFERENCE_AUDIO = {
    Mode.TEXT_TO_VIDEO: 0,
    Mode.IMAGE_TO_VIDEO: 0,
    Mode.REFERENCE_TO_VIDEO: 3,
    Mode.TEXT_TO_IMAGE: 0,
    Mode.REFERENCE_TO_IMAGE: 0,
    Mode.TEXT_TO_AUDIO: 0,
    Mode.REFERENCE_TO_AUDIO: 3,
}


def _serialize_preset(preset: RenderPreset) -> dict:
    return {
        "id": preset.id,
        "mode": preset.mode,
        "label": preset.label,
        "megapixels": preset.megapixels,
        "steps": preset.steps,
        "is_draft": preset.is_draft,
        "durations": [
            {
                "id": d.id,
                "duration_seconds": d.duration_seconds,
                "estimated_render_seconds": d.estimated_render_seconds,
            }
            for d in preset.durations.filter(is_active=True)
        ],
    }


def _serialize_reference(ref: ReferenceAsset) -> dict:
    return {
        "id": ref.id,
        "kind": ref.kind,
        "order": ref.order,
        "label": ref.label,
        "url": ref.file.url if ref.file else None,
    }


def _serialize_job(
    job: GenerationJob, *, detail: bool = False, expected_finish_time=None
) -> dict:
    data = {
        "id": job.id,
        "mode": job.mode,
        "content_type": CONTENT_TYPE_BY_MODE[job.mode],
        "status": job.status,
        "raw_prompt": job.raw_prompt,
        "title": job.title,
        "preset_id": job.preset_id,
        "duration_id": job.duration_id,
        "megapixels": job.megapixels,
        "aspect_ratio": job.aspect_ratio,
        "width": job.width,
        "height": job.height,
        "duration_seconds": job.duration_seconds,
        "estimated_seconds": job.estimated_seconds,
        "use_spectrum": job.use_spectrum,
        "video_url": job.video_file.url if job.video_file else None,
        "thumbnail_url": job.thumbnail_file.url if job.thumbnail_file else None,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "expected_finish_time": expected_finish_time,
        "phase": job.phase,
        "progress_current": job.progress_current,
        "progress_total": job.progress_total,
    }
    if detail:
        data.update(
            {
                "improved_prompt": job.improved_prompt,
                "error_message": job.error_message,
                "references": [_serialize_reference(r) for r in job.references.all()],
            }
        )
    return data


@extend_schema(
    summary="List available render presets",
    description="Backs features.md item 4: resolution/duration/step combos offered per mode, "
    "each with its estimated render time. Optionally filtered to one mode via ?mode=.",
    responses=RenderPresetSerializer(many=True),
    tags=["generation"],
)
@api_view(["GET"])
def list_presets(request):
    mode = request.query_params.get("mode")
    presets = RenderPreset.objects.filter(is_active=True).prefetch_related("durations")
    if mode:
        if mode not in Mode.values:
            return Response({"error": f"mode must be one of {Mode.values}"}, status=400)
        presets = presets.filter(mode=mode)
    return Response([_serialize_preset(p) for p in presets])


@extend_schema(
    summary="Queue ETA, optionally for a candidate job",
    description="Backs features.md item 5. With ?duration_id=, shows the ETA including that "
    "duration option's own render time -- meant to be shown before the user confirms queuing a "
    "job. Without it, shows just the current system-wide backlog (e.g. for a standalone queue "
    "view). seconds_ahead is a system-wide aggregate only (see generation/queue.py) -- never "
    "other users' individual job details.",
    responses={200: QueueEstimateResponseSerializer, 404: OpenApiResponse(description="Not found.")},
    tags=["generation"],
)
@api_view(["GET"])
def queue_estimate(request):
    duration_id = request.query_params.get("duration_id")
    additional_seconds = 0
    if duration_id:
        duration = get_object_or_404(
            RenderDuration, id=duration_id, is_active=True, preset__is_active=True
        )
        additional_seconds = duration.estimated_render_seconds

    seconds_ahead = estimated_seconds_ahead()
    total_seconds = seconds_ahead + additional_seconds
    return Response(
        {
            "seconds_ahead": seconds_ahead,
            "additional_seconds": additional_seconds,
            "total_seconds": total_seconds,
            "estimated_finish_time": timezone.now() + timedelta(seconds=total_seconds),
        }
    )


@extend_schema(
    summary="List or queue generation jobs",
    description=(
        "GET lists only the requesting user's own jobs (see generation/queue.py for the "
        "cross-user aggregate ETA instead). POST creates a GenerationJob (snapshotting the "
        "chosen preset's estimated_render_seconds) plus any attached image/audio references, and "
        "enqueues generation.tasks.process_queue via Django-Q2 -- jobs are worked through "
        "strictly one at a time, FIFO, see that module's docstring. Reference files are "
        "staged client-side (e.g. during AI-refine, see reference_labels on "
        "/api/prompt/refine/) and only actually uploaded here, atomically with job creation."
    ),
    request=CreateJobRequestSerializer,
    responses={
        200: GenerationJobSerializer(many=True),
        201: GenerationJobDetailSerializer,
        400: ErrorResponseSerializer,
    },
    tags=["generation"],
)
@api_view(["GET", "POST"])
def jobs(request):
    if request.method == "GET":
        queryset = (
            GenerationJob.objects.filter(user=request.user)
            .select_related("preset", "duration")
            .prefetch_related("references")
        )
        finish_times = expected_finish_times()
        return Response(
            [_serialize_job(j, expected_finish_time=finish_times.get(j.id)) for j in queryset]
        )

    mode = _validate_mode(request.data)
    if isinstance(mode, Response):
        return mode

    duration = RenderDuration.objects.filter(
        id=request.data.get("duration_id"), preset__mode=mode, is_active=True, preset__is_active=True
    ).select_related("preset").first()
    if duration is None:
        return Response(
            {"error": "duration_id must reference an active duration option for this mode."}, status=400
        )
    preset = duration.preset

    aspect_ratio = request.data.get("aspect_ratio")
    if CONTENT_TYPE_BY_MODE[mode] == ContentType.AUDIO:
        # Audio jobs always render at the minimum 32x32 (see resolution's
        # "1:1" -> RESOLUTION_MULTIPLE floor) -- visual output is discarded
        # entirely (see _postprocess_output()), so an aspect ratio choice
        # would be meaningless; force it server-side rather than trusting
        # the frontend to not send one (it doesn't show the picker for
        # audio modes, but this is the actual boundary).
        aspect_ratio = "1:1"
    if not is_valid_aspect_ratio(aspect_ratio):
        return Response(
            {
                "error": f"aspect_ratio must be one of {ASPECT_RATIO_VALUES}, or a custom "
                '"W:H" ratio (e.g. to match an uploaded first frame).'
            },
            status=400,
        )

    raw_prompt = request.data.get("raw_prompt", "")
    if not raw_prompt.strip():
        return Response({"error": "raw_prompt is required."}, status=400)
    improved_prompt = request.data.get("improved_prompt", "")

    # None (not "false") means "the client didn't send this field at all" --
    # see _resolve_use_spectrum for why that distinction matters.
    use_spectrum_raw = request.data.get("use_spectrum")
    use_spectrum_requested = (
        None if use_spectrum_raw is None else str(use_spectrum_raw).lower() in ("1", "true", "yes", "on")
    )
    use_spectrum = _resolve_use_spectrum(use_spectrum_requested)

    reference_images = request.FILES.getlist("reference_images")
    max_images = _MAX_REFERENCE_IMAGES[mode]
    if len(reference_images) > max_images:
        return Response(
            {"error": f"{Mode(mode).label} supports at most {max_images} image reference(s)."},
            status=400,
        )

    reference_audio = request.FILES.getlist("reference_audio")
    max_audio = _MAX_REFERENCE_AUDIO[mode]
    if len(reference_audio) > max_audio:
        return Response(
            {"error": f"{Mode(mode).label} supports at most {max_audio} audio reference(s)."},
            status=400,
        )

    chat_transcript_raw = request.data.get("chat_transcript")
    chat_transcript = None
    if chat_transcript_raw:
        try:
            parsed = json.loads(chat_transcript_raw)
        except (TypeError, ValueError):
            return Response({"error": "chat_transcript must be JSON-encoded."}, status=400)
        if not isinstance(parsed, list):
            return Response({"error": "chat_transcript must be a JSON array."}, status=400)
        # Lightweight filtering, matching this module's existing style (no
        # full serializer validation) -- silently drops malformed entries
        # rather than rejecting the whole job over a chat-transcript glitch.
        chat_transcript = [
            m
            for m in parsed
            if isinstance(m, dict)
            and m.get("role") in PromptChatMessage.Role.values
            and str(m.get("content", "")).strip()
        ]

    width, height = compute_resolution(preset.megapixels, aspect_ratio)

    with transaction.atomic():
        job = GenerationJob.objects.create(
            user=request.user,
            mode=mode,
            preset=preset,
            duration=duration,
            raw_prompt=raw_prompt,
            improved_prompt=improved_prompt,
            megapixels=preset.megapixels,
            steps=preset.steps,
            aspect_ratio=aspect_ratio,
            width=width,
            height=height,
            duration_seconds=duration.duration_seconds,
            estimated_seconds=duration.estimated_render_seconds,
            use_spectrum=use_spectrum,
        )
        for order, file in enumerate(reference_images):
            ReferenceAsset.objects.create(
                job=job, kind=ReferenceAsset.Kind.IMAGE, order=order, file=file
            )
        for order, file in enumerate(reference_audio):
            ReferenceAsset.objects.create(
                job=job, kind=ReferenceAsset.Kind.AUDIO, order=order, file=file
            )
        if chat_transcript:
            # Only ever created here -- see chat_message()'s docstring: the
            # live conversation itself never touches the DB, only a chat
            # that actually ends up backing a queued job does.
            chat_session = PromptChatSession.objects.create(
                user=request.user, mode=mode, resulting_job=job
            )
            PromptChatMessage.objects.bulk_create(
                PromptChatMessage(session=chat_session, role=m["role"], content=m["content"])
                for m in chat_transcript
            )

    # No job id passed -- process_queue() is a shared FIFO queue processor,
    # not a per-job task (see generation/tasks.py). Safe to enqueue
    # redundantly; a call that finds the queue already being worked through
    # by an earlier trigger just no-ops.
    async_task("generation.tasks.process_queue")

    finish_time = expected_finish_times().get(job.id)
    return Response(_serialize_job(job, detail=True, expected_finish_time=finish_time), status=201)


@extend_schema(
    methods=["GET"],
    summary="Get a generation job's status/result",
    description="Meant to be polled while queued/processing. Only the owning user can fetch "
    "their own job (404 otherwise).",
    responses={200: GenerationJobDetailSerializer, 404: OpenApiResponse(description="Not found.")},
    tags=["generation"],
)
@extend_schema(
    methods=["DELETE"],
    summary="Delete a generation job",
    description="Removes the job (and its reference/video/thumbnail files) permanently. Only "
    "the owning user can delete their own job (404 otherwise). Refused while the job is "
    "actively processing (409) -- generation.tasks._execute_job() is mutating that row and "
    "expects it to still exist; delete it once it's done, or once it's back to queued.",
    responses={
        204: OpenApiResponse(description="Deleted."),
        404: OpenApiResponse(description="Not found."),
        409: OpenApiResponse(ErrorResponseSerializer, description="Job is currently processing."),
    },
    tags=["generation"],
)
@extend_schema(
    methods=["PATCH"],
    summary="Rename a generation job",
    description="Only touches title -- every other field is set at creation time and never "
    "editable. Only the owning user can rename their own job (404 otherwise).",
    request=UpdateJobTitleRequestSerializer,
    responses={200: GenerationJobDetailSerializer, 400: ErrorResponseSerializer, 404: OpenApiResponse(description="Not found.")},
    tags=["generation"],
)
@api_view(["GET", "DELETE", "PATCH"])
def job_detail(request, job_id: int):
    job = get_object_or_404(
        GenerationJob.objects.select_related("preset", "duration").prefetch_related("references"),
        id=job_id,
        user=request.user,
    )

    if request.method == "DELETE":
        if job.status == GenerationJob.Status.PROCESSING:
            return Response({"error": "Can't delete a job that's currently processing."}, status=409)
        for ref in job.references.all():
            ref.file.delete(save=False)
        if job.video_file:
            job.video_file.delete(save=False)
        if job.thumbnail_file:
            job.thumbnail_file.delete(save=False)
        job.delete()
        return Response(status=204)

    if request.method == "PATCH":
        title = request.data.get("title")
        if not isinstance(title, str):
            return Response({"error": "title is required and must be a string."}, status=400)
        if len(title) > 200:
            return Response({"error": "title must be at most 200 characters."}, status=400)
        job.title = title.strip()
        job.save(update_fields=["title"])
        return Response(_serialize_job(job, detail=True))

    finish_time = expected_finish_times().get(job.id)
    return Response(_serialize_job(job, detail=True, expected_finish_time=finish_time))
