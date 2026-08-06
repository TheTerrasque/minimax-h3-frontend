import re
import uuid
from pathlib import Path

from django.conf import settings
from django.db import models
from django.utils import timezone

_SAFE_EXTENSION_RE = re.compile(r"\.[a-z0-9]{1,8}")


def _random_upload_path(directory: str, original_filename: str) -> str:
    """Builds an unguessable upload path -- a random UUID plus a sanitized
    original extension, under a Y/m-bucketed directory. Deliberately uses
    nothing else from the caller-supplied filename.

    Both GenerationJob.video_file (previously ComfyUI's own sequential
    output name verbatim, e.g. "MiniMax_H3_00324_.mp4" -> "00325" ->
    "00326" -- trivially walkable by incrementing a counter) and
    ReferenceAsset.file (previously the uploader's original filename
    verbatim, e.g. a phone camera's "20230625_092948.jpg") were served by
    django.views.static.serve (see config/urls.py) with no authentication
    or per-user access control at all -- an unguessable name is what
    actually stands between "knows the app's URL" and "can view/download
    any user's generated video or reference upload," so this alone doesn't
    replace real access control on that view, only removes the easiest way
    to exploit its absence (see that view's own docstring/ARCHITECTURE.md).
    """
    ext = Path(original_filename).suffix.lower()
    if not _SAFE_EXTENSION_RE.fullmatch(ext):
        ext = ""
    return f"{directory}/{timezone.now():%Y/%m}/{uuid.uuid4().hex}{ext}"


def generated_video_upload_path(instance, filename: str) -> str:
    return _random_upload_path("generated_videos", filename)


def reference_upload_path(instance, filename: str) -> str:
    return _random_upload_path("references", filename)


class Mode(models.TextChoices):
    """The three MiniMax H3 workflows in resources/workflows/."""

    TEXT_TO_VIDEO = "t2v", "Video from text"
    IMAGE_TO_VIDEO = "i2v", "Provide first frame"
    REFERENCE_TO_VIDEO = "r2v", "Provide references"


