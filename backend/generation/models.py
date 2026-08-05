from django.conf import settings
from django.db import models


class Mode(models.TextChoices):
    """The three MiniMax H3 workflows in resources/workflows/."""

    TEXT_TO_VIDEO = "t2v", "Video from text"
    IMAGE_TO_VIDEO = "i2v", "Provide first frame"
    REFERENCE_TO_VIDEO = "r2v", "Provide references"


class RenderPreset(models.Model):
    """Admin-editable (mode, resolution, duration, steps) -> estimated render time.

    Backs features.md item 4 ("internal list of supported resolutions and
    seconds for each mode, with estimated time to render"). GenerationJob
    snapshots estimated_render_seconds at creation time so later edits here
    don't retroactively change ETAs already shown to a user.

    "Draft mode" (fast, low-res, low-step passes to sanity-check a prompt
    before committing to a full render) is just another preset row here --
    e.g. is_draft=True, ~0.2 megapixels, few steps -- rather than a separate
    model/pipeline; is_draft only exists so the frontend can group/label
    these separately from "real" presets.
    """

    mode = models.CharField(max_length=8, choices=Mode.choices)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    duration_seconds = models.FloatField(help_text="Requested clip length, in seconds.")
    steps = models.PositiveIntegerField(default=20, help_text="Sampler steps (BasicScheduler.steps).")
    estimated_render_seconds = models.PositiveIntegerField(
        help_text="Expected wall-clock render time for this combination."
    )
    is_draft = models.BooleanField(
        default=False,
        help_text="Fast/low-quality preset meant for previewing a prompt, not a final render.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["mode", "is_draft", "width", "height", "duration_seconds"]

    def __str__(self) -> str:
        draft = " (draft)" if self.is_draft else ""
        return f"{self.get_mode_display()} {self.width}x{self.height} {self.duration_seconds}s{draft}"


class GenerationJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="generation_jobs"
    )
    mode = models.CharField(max_length=8, choices=Mode.choices)
    preset = models.ForeignKey(RenderPreset, on_delete=models.PROTECT, related_name="jobs")

    raw_prompt = models.TextField()
    improved_prompt = models.TextField(blank=True, default="")

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    estimated_seconds = models.PositiveIntegerField(
        help_text="Snapshot of preset.estimated_render_seconds at queue time."
    )

    # django-q2 task id, so a job can be looked up/cancelled via the cluster.
    q_task_id = models.CharField(max_length=64, blank=True, default="")

    # ComfyUI-side identifiers, see resources/COMFYUI_API_GUIDE.md.
    comfyui_prompt_id = models.CharField(max_length=64, blank=True, default="")
    video_file = models.FileField(upload_to="generated_videos/%Y/%m/", blank=True)
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
    file = models.FileField(upload_to="references/%Y/%m/")
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
    """An interactive conversation helping a user draft/refine a prompt --
    only ever created when settings.LLM_ENABLED, see generation/api.py.
    Persisted (rather than kept client-side) so it survives a page refresh
    and gives an audit trail of LLM usage; resulting_job links it to
    whatever GenerationJob the final prompt was actually used for, once one
    exists.
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
