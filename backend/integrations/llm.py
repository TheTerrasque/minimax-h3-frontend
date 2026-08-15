"""LLM-backed prompt improvement, per features.md item 11.

Talks to any OpenAI-compatible /chat/completions endpoint (configured in
Django settings, not hardcoded to one provider). Uses the guides in
"resources/prompt instructions/" as system context so the rewritten prompt
follows MiniMax H3's expected structure -- shot timelines and <Picture N>/
<Video N>/<Audio N> reference labels for video; much simpler single-scene
(image) or sound-only (audio) guides for the two modes that discard most
of that same underlying video render, see _GUIDE_FILENAMES below.

LLM integration is entirely optional -- see settings.LLM_ENABLED and
is_configured() below. Callers (generation/api.py) must check that before
exposing any AI feature; there is no automatic/implicit LLM call anywhere in
the job-execution path (generation/tasks.py) -- refinement only happens when
a user explicitly asks for it (the "AI refine" button or the chat).
"""

from __future__ import annotations

import base64
import json
import re
from functools import lru_cache

import requests
from django.conf import settings

from integrations import hooks

# r2v uses the multi-reference rewrite format; t2v/i2v share the base guide.
# Image/audio modes render through the same underlying t2v/r2v workflows
# (see generation/models.py's Mode docstring) but only a fraction of that
# output survives (one frame for image; the audio track, minus video, for
# audio) -- the video guides' shot/cut/camera-motion structure describes
# content that gets thrown away, so image/audio get their own, much
# simpler guides instead of reusing the video ones verbatim.
_GUIDE_FILENAMES = {
    "t2v": "VIDEO_PROMPT_WRITING_GUIDE_base_en.md",
    "i2v": "VIDEO_PROMPT_WRITING_GUIDE_base_en.md",
    "r2v": "VIDEO_PROMPT_WRITING_GUIDE_ref_en.md",
    "t2i": "IMAGE_PROMPT_WRITING_GUIDE_base_en.md",
    "r2i": "IMAGE_PROMPT_WRITING_GUIDE_ref_en.md",
    "t2a": "AUDIO_PROMPT_WRITING_GUIDE_base_en.md",
    "r2a": "AUDIO_PROMPT_WRITING_GUIDE_ref_en.md",
}

# Mirrors generation/models.py's CONTENT_TYPE_BY_MODE -- duplicated rather
# than imported, since integrations/ is the lower-level app here (generation/
# already depends on it, see tasks.py's imports, not the other way around).
_CONTENT_TYPE_BY_MODE = {
    "t2v": "video",
    "i2v": "video",
    "r2v": "video",
    "t2i": "image",
    "r2i": "image",
    "t2a": "audio",
    "r2a": "audio",
}


class LLMError(RuntimeError):
    pass


# Fenced-code-block language tag the chat system prompt asks the model to
# wrap a finalized prompt in -- lets the frontend mechanically pull it out
# of a chat reply (to show as its own "use this prompt" card) instead of
# asking the user to spot it in a wall of markdown. Keep this in sync with
# frontend/src/features/generate/chatMarkdown.ts's matching constant.
FINAL_PROMPT_FENCE = "final-prompt"

# Style guidance baked into both default system prompts below, independent
# of whatever structure the mode's house guide itself defines -- written
# prompts should read clearly and spell things out rather than leaving them
# for the render model to infer. The dialogue clause exists because a
# prompt that implies speech without giving the words (e.g. "she comments
# on it") lets the render model invent its own lines -- confirmed against
# a real render that did exactly that.
_PROMPT_STYLE_NOTE = (
    "Written prompts should be easy to read, and explicit rather than implicit. If a character "
    "speaks, sings, or narrates, always write their exact words -- never describe that they "
    "\"say something\"/\"comment on\"/etc. without giving the words themselves, since the render "
    "model will invent its own dialogue rather than staying silent."
)


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


