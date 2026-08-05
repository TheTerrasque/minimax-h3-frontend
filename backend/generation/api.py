"""DRF views for the generation app.

Only a health check, config flags, and the LLM prompt-assist endpoints
(one-shot refine + interactive chat) are implemented in this pass. Full CRUD
for presets/jobs/references (GET /api/presets/, POST /api/jobs/,
POST /api/jobs/{id}/references/, GET /api/queue-estimate/, etc.) is deferred
to the next pass -- see ARCHITECTURE.md. Validation here is deliberately
lightweight (plain dict checks, no DRF Serializer classes) to match that
minimal scope; upgrade to real serializers when the rest of the CRUD layer
gets built.
"""

from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from integrations import llm

from .models import Mode, PromptChatMessage, PromptChatSession


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    return Response({"status": "ok"})


@api_view(["GET"])
@permission_classes([AllowAny])
def config(request):
    """Feature flags the frontend needs before rendering, per features.md
    item 11: when no LLM is configured, none of the AI UI (refine button,
    chat) should be shown at all.
    """
    return Response({"llm_enabled": settings.LLM_ENABLED})


def _validate_mode(data) -> str | Response:
    mode = data.get("mode")
    if mode not in Mode.values:
        return Response({"error": f"mode must be one of {Mode.values}"}, status=400)
    return mode


@api_view(["POST"])
def refine_prompt(request):
    """One-shot rewrite -- the "AI refine" button. Body:
    {"mode": "t2v"|"i2v"|"r2v", "raw_prompt": "...", "reference_labels": [...]}
    (reference_labels optional -- there's no GenerationJob yet at this point,
    so the frontend passes whatever labels it's currently staging, e.g. from
    images the user has attached but not yet submitted as a job.)
    """
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


@api_view(["POST"])
def create_chat_session(request):
    """Starts a new interactive prompt-chat session. Body: {"mode": "..."}"""
    if not settings.LLM_ENABLED:
        return Response({"error": "No LLM is configured."}, status=503)

    mode = _validate_mode(request.data)
    if isinstance(mode, Response):
        return mode
    session = PromptChatSession.objects.create(user=request.user, mode=mode)
    return Response({"id": session.id, "mode": session.mode, "messages": []}, status=201)


@api_view(["GET"])
def get_chat_session(request, session_id: int):
    """Fetches a session's full history, so the frontend can resume it after
    a page refresh instead of needing to keep it in memory."""
    session = get_object_or_404(PromptChatSession, id=session_id, user=request.user)
    messages = [
        {"role": m.role, "content": m.content} for m in session.messages.all()
    ]
    return Response({"id": session.id, "mode": session.mode, "messages": messages})


@api_view(["POST"])
def post_chat_message(request, session_id: int):
    """Appends a user message to a chat session and returns the LLM's reply.
    Body: {"content": "...", "reference_labels": [...]} (reference_labels
    optional, same rationale as refine_prompt).
    """
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
    history = [
        {"role": m.role, "content": m.content} for m in session.messages.all()
    ]

    try:
        reply = llm.chat_reply(session.mode, history, reference_labels)
    except llm.LLMError as exc:
        return Response({"error": str(exc)}, status=502)

    assistant_message = PromptChatMessage.objects.create(
        session=session, role=PromptChatMessage.Role.ASSISTANT, content=reply
    )
    session.save()  # bumps updated_at (auto_now)
    return Response({"role": assistant_message.role, "content": assistant_message.content}, status=201)
