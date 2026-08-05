"""LLM-backed prompt improvement, per features.md item 11.

Talks to any OpenAI-compatible /chat/completions endpoint (configured in
Django settings, not hardcoded to one provider). Uses the guides in
"resources/prompt instructions/" as system context so the rewritten prompt
follows MiniMax H3's expected structure (shot timelines, <Picture N>/<Video
N>/<Audio N> reference labels, etc).
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


@lru_cache(maxsize=None)
def _load_guide(mode: str) -> str:
    filename = _GUIDE_FILENAMES[mode]
    path = settings.RESOURCES_DIR / "prompt instructions" / filename
    return path.read_text(encoding="utf-8")


def improve_prompt(mode: str, raw_prompt: str, reference_labels: list[str] | None = None) -> str:
    """Rewrites raw_prompt into MiniMax H3's expected prompt structure.

    reference_labels are the ReferenceAsset.label values already attached to
    the job (e.g. ["Picture 1", "Picture 2", "Audio 1"]) so the LLM can use
    the correct <Picture N>/<Video N>/<Audio N> tokens instead of inventing
    its own numbering.
    """
    guide = _load_guide(mode)
    reference_note = (
        f"Available reference labels to use verbatim: {', '.join(reference_labels)}."
        if reference_labels
        else "No reference assets are attached; do not invent <Picture N>/<Video N>/<Audio N> labels."
    )

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
            "content": f"{reference_note}\n\nUser's raw prompt:\n{raw_prompt}",
        },
    ]

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
