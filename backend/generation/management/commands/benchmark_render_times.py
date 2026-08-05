"""Sweeps (resolution, duration) combinations per mode against the real
ComfyUI instance, recording render time / failure into BenchmarkResult --
the raw data you'd curate RenderPreset rows from.

This submits REAL jobs and spends REAL GPU time. It is never run
automatically by anything in this project -- run it deliberately, and
expect it to take a while. Large combinations can make ComfyUI itself crash
(observed in practice, not just a caught OOM) -- this command detects that
distinctly from a clean per-job failure and stops immediately rather than
continuing to hammer a dead server; restart ComfyUI and re-run the same
command to pick up where it left off (already-recorded combinations are
skipped unless --retest).

Usage:
    uv run manage.py benchmark_render_times
    uv run manage.py benchmark_render_times --modes t2v i2v --steps 20
    uv run manage.py benchmark_render_times --resolution 1920x1088 --duration 5 --duration 10
    uv run manage.py benchmark_render_times --retest --timeout 1200
"""

from __future__ import annotations

import time

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from generation.models import BenchmarkResult, Mode
from generation.tasks import SAVE_VIDEO_NODE_ID, build_api_workflow
from integrations import comfyui

DEFAULT_RESOLUTIONS = [(608, 320), (1344, 768), (1920, 1088)]
DEFAULT_DURATIONS = [3.0, 5.0, 8.0]
DEFAULT_STEPS = 20
DEFAULT_TIMEOUT = 900.0
DEFAULT_PROMPT = (
    "A static wide shot of a calm ocean at sunset, gentle waves, soft golden light, "
    "a seagull drifting across the frame. Audio: quiet ambient waves and a light breeze."
)


def _parse_resolution(value: str) -> tuple[int, int]:
    try:
        w, h = value.lower().split("x")
        return int(w), int(h)
    except ValueError as exc:
        raise CommandError(f"Invalid --resolution {value!r}, expected WIDTHxHEIGHT (e.g. 1344x768)") from exc


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument(
            "--modes", nargs="+", choices=Mode.values, default=list(Mode.values)
        )
        parser.add_argument(
            "--resolution",
            action="append",
            dest="resolutions",
            type=_parse_resolution,
            help="WIDTHxHEIGHT, repeatable. Defaults to a small built-in spread if omitted.",
        )
        parser.add_argument(
            "--duration",
            action="append",
            dest="durations",
            type=float,
            help="Clip length in seconds, repeatable. Defaults to a small built-in spread.",
        )
        parser.add_argument("--steps", type=int, default=DEFAULT_STEPS)
        parser.add_argument(
            "--timeout", type=float, default=DEFAULT_TIMEOUT, help="Per-job timeout, in seconds."
        )
        parser.add_argument("--prompt", default=DEFAULT_PROMPT)
        parser.add_argument(
            "--retest",
            action="store_true",
            help="Re-run combinations that already have a BenchmarkResult, overwriting it.",
        )

    def handle(self, *args, **options):
        modes = options["modes"]
        resolutions = options["resolutions"] or DEFAULT_RESOLUTIONS
        durations = options["durations"] or DEFAULT_DURATIONS
        steps = options["steps"]
        timeout = options["timeout"]
        prompt = options["prompt"]
        retest = options["retest"]

        if not comfyui.is_alive():
            raise CommandError(
                f"ComfyUI at {settings.COMFYUI_BASE_URL} is not reachable. Start it before benchmarking."
            )

        combos = [
            (mode, w, h, d)
            for mode in modes
            for (w, h) in resolutions
            for d in durations
        ]
        combos.sort(key=lambda c: c[1] * c[2] * c[3])  # cheapest (proxy: pixels x seconds) first

        self.stdout.write(f"Sweeping {len(combos)} combinations (steps={steps}, timeout={timeout}s)...")

        for mode, width, height, duration in combos:
            existing = BenchmarkResult.objects.filter(
                mode=mode, width=width, height=height, duration_seconds=duration, steps=steps
            ).first()
            if existing and not retest:
                self.stdout.write(f"  skip {mode} {width}x{height} {duration}s (already: {existing.status})")
                continue

            label = f"{mode} {width}x{height} {duration}s steps={steps}"
            self.stdout.write(f"  testing {label} ...")

            if not comfyui.is_alive():
                self._record(mode, width, height, duration, steps, BenchmarkResult.Status.CRASHED,
                              error_message="ComfyUI unreachable before this combination started.")
                raise CommandError(
                    "ComfyUI is no longer reachable -- likely crashed on the previous combination. "
                    "Restart it and re-run this command; already-recorded combinations will be skipped."
                )

            try:
                workflow = build_api_workflow(
                    mode, width=width, height=height, duration_seconds=duration,
                    steps=steps, prompt_text=prompt,
                )
                t_start = time.monotonic()
                prompt_id = comfyui.queue_prompt(workflow, client_id="benchmark")
                history = comfyui.wait_for_result(prompt_id, poll_seconds=3.0, timeout=timeout)
                comfyui.check_for_error(history)
                render_seconds = time.monotonic() - t_start

                output = comfyui.extract_video_output(history, SAVE_VIDEO_NODE_ID)
                comfyui.delete_output_file(output)  # don't keep benchmark videos around
                comfyui.clear_history(prompt_id)

                self._record(mode, width, height, duration, steps, BenchmarkResult.Status.OK,
                              render_seconds=render_seconds, comfyui_prompt_id=prompt_id)
                self.stdout.write(self.style.SUCCESS(f"    OK in {render_seconds:.1f}s"))

            except comfyui.ComfyUIExecutionError as exc:
                self._record(mode, width, height, duration, steps, BenchmarkResult.Status.OOM_ERROR,
                              error_message=str(exc))
                self.stdout.write(self.style.WARNING(f"    execution error (likely OOM): {exc}"))

            except TimeoutError as exc:
                self._record(mode, width, height, duration, steps, BenchmarkResult.Status.TIMEOUT,
                              error_message=str(exc))
                self.stdout.write(self.style.WARNING(f"    timed out after {timeout}s"))

            except requests.exceptions.RequestException as exc:
                self._record(mode, width, height, duration, steps, BenchmarkResult.Status.CRASHED,
                              error_message=str(exc))
                raise CommandError(
                    f"Lost connection to ComfyUI mid-request ({exc}) -- it likely crashed on "
                    f"{label}. Restart it and re-run this command; already-recorded combinations "
                    "will be skipped."
                ) from exc

        self.stdout.write(self.style.SUCCESS("Done."))

    def _record(self, mode, width, height, duration, steps, status, *, render_seconds=None,
                error_message="", comfyui_prompt_id=""):
        BenchmarkResult.objects.update_or_create(
            mode=mode, width=width, height=height, duration_seconds=duration, steps=steps,
            defaults={
                "status": status,
                "render_seconds": render_seconds,
                "error_message": error_message,
                "comfyui_prompt_id": comfyui_prompt_id,
            },
        )
