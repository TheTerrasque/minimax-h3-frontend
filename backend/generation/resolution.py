"""Aspect-ratio -> pixel-resolution math, replicating the ResolutionSelector
node's own logic (megapixels + aspect ratio -> width/height, rounded to a
multiple) -- see resources/workflows/*.json. We bypass that node entirely in
generation/tasks.py::build_api_workflow() (it only accepts an aspect-ratio
preset + megapixels widget, not arbitrary literal width/height, and we need
to set literal width/height there), so this is our own reimplementation of
"pick pixel dimensions from megapixels + ratio" for the API/frontend layer.

Unlike megapixels (which determines render time -- see RenderPreset), aspect
ratio does not meaningfully affect render time for a fixed pixel count, so
it's kept as a small fixed enum here rather than a DB-backed model: it's
orthogonal to the RenderPreset/RenderDuration catalog, not another axis of
things worth benchmarking.
"""

from __future__ import annotations

# Mirrors ResolutionSelector's own aspect_ratio combo options exactly (see
# backend/scripts/object_info_cache/ResolutionSelector.json, fetched live
# from ComfyUI) -- (value, label) pairs, value is what the API/frontend use.
ASPECT_RATIOS: list[tuple[str, str]] = [
    ("1:1", "1:1 (Square)"),
    ("2:3", "2:3 (Portrait Photo)"),
    ("3:2", "3:2 (Photo)"),
    ("3:4", "3:4 (Portrait Standard)"),
    ("4:3", "4:3 (Standard)"),
    ("9:16", "9:16 (Portrait Widescreen)"),
    ("16:9", "16:9 (Widescreen)"),
    ("21:9", "21:9 (Ultrawide)"),
]
ASPECT_RATIO_VALUES = [value for value, _ in ASPECT_RATIOS]
DEFAULT_ASPECT_RATIO = "16:9"

# MiniMaxH3ImageToVideo/ReferenceToVideo's width/height inputs both declare
# step=32 (confirmed live via /object_info) -- round to that so every
# resolution we ever send is guaranteed valid regardless of aspect ratio.
RESOLUTION_MULTIPLE = 32


def compute_resolution(megapixels: float, aspect_ratio: str) -> tuple[int, int]:
    """(megapixels, "W:H") -> (width, height), both multiples of
    RESOLUTION_MULTIPLE, with width/height ratio as close to the requested
    aspect ratio as that rounding allows."""
    w_ratio_str, h_ratio_str = aspect_ratio.split(":")
    w_ratio, h_ratio = float(w_ratio_str), float(h_ratio_str)

    target_pixels = megapixels * 1_000_000
    height = (target_pixels * h_ratio / w_ratio) ** 0.5
    width = height * (w_ratio / h_ratio)

    def round_to_multiple(value: float) -> int:
        return max(RESOLUTION_MULTIPLE, round(value / RESOLUTION_MULTIPLE) * RESOLUTION_MULTIPLE)

    return round_to_multiple(width), round_to_multiple(height)
