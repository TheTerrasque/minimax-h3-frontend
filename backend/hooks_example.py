"""Starting template for the optional pre/post hooks -- see
integrations/hooks.py and config/settings.py's PRE_LLM_HOOK/POST_LLM_HOOK/
PRE_RENDER_HOOK/POST_RENDER_HOOK.

Not imported by anything itself -- copy this file (or write your own),
point the relevant setting at it as a dotted path, and edit freely. E.g.
in .env:

    PRE_RENDER_HOOK=hooks_example.wake_gpu_server
    POST_RENDER_HOOK=hooks_example.notify_render_done

Each hook is called synchronously with keyword arguments only, and its
return value is ignored -- see integrations/hooks.run_hook() for exactly
what each one receives and how a raised exception is handled (logged,
never propagated).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def wake_gpu_server(**kwargs) -> None:
    """PRE_RENDER_HOOK / PRE_LLM_HOOK example: block until whatever backs
    ComfyUI or the LLM is actually up, e.g. pinging a sleeping machine over
    the network or hitting a wake-on-LAN endpoint, before the real call
    starts. `kwargs` holds "job" (PRE_RENDER_HOOK) or "messages"
    (PRE_LLM_HOOK) -- unused here, but available if the wake logic needs to
    vary by mode/content.
    """
    logger.info("wake_gpu_server hook fired")


def notify_render_done(*, job, success: bool, **kwargs) -> None:
    """POST_RENDER_HOOK example: fire a desktop/phone notification when a
    render finishes. Swap the logger.info() below for e.g. a
    ntfy.sh/Pushover/webhook POST.
    """
    logger.info("Job %s finished (success=%s)", job.id, success)


def notify_llm_reply(*, messages, reply, **kwargs) -> None:
    """POST_LLM_HOOK example: same idea, for the LLM call instead of a render."""
    logger.info("LLM replied (%d chars)", len(reply))
