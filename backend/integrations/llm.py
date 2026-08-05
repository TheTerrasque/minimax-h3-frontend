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


def is_configured() -> bool:
    return bool(settings.LLM_API_BASE_URL and settings.LLM_API_KEY and settings.LLM_MODEL)


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


def _post_chat_completion(messages: list[dict[str, str]]) -> str:
    if not is_configured():
        raise LLMError("No LLM is configured (LLM_API_BASE_URL/LLM_API_KEY/LLM_MODEL unset).")
    resp = requests.post(
        f"{settings.LLM_API_BASE_URL.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
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
    mode: str, history: list[dict[str, str]], reference_labels: list[str] | None = None
) -> str:
    """Multi-turn conversational prompt crafting -- backs the interactive
    chat feature (generation.models.PromptChatSession/PromptChatMessage).

    history is the full prior conversation as [{"role": "user"|"assistant",
    "content": ...}, ...] (oldest first); this call appends nothing itself,
    the caller is expected to have already appended the latest user message
    before calling. Returns the assistant's reply text only -- the caller
    persists it as the next PromptChatMessage.
    """
    guide = _load_guide(mode)
    system_message = {
        "role": "system",
        "content": (
            "You help a user iteratively write and refine a video-generation prompt for the "
            "MiniMax H3 model, by having a conversation with them -- ask clarifying questions "
            "when useful, suggest concrete ideas, and revise based on their feedback. The house "
            "prompt-writing guide below defines the exact structure the FINAL prompt must follow. "
            "When the user seems happy with the direction, or asks you to finalize/output it, "
            "present the complete, ready-to-use prompt formatted per the guide, clearly set apart "
            "from the rest of your reply (e.g. in its own paragraph) so it's easy to copy.\n\n"
            f"{_reference_note(reference_labels)}\n\n{guide}"
        ),
    }
    return _post_chat_completion([system_message, *history])
