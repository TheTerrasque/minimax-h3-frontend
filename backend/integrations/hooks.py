"""Optional site-specific pre/post hooks around the LLM call and a job's
render -- see config/settings.py's PRE_LLM_HOOK/POST_LLM_HOOK/
PRE_RENDER_HOOK/POST_RENDER_HOOK and backend/hooks_example.py for a
starting template.

Each setting is a dotted Python path (same convention as Django's own
ACCOUNT_ADAPTER) resolving to a callable(**context) -- run_hook() imports
and calls it synchronously, so a pre-hook genuinely finishes (e.g. "load a
model first") before the actual LLM/render call starts. Unset (the
default) is a no-op.

A hook that raises is logged and swallowed, never propagated -- this is
site-specific glue, not part of the actual generation path; a broken
notification script or a model-warmup call that fails shouldn't take an
LLM call or a render down with it. If a hook genuinely needs to gate
whether the real call proceeds (rather than just running before/after it),
that's out of scope for this mechanism -- write that logic into the real
code path instead.
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)


def run_hook(setting_name: str, **context: Any) -> None:
    """Looks up settings.<setting_name>; if set, imports it as a dotted
    path and calls it with the given context kwargs. No-ops if unset.
    """
    dotted_path = getattr(settings, setting_name, "")
    if not dotted_path:
        return
    try:
        hook = import_string(dotted_path)
        hook(**context)
    except Exception:
        logger.exception("%s hook (%s) raised -- ignoring", setting_name, dotted_path)
