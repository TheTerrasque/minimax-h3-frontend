"""Sweeps (resolution, duration) combinations per mode against the real
ComfyUI instance, recording render time / failure into BenchmarkResult --
the raw data you'd curate RenderPreset rows from.

This submits REAL jobs and spends REAL GPU time. It is never run
automatically by anything in this project -- run it deliberately, and
expect it to take a while.

Built for genuinely unattended overnight runs against a ComfyUI instance
that's supervised by a process manager configured to auto-restart it on
crash (this project's target environment: back up within roughly a minute
of a crash, not a manual restart). Large combinations can make ComfyUI
itself crash (observed in practice, not just a caught OOM) -- when that
happens this command does NOT stop the sweep: it waits for ComfyUI to come
back (--restart-timeout, generously above the observed ~1 minute), submits
a tiny throwaway warm-up render first (model-loading/JIT warm-up so the
*next real* combo's timing isn't skewed by a cold start), then retries the
SAME combination that crashed -- it never actually completed, so simply
moving on would silently lose that data point. If a combination crashes
ComfyUI repeatedly (--max-crash-retries), it's given up on -- recorded as
CRASHED and the sweep moves on to the next combination -- rather than
retrying forever. Already-recorded combinations (including ones given up
on) are skipped on a re-run unless --retest.

Usage:
    uv run manage.py benchmark_render_times
    uv run manage.py benchmark_render_times --modes t2v i2v --steps 20
    uv run manage.py benchmark_render_times --resolution 1920x1088 --duration 5 --duration 10
    uv run manage.py benchmark_render_times --retest --timeout 1200
    uv run manage.py benchmark_render_times --restart-timeout 600 --max-crash-retries 5
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

# How long to wait for ComfyUI to come back after a crash, and how often to
# poll while waiting. Default is generous relative to this project's
# observed ~1 minute auto-restart, so a slow restart doesn't abandon an
# otherwise-fine overnight run.
DEFAULT_RESTART_TIMEOUT = 300.0
DEFAULT_RESTART_POLL_SECONDS = 5.0

# How many times a single combination is allowed to crash ComfyUI before
# this command gives up on it (rather than retrying forever) and moves on.
DEFAULT_MAX_CRASH_RETRIES = 3

# A cheap, tiny render submitted right after ComfyUI comes back from a
# crash -- purely to get the model loaded/warm before the next *real*
# (recorded) combination runs, so that combination's timing reflects steady
# -state performance rather than a cold start. Never recorded as a
# BenchmarkResult. t2v regardless of which mode actually crashed: all three
# modes share the same underlying model, and t2v needs no reference uploads.
_WARMUP_WIDTH = 320
_WARMUP_HEIGHT = 192
_WARMUP_DURATION_SECONDS = 2.0
_WARMUP_STEPS = 8
_WARMUP_TIMEOUT = 120.0
_WARMUP_PROMPT = "A simple static test pattern."


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
        parser.add_argument(
            "--restart-timeout",
            type=float,
            default=DEFAULT_RESTART_TIMEOUT,
            help="Max seconds to wait for ComfyUI to come back after a crash before giving up "
            "on the combination it crashed on.",
        )
        parser.add_argument(
            "--restart-poll-interval",
            type=float,
            default=DEFAULT_RESTART_POLL_SECONDS,
            help="Seconds between reachability checks while waiting for ComfyUI to restart.",
        )
        parser.add_argument(
            "--max-crash-retries",
            type=int,
            default=DEFAULT_MAX_CRASH_RETRIES,
            help="How many times a single combination may crash ComfyUI before this command "
            "gives up on it (records CRASHED) and moves on, rather than retrying forever.",
        )

    def handle(self, *args, **options):
        modes = options["modes"]
        resolutions = options["resolutions"] or DEFAULT_RESOLUTIONS
        durations = options["durations"] or DEFAULT_DURATIONS
        steps = options["steps"]
        timeout = options["timeout"]
        prompt = options["prompt"]
        retest = options["retest"]
        restart_timeout = options["restart_timeout"]
        restart_poll_interval = options["restart_poll_interval"]
        max_crash_retries = options["max_crash_retries"]

        if not comfyui.is_alive():
            # Even the very first check gets the same "wait it out" treatment
            # as a mid-sweep crash -- this command is meant to be started and
            # left running unattended, and a restart loop mid-cycle right at
            # startup shouldn't be any different from one mid-sweep.
            self.stdout.write(self.style.WARNING(
                f"ComfyUI at {settings.COMFYUI_BASE_URL} isn't reachable yet -- waiting for it..."
            ))
            if not self._wait_for_restart(restart_timeout, restart_poll_interval):
                raise CommandError(
                    f"ComfyUI at {settings.COMFYUI_BASE_URL} did not become reachable within "
                    f"{restart_timeout:.1f}s. Start it before benchmarking."
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
            self._run_combo_with_crash_recovery(
                mode, width, height, duration, steps, prompt, timeout,
                label, restart_timeout, restart_poll_interval, max_crash_retries,
            )

        self.stdout.write(self.style.SUCCESS("Done."))

    def _run_combo_with_crash_recovery(
        self, mode, width, height, duration, steps, prompt, timeout,
        label, restart_timeout, restart_poll_interval, max_crash_retries,
    ):
        """Attempts one combination, transparently recovering from a ComfyUI
        crash (wait for the auto-restart -> warm-up render -> retry the SAME
        combination, since it never actually completed) up to
        max_crash_retries times before giving up on it and returning control
        to the sweep -- a single bad combination must never halt the rest of
        an unattended overnight run.
        """
        crash_count = 0
        while True:
            if not comfyui.is_alive():
                self.stdout.write(self.style.WARNING(
                    f"    ComfyUI unreachable before {label} -- likely crashed on the previous "
                    "combination. Waiting for it to restart..."
                ))
                if not self._wait_for_restart(restart_timeout, restart_poll_interval):
                    self._record(mode, width, height, duration, steps, BenchmarkResult.Status.CRASHED,
                                  error_message=f"ComfyUI did not come back within {restart_timeout:.1f}s.")
                    self.stdout.write(self.style.ERROR(
                        f"    giving up on {label} -- ComfyUI never came back."
                    ))
                    return
                self._warm_up()

            self.stdout.write(f"  testing {label} ...")
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
                return

            except comfyui.ComfyUIExecutionError as exc:
                # ComfyUI stayed alive and told us this combo itself failed
                # (e.g. OOM) -- a terminal result for this combo, not a crash
                # to retry.
                self._record(mode, width, height, duration, steps, BenchmarkResult.Status.OOM_ERROR,
                              error_message=str(exc))
                self.stdout.write(self.style.WARNING(f"    execution error (likely OOM): {exc}"))
                return

            except TimeoutError as exc:
                self._record(mode, width, height, duration, steps, BenchmarkResult.Status.TIMEOUT,
                              error_message=str(exc))
                self.stdout.write(self.style.WARNING(f"    timed out after {timeout}s"))
                return

            except requests.exceptions.RequestException as exc:
                crash_count += 1
                self.stdout.write(self.style.WARNING(
                    f"    lost connection to ComfyUI on {label} (crash {crash_count}/"
                    f"{max_crash_retries}): {exc}"
                ))
                if crash_count > max_crash_retries:
                    self._record(
                        mode, width, height, duration, steps, BenchmarkResult.Status.CRASHED,
                        error_message=f"Crashed ComfyUI {crash_count} times attempting this "
                        f"combination; giving up. Last error: {exc}",
                    )
                    self.stdout.write(self.style.ERROR(
                        f"    giving up on {label} after {crash_count} crashes."
                    ))
                    return
                # Loop back around: the top of the loop will notice ComfyUI is
                # down, wait for the restart, warm up, and retry this same
                # combination.

    def _wait_for_restart(self, timeout: float, poll_interval: float) -> bool:
        """Polls comfyui.is_alive() until it returns True or `timeout`
        elapses. Returns whether it came back."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if comfyui.is_alive():
                self.stdout.write(self.style.SUCCESS("    ComfyUI is back up."))
                return True
            time.sleep(poll_interval)
        return comfyui.is_alive()

    def _warm_up(self) -> None:
        """A tiny, throwaway render (never recorded as a BenchmarkResult) run
        right after ComfyUI comes back from a crash, so the model is already
        loaded/warm by the time the next *real* combination runs -- without
        this, that combination's timing would include a cold-start penalty
        that has nothing to do with its own resolution/duration/steps.
        Failure here isn't fatal: if ComfyUI isn't fully ready yet despite
        is_alive() succeeding, the actual combination retry right after this
        will hit the same crash-recovery path again.
        """
        self.stdout.write("    Warming up with a tiny throwaway render...")
        try:
            workflow = build_api_workflow(
                Mode.TEXT_TO_VIDEO, width=_WARMUP_WIDTH, height=_WARMUP_HEIGHT,
                duration_seconds=_WARMUP_DURATION_SECONDS, steps=_WARMUP_STEPS,
                prompt_text=_WARMUP_PROMPT,
            )
            prompt_id = comfyui.queue_prompt(workflow, client_id="benchmark-warmup")
            comfyui.wait_for_result(prompt_id, poll_seconds=2.0, timeout=_WARMUP_TIMEOUT)
            comfyui.clear_history(prompt_id)
            self.stdout.write(self.style.SUCCESS("    Warm-up done."))
        except Exception as exc:  # noqa: BLE001 -- deliberately broad, see docstring
            self.stdout.write(self.style.WARNING(
                f"    Warm-up render didn't complete cleanly ({exc}) -- continuing anyway."
            ))

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
