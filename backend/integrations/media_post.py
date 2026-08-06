"""ffmpeg post-processing of a ComfyUI video render into a still frame or
an audio-only file -- see generation/models.py's Mode docstring: the
image/audio modes reuse the same video workflows as t2v/r2v (there's no
native image- or audio-only ComfyUI graph for this model) and derive their
actual output from that rendered video via these two functions, called
from generation/tasks.py's _finish_job_from_history().

Verified against real ComfyUI renders (not just synthetic test files) --
see git history for the manual verification this was built against: a
5-frame (near-zero duration) render at normal resolution produces a fully
coherent frame 0 despite the model's width/height/length node schema
tooltip calling that frame count "untested" (trained range ~124-362); a
32x32 render at 10 steps still produces real, non-silent audio.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

FFMPEG_TIMEOUT = 60


class FfmpegError(RuntimeError):
    pass


def _run_ffmpeg(args: list[str], input_bytes: bytes, output_suffix: str) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        in_path = Path(tmp) / "input.mp4"
        out_path = Path(tmp) / f"output{output_suffix}"
        in_path.write_bytes(input_bytes)
        try:
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", str(in_path), *args, str(out_path)],
                capture_output=True,
                timeout=FFMPEG_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            raise FfmpegError(f"ffmpeg timed out after {FFMPEG_TIMEOUT}s") from exc
        if result.returncode != 0 or not out_path.exists():
            raise FfmpegError(f"ffmpeg failed: {result.stderr.decode(errors='replace')[-2000:]}")
        return out_path.read_bytes()


def extract_first_frame(video_bytes: bytes) -> bytes:
    """Extracts frame 0 as a PNG -- backs Mode.TEXT_TO_IMAGE/REFERENCE_TO_IMAGE.
    -update 1 (write a single image, not a numbered sequence) avoids an
    otherwise-harmless ffmpeg warning about the output filename not
    matching an image-sequence pattern."""
    return _run_ffmpeg(["-frames:v", "1", "-update", "1"], video_bytes, ".png")


def extract_audio(video_bytes: bytes) -> bytes:
    """Extracts the audio track as an MP3 -- backs Mode.TEXT_TO_AUDIO/REFERENCE_TO_AUDIO."""
    return _run_ffmpeg(["-vn", "-acodec", "libmp3lame", "-q:a", "2"], video_bytes, ".mp3")
