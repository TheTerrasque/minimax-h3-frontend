"""Diagnostic: checks whether each extra configured via COMFYUI_EXTRAS (see
extras.md, config/settings.py's EXTRAS_CONFIG) actually has its ComfyUI-side
node(s) installed on the configured ComfyUI instance right now. Doesn't
touch the render path at all -- purely a "did I actually install the custom
node yet" sanity check to run after flipping COMFYUI_EXTRAS on and before
queuing a real job with it.

Usage:
    uv run manage.py check_extras
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from integrations import comfyui, spectrum

# One entry per extra this app actually knows how to wire in -- see
# extras.md's "why only one extra is wired up right now" for why this isn't
# a real registry yet. A slug present in settings.EXTRAS_CONFIG but missing
# here is reported as "not recognized by this app" rather than silently
# skipped, since that's almost certainly a typo in COMFYUI_EXTRAS.
_KNOWN_EXTRAS: dict[str, list[str]] = {
    "spectrum": [spectrum.SPECTRUM_NODE_CLASS],
}

_LEVEL_LABELS = {
    0: "optional, default off",
    1: "optional, default on",
    2: "forced on for every job",
}


class Command(BaseCommand):
    help = __doc__

    def handle(self, *args, **options):
        if not settings.EXTRAS_CONFIG:
            self.stdout.write("No extras configured (COMFYUI_EXTRAS is empty).")
            return

        if not comfyui.is_alive():
            self.stdout.write(
                self.style.ERROR(
                    f"ComfyUI at {settings.COMFYUI_BASE_URL} isn't reachable -- "
                    "can't check node availability."
                )
            )
            return

        for slug, level in settings.EXTRAS_CONFIG.items():
            level_label = _LEVEL_LABELS.get(level, f"level {level}")
            node_classes = _KNOWN_EXTRAS.get(slug)
            if node_classes is None:
                self.stdout.write(
                    self.style.WARNING(
                        f"{slug} ({level_label}): not recognized by this app yet -- "
                        "check COMFYUI_EXTRAS for a typo (see extras.md)."
                    )
                )
                continue

            missing = [c for c in node_classes if comfyui.get_object_info(c) is None]
            if missing:
                self.stdout.write(
                    self.style.ERROR(
                        f"{slug} ({level_label}): NOT detected -- missing ComfyUI node(s): "
                        f"{', '.join(missing)}. See extras.md#{slug} for install steps."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"{slug} ({level_label}): detected -- {', '.join(node_classes)} found."
                    )
                )