class RenderPreset(models.Model):
    """Admin-editable (mode, megapixels, steps) "quality tier" -- the first of
    two axes that together determine render time (the other is
    RenderDuration below; aspect ratio, by contrast, does NOT meaningfully
    affect render time for a fixed pixel count, so it's kept as a small
    fixed enum in resolution.py rather than a third DB-backed axis here --
    see that module's docstring).

    Backs features.md item 4 ("internal list of supported resolutions and
    seconds for each mode, with estimated time to render"). Literal
    width/height aren't stored here at all -- they're computed from
    (megapixels, a user-chosen aspect ratio) at job-creation time via
    resolution.compute_resolution() and snapshotted onto the GenerationJob,
    the same way RenderDuration.estimated_render_seconds gets snapshotted
    onto GenerationJob.estimated_seconds so later admin edits here don't
    retroactively change a number already shown to a user.

    "Draft mode" (fast, low-res, low-step passes to sanity-check a prompt
    before committing to a full render) is just another preset row here --
    e.g. is_draft=True, ~0.2 megapixels, few steps -- rather than a separate
    model/pipeline; is_draft only exists so the frontend can group/label
    these separately from "real" presets.
    """

    mode = models.CharField(max_length=8, choices=Mode.choices)
    label = models.CharField(max_length=60, help_text='e.g. "Draft", "Standard", "High quality".')
    megapixels = models.FloatField(help_text="Target pixel count for this tier, in megapixels.")
    steps = models.PositiveIntegerField(default=20, help_text="Sampler steps (BasicScheduler.steps).")
    is_draft = models.BooleanField(
        default=False,
        help_text="Fast/low-quality preset meant for previewing a prompt, not a final render.",
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(
        default=0,
        help_text="Admin-controlled display order (lower first) -- kept in sync across every "
        "mode's row for the same label. See generation/admin_api.py's reorder endpoint.",
    )

    class Meta:
        ordering = ["sort_order", "mode", "megapixels"]

    def __str__(self) -> str:
        draft = " (draft)" if self.is_draft else ""
        return f"{self.get_mode_display()} {self.label} ({self.megapixels}MP){draft}"


class RenderDuration(models.Model):
    """One selectable clip length for a given RenderPreset, with its own
    admin-set/benchmarked estimated_render_seconds -- e.g. a "Standard"
    preset might offer 3s/5s/8s/12s, each independently benchmarked rather
    than derived from a formula, since render time doesn't scale perfectly
    linearly with duration in practice. A GenerationJob references one of
    these directly (not just a RenderPreset) -- see GenerationJob.duration.
    """

    preset = models.ForeignKey(RenderPreset, on_delete=models.CASCADE, related_name="durations")
    duration_seconds = models.FloatField(help_text="Requested clip length, in seconds.")
    estimated_render_seconds = models.PositiveIntegerField(
        help_text="Expected wall-clock render time for this (preset, duration) combination."
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["preset", "duration_seconds"]
        constraints = [
            models.UniqueConstraint(
                fields=["preset", "duration_seconds"], name="unique_preset_duration"
            )
        ]

    def __str__(self) -> str:
        return f"{self.preset} @ {self.duration_seconds}s (~{self.estimated_render_seconds}s)"


class GenerationJob(models.Model):
    class Status(models.TextChoices):
        """Deliberately just three states for now (see tasks.py's
        process_queue()): jobs are processed strictly one at a time, FIFO,
        so there's no need to distinguish "running" from "about to run" the
        way a parallel-worker model would. DONE covers both success and
        failure -- check error_message/video_file to tell them apart (a
        real terminal FAILED state can come back later if that distinction
        needs to be first-class again)."""

        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        DONE = "done", "Done"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="generation_jobs"
    )
    mode = models.CharField(max_length=8, choices=Mode.choices)
    preset = models.ForeignKey(RenderPreset, on_delete=models.PROTECT, related_name="jobs")
    duration = models.ForeignKey(RenderDuration, on_delete=models.PROTECT, related_name="jobs")

    raw_prompt = models.TextField()
    improved_prompt = models.TextField(blank=True, default="")

    # Snapshotted at creation from preset/duration/aspect_ratio (see
    # generation/api.py::jobs() and resolution.compute_resolution()) so
    # later admin edits to the catalog don't retroactively change a value
    # already shown to a user, and so tasks.py has everything it needs
    # without joining back through preset/duration.
    megapixels = models.FloatField(help_text="Snapshot of preset.megapixels at queue time.")
    steps = models.PositiveIntegerField(
        default=20,
        help_text="Snapshot of preset.steps at queue time -- also used as the workload "
        "dimension (steps * megapixels * duration_seconds) by the admin catalog's "
        "curve-fit estimator, see generation/admin_api.py.",
    )
    aspect_ratio = models.CharField(max_length=8, help_text='e.g. "16:9" -- see resolution.ASPECT_RATIOS.')
    width = models.PositiveIntegerField(help_text="Computed from megapixels + aspect_ratio at queue time.")
    height = models.PositiveIntegerField(help_text="Computed from megapixels + aspect_ratio at queue time.")
    duration_seconds = models.FloatField(help_text="Snapshot of duration.duration_seconds at queue time.")

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    estimated_seconds = models.PositiveIntegerField(
        help_text="Snapshot of duration.estimated_render_seconds at queue time."
    )

    # Unused since the switch to tasks.process_queue(): one shared Django-Q2
    # task now works through the whole FIFO queue rather than one task per
    # job, so there's no single task id to attribute to a given job anymore.
    q_task_id = models.CharField(max_length=64, blank=True, default="")

    # ComfyUI-side identifiers, see resources/COMFYUI_API_GUIDE.md.
    comfyui_prompt_id = models.CharField(max_length=64, blank=True, default="")
    video_file = models.FileField(upload_to=generated_video_upload_path, blank=True)
    error_message = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"GenerationJob({self.id}, {self.mode}, {self.status})"


class ReferenceAsset(models.Model):
    class Kind(models.TextChoices):
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"
        AUDIO = "audio", "Audio"

    job = models.ForeignKey(GenerationJob, on_delete=models.CASCADE, related_name="references")
    kind = models.CharField(max_length=8, choices=Kind.choices)
    order = models.PositiveIntegerField(default=0)
    file = models.FileField(upload_to=reference_upload_path)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["kind", "order"]

    def __str__(self) -> str:
        return self.label

    @property
    def label(self) -> str:
        """"Picture 1" / "Video 1" / "Audio 1" style label.

        Matches the <Picture N>/<Video N>/<Audio N> convention in
        resources/prompt instructions/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md, so
        the frontend can offer "insert reference" buttons that write tokens
        the LLM prompt-assist step already understands.
        """
        kind_labels = {
            self.Kind.IMAGE: "Picture",
            self.Kind.VIDEO: "Video",
            self.Kind.AUDIO: "Audio",
        }
        same_kind_ids = list(
            self.job.references.filter(kind=self.kind).order_by("order", "id").values_list(
                "id", flat=True
            )
        )
        position = same_kind_ids.index(self.id) + 1 if self.id in same_kind_ids else 1
        return f"{kind_labels[self.kind]} {position}"


class PromptChatSession(models.Model):
    """An audit trail of a chat conversation that actually got used to draft
    a queued job's prompt -- NOT the live conversation itself, which stays
    entirely client-side (React state, generation/api.py's chat_message()
    is fully stateless) until/unless the user actually queues a job with it
    attached. Only ever created already linked to resulting_job, inside the
    same POST /api/jobs/ transaction that creates the job (see api.py's
    jobs()) -- a conversation the user has but never uses to queue anything
    is never written here at all, by design (a user request: no DB trail
    for chat content that doesn't end up backing a real job). null=True on
    resulting_job is a vestige of an earlier design where sessions could
    exist before a job did; kept for schema compatibility rather than
    forcing a migration, but every row created going forward has it set.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="prompt_chat_sessions"
    )
    mode = models.CharField(max_length=8, choices=Mode.choices)
    resulting_job = models.ForeignKey(
        GenerationJob,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chat_sessions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"PromptChatSession({self.id}, {self.user}, {self.mode})"


class PromptChatMessage(models.Model):
    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"

    session = models.ForeignKey(PromptChatSession, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=16, choices=Role.choices)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.role}: {self.content[:50]}"


class BenchmarkResult(models.Model):
    """One (mode, resolution, duration, steps) data point from
    manage.py benchmark_render_times -- the raw sweep data used to figure
    out what's actually viable (and how long it takes) before curating
    RenderPreset rows from it. Deliberately a separate model from
    RenderPreset: this can hold many more combinations than you'd ever want
    to expose to users directly, including ones that failed.
    """

    class Status(models.TextChoices):
        OK = "ok", "OK"
        OOM_ERROR = "oom_error", "OOM / execution error"
        TIMEOUT = "timeout", "Timed out"
        CRASHED = "crashed", "ComfyUI unreachable (crashed)"

    mode = models.CharField(max_length=8, choices=Mode.choices)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    duration_seconds = models.FloatField()
    steps = models.PositiveIntegerField()

    status = models.CharField(max_length=16, choices=Status.choices)
    render_seconds = models.FloatField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")
    comfyui_prompt_id = models.CharField(max_length=64, blank=True, default="")
    tested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["mode", "width", "height", "duration_seconds"]
        constraints = [
            models.UniqueConstraint(
                fields=["mode", "width", "height", "duration_seconds", "steps"],
                name="unique_benchmark_combo",
            )
        ]

    def __str__(self) -> str:
        return f"{self.get_mode_display()} {self.width}x{self.height} {self.duration_seconds}s -> {self.status}"
