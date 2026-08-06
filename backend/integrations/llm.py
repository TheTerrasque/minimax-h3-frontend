"""LLM-backed prompt improvement, per features.md item 11.

Talks to any OpenAI-compatible /chat/completions endpoint (configured in
Django settings, not hardcoded to one provider). Uses the guides in
"resources/prompt instructions/" as system context so the rewritten prompt
follows MiniMax H3's expected structure (shot timelines, <Picture N>/<Video
N>/<Audio N> reference labels, etc).

LLM integration is entirely optional -- see settings.LLM_ENABLED and
is_configured() below. Callers (generation/api.py) must check that before
exposing any AI feature; there is no automatic/implicit LLM call anywhere in
the job-execution path (generation/tasks.py) -- refinement only happens when
a user explicitly asks for it (the "AI refine" button or the chat).
"""

from __future__ import annotations

import base64
from functools import lru_cache

import requests
from django.conf import settings

# r2v uses the multi-reference rewrite format; t2v/i2v share the base guide.
_GUIDE_FILENAMES = {
    "t2v": "VIDEO_PROMPT_WRITING_GUIDE_base_en.md",
    "i2v": "VIDEO_PROMPT_WRITING_GUIDE_base_en.md",
    "r2v": "VIDEO_PROMPT_WRITING_GUIDE_ref_en.md",
}


class LLMError(RuntimeError):
    pass


# Fenced-code-block language tag the chat system prompt asks the model to
# wrap a finalized prompt in -- lets the frontend mechanically pull it out
# of a chat reply (to show as its own "use this prompt" card) instead of
# asking the user to spot it in a wall of markdown. Keep this in sync with
# frontend/src/features/generate/chatMarkdown.ts's matching constant.
FINAL_PROMPT_FENCE = "final-prompt"


def is_configured() -> bool:
    # LLM_API_KEY is deliberately not required here -- see settings.py's
    # LLM_ENABLED comment; many self-hosted OpenAI-compatible servers don't
    # need one at all.
    return bool(settings.LLM_API_BASE_URL and settings.LLM_MODEL)


@lru_cache(maxsize=None)
def _load_guide(mode: str) -> str:
    filename = _GUIDE_FILENAMES[mode]
    path = settings.RESOURCES_DIR / "prompt instructions" / filename
    return path.read_text(encoding="utf-8")


def _reference_note(reference_labels: list[str] | None) -> str:
    return (
        f"Available reference labels to use verbatim: {', '.join(reference_labels)}."
        if reference_labels
        else "No reference assets are attached; do not invent <Picture N>/<Video N>/<Audio N> labels."
    )


def _image_content_part(image_bytes: bytes, content_type: str) -> dict:
    """One OpenAI vision-API `image_url` content part, image bytes inlined
    as a base64 data URL -- see chat_reply()'s reference_images param."""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{content_type};base64,{b64}"}}


def _post_chat_completion(messages: list[dict[str, str]]) -> str:
    if not is_configured():
        raise LLMError("No LLM is configured (LLM_API_BASE_URL/LLM_MODEL unset).")
    headers = {"Authorization": f"Bearer {settings.LLM_API_KEY}"} if settings.LLM_API_KEY else {}
    resp = requests.post(
        f"{settings.LLM_API_BASE_URL.rstrip('/')}/chat/completions",
        headers=headers,
        json={"model": settings.LLM_MODEL, "messages": messages, "temperature": 0.4},
        timeout=120,
    )
    if resp.status_code >= 400:
        raise LLMError(f"LLM request failed: {resp.text}")
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def improve_prompt(mode: str, raw_prompt: str, reference_labels: list[str] | None = None) -> str:
    """One-shot rewrite of raw_prompt into MiniMax H3's expected prompt
    structure -- backs the "AI refine" button."""
    guide = _load_guide(mode)
    messages = [
        {
            "role": "system",
            "content": (
                "You rewrite user video prompts to follow the house prompt-writing guide "
                "below exactly. Output only the rewritten prompt, nothing else.\n\n" + guide
            ),
        },
        {
            "role": "user",
            "content": f"{_reference_note(reference_labels)}\n\nUser's raw prompt:\n{raw_prompt}",
        },
    ]
    return _post_chat_completion(messages)


def chat_reply(
    mode: str,
    history: list[dict[str, str]],
    reference_labels: list[str] | None = None,
    raw_prompt: str = "",
    reference_images: list[tuple[bytes, str]] | None = None,
) -> str:
    """Multi-turn conversational prompt crafting -- backs the interactive
    chat feature. Entirely stateless: nothing here reads or writes
    generation.models.PromptChatSession/PromptChatMessage -- those are only
    ever created (see generation/api.py's jobs()) once a chat's transcript
    actually gets used to queue a job, not during the live conversation.

    history is the full prior conversation as [{"role": "user"|"assistant",
    "content": ...}, ...] (oldest first); this call appends nothing itself,
    the caller is expected to have already appended the latest user message
    before calling. Returns the assistant's reply text only.

    raw_prompt: the user's current draft in the main prompt box, if any --
    given as system-message context so the assistant knows about it even on
    the very first turn, before the user repeats themselves in the chat.

    reference_images: (bytes, content_type) pairs for the currently-staged
    reference images, resent with every call (the caller already has them
    in memory client-side, so this is cheap) and attached to the latest
    user turn as vision content parts -- but only when
    settings.LLM_VISION_ENABLED, since a text-only model may error on or
    silently ignore image_url content it doesn't understand. When disabled
    (or no images), this is a no-op and the request is plain text, same as
    before.
    """
    guide = _load_guide(mode)
    draft_note = (
        f"\n\nThe user's current draft prompt in the main text box (not yet part of this "
        f"conversation -- they haven't sent it as a chat message): {raw_prompt.strip()}"
        if raw_prompt.strip()
        else ""
    )
    system_message = {
        "role": "system",
        "content": (
            "You help a user iteratively write and refine a video-generation prompt for the "
            "MiniMax H3 model, by having a conversation with them -- ask clarifying questions "
            "when useful, suggest concrete ideas, and revise based on their feedback. The house "
            "prompt-writing guide below defines the exact structure the FINAL prompt must follow. "
            "When you have a finalized, ready-to-use prompt to give the user -- proactively once "
            "the conversation has converged on one, or immediately whenever they ask you to "
            f"finalize/output it -- put ONLY that exact prompt text in its own fenced code block "
            f"tagged `{FINAL_PROMPT_FENCE}`, like:\n"
            f"```{FINAL_PROMPT_FENCE}\n<the finished prompt, nothing else>\n```\n"
            f"Never use that `{FINAL_PROMPT_FENCE}` tag for anything other than a complete, "
            "finished prompt -- an app reads that exact block mechanically and offers it to the "
            "user as a one-click action, so it must contain the prompt text alone (no labels, "
            "no commentary) whenever you use it. The rest of your reply (questions, suggestions, "
            "explanations) goes outside that block, as normal.\n\n"
            f"{_reference_note(reference_labels)}{draft_note}\n\n{guide}"
        ),
    }
    messages = [system_message, *history]

    if settings.LLM_VISION_ENABLED and reference_images and messages[-1]["role"] == "user":
        text = messages[-1]["content"]
        messages[-1] = {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                *(_image_content_part(data, content_type) for data, content_type in reference_images),
            ],
        }

    return _post_chat_completion(messages)
