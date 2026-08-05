"""DRF views for the generation app.

Only a health check, config flags, and the LLM prompt-assist endpoints
(one-shot refine + interactive chat) are implemented in this pass. Full CRUD
for presets/jobs/references (GET /api/presets/, POST /api/jobs/,
POST /api/jobs/{id}/references/, GET /api/queue-estimate/, etc.) is deferred
to the next pass -- see ARCHITECTURE.md. Validation here is deliberately
lightweight (plain dict checks, no DRF Serializer classes) to match that
minimal scope; upgrade to real serializers when the rest of the CRUD layer
gets built.

Every view carries an @extend_schema so the auto-generated OpenAPI docs
(config/urls.py: /api/schema/swagger-ui/) actually describe request/response
bodies -- drf-spectacular can't infer those from a plain @api_view function
that reads request.data by hand, so the *_serializer classes below exist
purely for documentation and aren't used for real validation.
"""

from django.conf import settings
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from integrations import llm

from .models import Mode, PromptChatMessage, PromptChatSession


class HealthResponseSerializer(serializers.Serializer):
    status = serializers.CharField()


class ConfigResponseSerializer(serializers.Serializer):
    llm_enabled = serializers.BooleanField(
        help_text="False when no LLM is configured -- hide all AI UI (refine button, chat) when so."
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


class RefinePromptResponseSerializer(serializers.Serializer):
    improved_prompt = serializers.CharField()


class CreateChatSessionRequestSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=Mode.choices)


class ChatMessageSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=PromptChatMessage.Role.choices)
    content = serializers.CharField()


class ChatSessionResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    mode = serializers.ChoiceField(choices=Mode.choices)
    messages = ChatMessageSerializer(many=True)


class PostChatMessageRequestSerializer(serializers.Serializer):
    content = serializers.CharField()
    reference_labels = serializers.ListField(
        child=serializers.CharField(), required=False
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
    return Response({"llm_enabled": settings.LLM_ENABLED})


def _validate_mode(data) -> str | Response:
    mode = data.get("mode")
    if mode not in Mode.values:
        return Response({"error": f"mode must be one of {Mode.values}"}, status=400)
    return mode


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
    reference_labels = request.data.get("reference_labels") or None

    try:
        improved_prompt = llm.improve_prompt(mode, raw_prompt, reference_labels)
    except llm.LLMError as exc:
        return Response({"error": str(exc)}, status=502)
    return Response({"improved_prompt": improved_prompt})


@extend_schema(
    summary="Start an interactive prompt-chat session",
    description=(
        "Persisted (see ARCHITECTURE.md) rather than stateless, so it survives a page refresh. "
        "503 if no LLM is configured."
    ),
    request=CreateChatSessionRequestSerializer,
    responses={
        201: ChatSessionResponseSerializer,
        400: ErrorResponseSerializer,
        503: OpenApiResponse(ErrorResponseSerializer, description="No LLM is configured."),
    },
    tags=["prompt-assist"],
)
@api_view(["POST"])
def create_chat_session(request):
    if not settings.LLM_ENABLED:
        return Response({"error": "No LLM is configured."}, status=503)

    mode = _validate_mode(request.data)
    if isinstance(mode, Response):
        return mode
    session = PromptChatSession.objects.create(user=request.user, mode=mode)
    return Response({"id": session.id, "mode": session.mode, "messages": []}, status=201)


@extend_schema(
    summary="Fetch a chat session's full history",
    description="Lets the frontend resume a session after a page refresh instead of "
    "keeping it in memory. Only the owning user can fetch their own sessions (404 otherwise).",
    responses={200: ChatSessionResponseSerializer, 404: OpenApiResponse(description="Not found.")},
    tags=["prompt-assist"],
)
@api_view(["GET"])
def get_chat_session(request, session_id: int):
    session = get_object_or_404(PromptChatSession, id=session_id, user=request.user)
    messages = [{"role": m.role, "content": m.content} for m in session.messages.all()]
    return Response({"id": session.id, "mode": session.mode, "messages": messages})


@extend_schema(
    summary="Post a message to a chat session",
    description="Appends the user's message, gets the LLM's reply (with the full "
    "conversation history as context), persists and returns it. 503 if no LLM is configured.",
    request=PostChatMessageRequestSerializer,
    responses={
        201: ChatMessageSerializer,
        400: ErrorResponseSerializer,
        404: OpenApiResponse(description="Not found."),
        502: OpenApiResponse(ErrorResponseSerializer, description="The LLM request itself failed."),
        503: OpenApiResponse(ErrorResponseSerializer, description="No LLM is configured."),
    },
    tags=["prompt-assist"],
)
@api_view(["POST"])
def post_chat_message(request, session_id: int):
    if not settings.LLM_ENABLED:
        return Response({"error": "No LLM is configured."}, status=503)

    session = get_object_or_404(PromptChatSession, id=session_id, user=request.user)
    content = request.data.get("content", "")
    if not content.strip():
        return Response({"error": "content is required."}, status=400)
    reference_labels = request.data.get("reference_labels") or None

    PromptChatMessage.objects.create(
        session=session, role=PromptChatMessage.Role.USER, content=content
    )
    history = [{"role": m.role, "content": m.content} for m in session.messages.all()]

    try:
        reply = llm.chat_reply(session.mode, history, reference_labels)
    except llm.LLMError as exc:
        return Response({"error": str(exc)}, status=502)

    assistant_message = PromptChatMessage.objects.create(
        session=session, role=PromptChatMessage.Role.ASSISTANT, content=reply
    )
    session.save()  # bumps updated_at (auto_now)
    return Response({"role": assistant_message.role, "content": assistant_message.content}, status=201)