def _continuation_note(is_continuation: bool) -> str:
    """Director Mode's only other hook into this module beyond extra_context
    (see improve_prompt()/chat_reply()'s is_continuation param) -- a clip
    flagged "continues previous" splices in real motion/audio continuity
    automatically at render time (see integrations/motion_context.py), but
    that only wires up the technical mechanism; nothing told the LLM the
    *prompt itself* should read as an unbroken continuation rather than a
    fresh shot. Confirmed against a real render: an unstructured
    continuation prompt produced a jump to a completely different camera
    angle despite continues_previous being set.
    """
    if not is_continuation:
        return ""
    return (
        "\n\nThis clip continues directly from the previous one in the sequence, as one unbroken "
        "shot with no cut -- the render pipeline splices in real motion/audio continuity "
        "automatically, but the prompt text itself must still read as a seamless continuation: "
        "keep the same camera angle/framing, setting, and characters established by the previous "
        "clip's action rather than introducing a new one, and describe only how the action "
        "continues or develops from where it left off."
    )


_WRAPPING_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\n(.*)\n```$", re.DOTALL)


def _strip_wrapping_fence(text: str) -> str:
    """improve_prompt() asks for the rewritten prompt and nothing else, but
    a model that's just seen fenced ```text examples in the guide (see the
    image/audio ref guides' Section 3) sometimes wraps its own reply in one
    too, out of pattern-matching habit rather than actual intent -- which
    would otherwise get submitted to ComfyUI as part of the literal prompt
    text if the user queues it unedited. Only strips a fence that wraps the
    *entire* reply, not one appearing partway through real content.
    """
    match = _WRAPPING_FENCE_RE.match(text.strip())
    return match.group(1).strip() if match else text


def _image_content_part(image_bytes: bytes, content_type: str) -> dict:
    """One OpenAI vision-API `image_url` content part, image bytes inlined
    as a base64 data URL -- see chat_reply()'s reference_images param."""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{content_type};base64,{b64}"}}


def _post_chat_completion(messages: list[dict[str, str]], *, timeout: int = 120) -> str:
    if not is_configured():
        raise LLMError("No LLM is configured (LLM_API_BASE_URL/LLM_MODEL unset).")
    headers = {"Authorization": f"Bearer {settings.LLM_API_KEY}"} if settings.LLM_API_KEY else {}

    # See integrations/hooks.py -- e.g. waking a model server before the
    # first call of the day. Runs (and, if configured, finishes) before the
    # actual request below.
    hooks.run_hook("PRE_LLM_HOOK", messages=messages)

    resp = requests.post(
        f"{settings.LLM_API_BASE_URL.rstrip('/')}/chat/completions",
        headers=headers,
        json={"model": settings.LLM_MODEL, "messages": messages, "temperature": 0.4},
        timeout=timeout,
    )
    if resp.status_code >= 400:
        raise LLMError(f"LLM request failed: {resp.text}")
    resp.raise_for_status()
    reply = resp.json()["choices"][0]["message"]["content"].strip()

    hooks.run_hook("POST_LLM_HOOK", messages=messages, reply=reply)

    return reply


def _custom_system_note(mode_specific: str) -> str:
    """Optional site-specific system-prompt additions -- see
    settings.LLM_CUSTOM_SYSTEM_PROMPT (applied by both callers below) and
    the LLM_CUSTOM_SYSTEM_PROMPT_REFINE/_CHAT variants (mode_specific,
    passed in by each caller). Appended after the house guide, in that
    order, so site config can add to or steer the shipped behavior without
    editing the guide files themselves. Empty (no-op) unless configured.
    """
    parts = [settings.LLM_CUSTOM_SYSTEM_PROMPT.strip(), mode_specific.strip()]
    text = "\n\n".join(p for p in parts if p)
    return f"\n\n{text}" if text else ""


def _extra_context_note(extra_context: str | None) -> str:
    """Caller-supplied free-text context layered on top of the mode's own
    house guide -- currently only Director Mode uses this (a project's
    overarching_prompt: shared world/setting/character prose every clip in
    the project should stay consistent with, see director/models.py's
    Project docstring), threaded through from generation/api.py's
    refine_prompt()/chat_message() views as an optional `extra_context`
    field so this stays a generic capability rather than Director-specific
    plumbing baked into this module.
    """
    text = (extra_context or "").strip()
    if not text:
        return ""
    return (
        "\n\nShared project context (setting/characters/continuity this prompt should stay "
        f"consistent with -- don't just restate it, use it to inform word choices): {text}"
    )


def _duration_note(mode: str, duration_seconds: float | None) -> str:
    # Image mode's duration is pinned to the technical minimum (only frame
    # 0 survives, see generation/models.py's Mode docstring) -- telling the
    # LLM about it would be noise, not useful context, so this is
    # deliberately skipped for image content regardless of what's passed.
    if _CONTENT_TYPE_BY_MODE[mode] == "image" or not duration_seconds:
        return ""
    if _CONTENT_TYPE_BY_MODE[mode] == "audio":
        return (
            f"\n\nTarget audio duration: {duration_seconds:.2f} seconds. Any dynamics/change "
            "described as happening at a specific time (per the guide's \"dynamics over the "
            "clip's duration\") must fall within this duration."
        )
    # Video: the house guide requires shot-cut timestamps (and the last-frame
    # alignment instruction's S.SS mark) to fall within the actual video
    # duration -- without this, the LLM has no way to know that duration and
    # would have to guess, easily producing cut times past the real clip
    # length.
    return (
        f"\n\nTarget video duration: {duration_seconds:.2f} seconds. Every shot cut timestamp, "
        "and the last-frame alignment instruction's S.SS mark if this task uses one, must fall "
        "within this duration."
    )


def improve_prompt(
    mode: str,
    raw_prompt: str,
    reference_labels: list[str] | None = None,
    duration_seconds: float | None = None,
    reference_images: list[tuple[bytes, str]] | None = None,
    extra_context: str | None = None,
    is_continuation: bool = False,
) -> str:
    """One-shot rewrite of raw_prompt into MiniMax H3's expected prompt
    structure -- backs the "AI refine" button.

    reference_images: (bytes, content_type) pairs for the currently-staged
    reference images (e.g. i2v's first/last frame) -- attached to the user
    message as vision content parts, same as chat_reply(), but only when
    settings.LLM_VISION_ENABLED; a no-op plain-text request otherwise.

    extra_context: see _extra_context_note(). is_continuation: see
    _continuation_note().
    """
    guide = _load_guide(mode)
    user_content: str | list[dict] = f"{_reference_note(reference_labels)}\n\nUser's raw prompt:\n{raw_prompt}"
    if settings.LLM_VISION_ENABLED and reference_images:
        user_content = [
            {"type": "text", "text": user_content},
            *(_image_content_part(data, content_type) for data, content_type in reference_images),
        ]
    messages = [
        {
            "role": "system",
            "content": (
                f"You rewrite user {_CONTENT_TYPE_BY_MODE[mode]} prompts to follow the house "
                "prompt-writing guide below exactly. Output only the rewritten prompt, nothing else. "
                f"{_PROMPT_STYLE_NOTE}"
                f"{_duration_note(mode, duration_seconds)}{_extra_context_note(extra_context)}"
                f"{_continuation_note(is_continuation)}\n\n{guide}"
                f"{_custom_system_note(settings.LLM_CUSTOM_SYSTEM_PROMPT_REFINE)}"
            ),
        },
        {"role": "user", "content": user_content},
    ]
    return _strip_wrapping_fence(_post_chat_completion(messages))


def chat_reply(
    mode: str,
    history: list[dict[str, str]],
    reference_labels: list[str] | None = None,
    raw_prompt: str = "",
    reference_images: list[tuple[bytes, str]] | None = None,
    improved_prompt: str = "",
    duration_seconds: float | None = None,
    extra_context: str | None = None,
    is_continuation: bool = False,
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

    improved_prompt: the currently-active AI-refined prompt shown to the
    user (from a prior "AI refine" click or an earlier chat turn), if any --
    given as separate, clearly-labeled context so the assistant knows what
    the user is actually looking at right now and can revise *that* instead
    of drifting back to raw_prompt or re-deriving from scratch.

    duration_seconds: the currently-selected clip length, if any -- see
    _duration_note(); content-type-dependent (shot-cut timestamps for
    video, dynamics timing for audio, ignored entirely for image).

    reference_images: (bytes, content_type) pairs for the currently-staged
    reference images, resent with every call (the caller already has them
    in memory client-side, so this is cheap) and attached to the latest
    user turn as vision content parts -- but only when
    settings.LLM_VISION_ENABLED, since a text-only model may error on or
    silently ignore image_url content it doesn't understand. When disabled
    (or no images), this is a no-op and the request is plain text, same as
    before.

    extra_context: see _extra_context_note(). is_continuation: see
    _continuation_note().
    """
    guide = _load_guide(mode)
    draft_note = (
        f"\n\nThe user's current draft prompt in the main text box (not yet part of this "
        f"conversation -- they haven't sent it as a chat message): {raw_prompt.strip()}"
        if raw_prompt.strip()
        else ""
    )
    improved_note = (
        f"\n\nThe current AI prompt -- the AI-refined prompt the user already has in hand "
        f"(from a prior refine or chat turn), shown to them right now and what will actually "
        f"be rendered unless they change it: {improved_prompt.strip()}"
        if improved_prompt.strip()
        else ""
    )
    system_message = {
        "role": "system",
        "content": (
            f"You help a user iteratively write and refine a {_CONTENT_TYPE_BY_MODE[mode]}-"
            "generation prompt for the MiniMax H3 model, by having a conversation with them -- "
            "ask clarifying questions when useful, suggest concrete ideas, and revise based on "
            f"their feedback. {_PROMPT_STYLE_NOTE} When revising an existing draft, change only what the user actually "
            "asked you to change -- keep every other part of the current draft exactly as it is, "
            "word-for-word, unless they explicitly ask for a broader rewrite. The house "
            "prompt-writing guide below defines the exact structure "
            "the FINAL prompt must follow. When you have a finalized, ready-to-use prompt to "
            "give the user -- proactively once the conversation has converged on one, or "
            "immediately whenever they ask you to finalize/output it -- put ONLY that exact "
            f"prompt text in its own fenced code block tagged `{FINAL_PROMPT_FENCE}`, like:\n"
            f"```{FINAL_PROMPT_FENCE}\n<the finished prompt, nothing else>\n```\n"
            f"Never use that `{FINAL_PROMPT_FENCE}` tag for anything other than a complete, "
            "finished prompt -- an app reads that exact block mechanically and offers it to the "
            "user as a one-click action, so it must contain the prompt text alone (no labels, "
            "no commentary) whenever you use it. The rest of your reply (questions, suggestions, "
            "explanations) goes outside that block, as normal.\n\n"
            f"{_reference_note(reference_labels)}{draft_note}{improved_note}"
            f"{_duration_note(mode, duration_seconds)}{_extra_context_note(extra_context)}"
            f"{_continuation_note(is_continuation)}\n\n{guide}"
            f"{_custom_system_note(settings.LLM_CUSTOM_SYSTEM_PROMPT_CHAT)}"
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


@lru_cache(maxsize=1)
def _load_plan_guide() -> str:
    path = settings.RESOURCES_DIR / "prompt instructions" / "DIRECTOR_PLAN_GUIDE_en.md"
    return path.read_text(encoding="utf-8")


def plan_scenes(
    idea_text: str,
    *,
    resource_labels: list[str] | None = None,
    extra_context: str | None = None,
    require_reference_mode: bool = False,
) -> list:
    """One-shot script/idea -> a proposed ordered sequence of Director Mode
    scenes, backing "Generate from script". Returns whatever JSON value the
    LLM replied with (expected: a list of {"mode", "continues_previous",
    "prompt", "notes"} dicts, per DIRECTOR_PLAN_GUIDE_en.md) -- deliberately
    untyped/unvalidated here, since this module has no notion of Director's
    Mode/CONTINUATION_CAPABLE_MODES; director/services.py's
    normalize_planned_scenes() is what turns this into a trustworthy shape
    (the LLM's JSON is untrusted input, not a contract -- see that
    function's docstring).

    require_reference_mode: True once the target project has shared
    resources -- every clip must then be r2v (only r2v can actually wire a
    shared resource into a render), so this swaps in the reference variant
    of the house prompt guide instead of the base one, and tells the model
    every scene must be r2v and should use the listed reference tokens
    where relevant. normalize_planned_scenes() enforces the mode itself
    regardless of what comes back here -- this only shapes what gets
    written, not what's trusted.

    Raises LLMError both for a failed request and for a reply that isn't
    parseable JSON at all, so callers only need to handle one exception
    type for "the plan step itself didn't work" (a 502, same as
    improve_prompt/chat_reply's own failure mode) -- as opposed to "the
    JSON parsed but some scenes were junk", which normalize_planned_scenes
    handles by dropping/repairing entries rather than raising.
    """
    plan_guide = _load_plan_guide()
    prompt_guide = _load_guide("r2v" if require_reference_mode else "t2v")
    mode_note = (
        "\n\nThis project has shared reference assets attached -- every scene you propose must "
        'use "mode": "r2v" and its prompt must follow the reference guide below\'s structure, '
        "incorporating the listed reference token(s) wherever genuinely relevant to that scene's "
        "content. Never invent a token that isn't listed, and never force one in where it doesn't "
        "actually apply."
        if require_reference_mode
        else ""
    )
    guide_note = (
        "reference sections apply"
        if require_reference_mode
        else "T2VA/I2VA sections apply; ignore FL2VA/L2VA, this app never supplies a last-frame image"
    )
    messages = [
        {
            "role": "system",
            "content": (
                f"{_PROMPT_STYLE_NOTE}\n\n{plan_guide}{mode_note}\n\n---\n\nHouse prompt-writing "
                f"guide referenced above ({guide_note}):\n\n{prompt_guide}\n\n"
                f"{_reference_note(resource_labels)}{_extra_context_note(extra_context)}"
                f"{_custom_system_note(settings.LLM_CUSTOM_SYSTEM_PROMPT_REFINE)}"
            ),
        },
        {"role": "user", "content": idea_text},
    ]
    # A full scene breakdown is many multi-paragraph prompts in one reply --
    # far more output than a single refine/chat turn -- so this gets a
    # longer timeout than _post_chat_completion's 120s default rather than
    # sharing it and risking a spurious timeout on a longer script/idea.
    reply = _strip_wrapping_fence(_post_chat_completion(messages, timeout=300))
    try:
        return json.loads(reply)
    except json.JSONDecodeError as exc:
        raise LLMError(f"The AI's reply wasn't valid JSON: {exc}") from exc


def extract_reference_subjects(idea_text: str, extra_context: str | None = None) -> list:
    """One-shot script/idea -> a proposed list of character/object/voice
    reference assets worth generating up front, backing "Generate from
    script"'s reference-suggestion step. Returns whatever JSON value the
    LLM replied with (expected: a list of {"name", "kind", "description"}
    dicts) -- deliberately untyped/unvalidated here, same posture as
    plan_scenes(): director/api.py's extract_references() view does the
    minimal shape-checking needed before handing this to the frontend, and
    nothing here is ever written to a Clip/ProjectResource directly.

    The point of this step: plan_scenes() (and a human writing prompts by
    hand) can only keep a character's look/voice consistent across hard
    cuts by re-describing them in words every time (see
    DIRECTOR_PLAN_GUIDE_en.md section 2.5) -- real reference assets (a
    character-sheet image, a voice sample) pin it far more reliably, since
    a render that actually draws on one inherits the look/voice from the
    reference itself rather than the render model's own reinterpretation
    of a text description. This surfaces which recurring subjects would
    actually benefit from one, and a ready-to-use generation prompt for
    each, so the frontend can offer a one-click "generate this reference"
    per entry before the user proceeds to plan_scenes().

    Each returned "description" is meant to be submitted verbatim as a
    t2i (kind="image") or t2a (kind="audio") job's raw_prompt -- it
    already follows the respective house guide's own structure, so the
    frontend doesn't need to run it through improve_prompt() first.
    """
    image_guide = _load_guide("t2i")
    audio_guide = _load_guide("t2a")
    messages = [
        {
            "role": "system",
            "content": (
                "You read a script or loose idea for a multi-clip video project and identify which "
                "recurring named characters, objects, or settings would genuinely benefit from a "
                "fixed reference asset -- something a downstream image/audio generator can create "
                "once, so every clip that uses it can draw on the same reference instead of the "
                "render model reinventing that subject's appearance or voice from a text "
                "description each time. Only propose a subject that actually recurs across more "
                "than one scene and would be visibly worse for looking/sounding inconsistent -- "
                "never propose a one-off background detail that appears in a single scene, and "
                "don't invent named characters/objects the text doesn't actually establish. A "
                "recurring character who's both seen and heard speaking can get two separate "
                "entries (one image, one audio) if both are worth pinning down.\n\n"
                "For each proposed subject, respond with:\n"
                '- "name": a short human label (e.g. "Mara", "the lighthouse", "Mara\'s voice").\n'
                '- "kind": "image" for a visual character/object/setting reference, or "audio" for '
                "a speaking character's voice.\n"
                '- "description": a ready-to-use generation prompt for that single reference '
                "asset, written to submit as-is. For \"image\", follow the image guide below's own "
                "structure (one dense paragraph describing a single frame: style, subject, "
                "composition, environment, lighting). For \"audio\", follow the audio guide below's "
                "own structure (a plain description of the voice) and, since this is a voice "
                "reference specifically, always have the character speak one short representative "
                "line using `<d>[English] ...</d>` (invent a natural line if the source text "
                "doesn't give one) with the voice's pitch/timbre/pacing/accent described just "
                "before the tag, so the sample actually captures speech.\n\n"
                "Respond with only a single JSON array, no prose before or after it (a fenced "
                "```json code block wrapping the array is fine; nothing else is) -- an empty array "
                "if nothing in the text genuinely warrants a reference.\n\n"
                "---\n\nImage house guide referenced above:\n\n"
                f"{image_guide}\n\n---\n\nAudio house guide referenced above:\n\n{audio_guide}"
                f"{_extra_context_note(extra_context)}"
                f"{_custom_system_note(settings.LLM_CUSTOM_SYSTEM_PROMPT_REFINE)}"
            ),
        },
        {"role": "user", "content": idea_text},
    ]
    reply = _strip_wrapping_fence(_post_chat_completion(messages, timeout=180))
    try:
        return json.loads(reply)
    except json.JSONDecodeError as exc:
        raise LLMError(f"The AI's reply wasn't valid JSON: {exc}") from exc


def check_project_continuity(overarching_prompt: str, clips: list[dict]) -> str:
    """One-shot review of a Director project's full clip sequence, backing
    "Check continuity" -- a plain-text report of likely problems for a
    human to read, not a structured/automated fix. Deliberately free-form
    prose (a bulleted list), not JSON: unlike plan_scenes(), nothing here
    needs to be parsed back into Clip rows, so there's no reason to force a
    schema onto what's really just an LLM's written opinion.

    clips: [{"order": int, "mode": str, "continues_previous": bool,
    "prompt": str}, ...] in board order -- the caller (director/api.py's
    check_continuity()) builds this from real Clip rows; this module stays
    ignorant of the Clip model itself, same as plan_scenes().
    """
    lines = []
    for c in clips:
        tag = f"{c['mode']}{', continues previous' if c['continues_previous'] else ''}"
        prompt = c["prompt"].strip() or "(empty prompt)"
        lines.append(f"Clip {c['order'] + 1} ({tag}):\n{prompt}")
    clips_text = "\n\n".join(lines) if lines else "(no clips yet)"
    context_note = f"Shared project context: {overarching_prompt.strip()}\n\n" if overarching_prompt.strip() else ""

    messages = [
        {
            "role": "system",
            "content": (
                "You review a sequence of video clip prompts from a multi-clip video project for "
                "continuity problems. Clips marked \"continues previous\" are meant to read as one "
                "unbroken shot -- flag any pair where the continuing clip's content doesn't actually "
                "carry on the same camera angle/framing, setting, or characters the previous clip "
                "established (a described change of angle/location/subject on a "
                "\"continues previous\" clip is a real problem, not a stylistic choice). Also flag, "
                "across ANY clips (continuing or not): contradictions with the shared project "
                "context or with each other (e.g. a character's appearance or the setting "
                "described differently in different clips), a prompt that implies dialogue/"
                "narration/singing without giving the exact words (the render model will invent "
                "lines rather than staying silent), and anything else vague enough to likely "
                "produce a confused or inconsistent render.\n\n"
                "Reply with a short bulleted list of concrete issues, each naming the specific clip "
                "number(s) involved and what's wrong. If you find nothing worth flagging, reply "
                "with one plain sentence saying so -- don't invent issues to fill space, and don't "
                "comment on anything that isn't actually a continuity/consistency problem (e.g. "
                "don't critique writing style or suggest unrelated creative changes)."
                f"{_custom_system_note(settings.LLM_CUSTOM_SYSTEM_PROMPT_REFINE)}"
            ),
        },
        {"role": "user", "content": f"{context_note}{clips_text}"},
    ]
    # Same reasoning as plan_scenes()'s longer timeout -- reviewing every
    # clip in a large project is a bigger request than a single refine turn.
    return _post_chat_completion(messages, timeout=180)
