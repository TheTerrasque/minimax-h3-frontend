"""Director Mode: chaining multiple GenerationJob renders into one ordered
sequence of Clips, with a shared Project-level prompt/resources and
motion-continuity between adjacent Clips flagged as continuations of each
other (see integrations/motion_context.py, extras.md). See the approved
plan for the full design -- this module is deliberately just the data model
+ dirty-cascade rule; rendering itself lives in services.py/tasks.py and the
API surface in api.py, kept separate so this stays easy to read on its own.

Layering: this app depends on generation (GenerationJob, RenderPreset,
RenderDuration, Mode, ReferenceAsset's Kind/label convention) -- generation
never imports director, see apps.py's ready()/signals.py for the one seam
that runs the other way (a Django signal, not an import).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from generation.models import Mode, RenderDuration, RenderPreset, _random_upload_path
from generation.models import GenerationJob, ReferenceAsset

# Continuation is only meaningful for modes whose sampler node actually
# produces a conditioning+latent pair MiniMaxH3MotionContext can consume --
# confirmed against the extension's example workflow (see the plan's
# "Extension research" section). t2v's sampler has nothing for it to
# continue *from* in a meaningful sense (no image/reference anchor), so it's
# excluded here rather than only in the frontend -- see Clip.clean() below.
CONTINUATION_CAPABLE_MODES = {Mode.IMAGE_TO_VIDEO, Mode.REFERENCE_TO_VIDEO}


def project_resource_upload_path(instance, filename: str) -> str:
    return _random_upload_path("director_resources", filename)


def clip_reference_upload_path(instance, filename: str) -> str:
    return _random_upload_path("director_clip_references", filename)


class Project(models.Model):
    """One "movie": a title, a shared prompt/resource context every Clip
    draws on, and an ordered sequence of Clips (see Clip.order). Editing
    overarching_prompt (or its resources) invalidates every Clip in the
    project -- see services.mark_project_dirty().
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="director_projects")
    title = models.CharField(max_length=200, blank=True, default="")
    overarching_prompt = models.TextField(
        blank=True,
        default="",
        help_text="Shared world/setting/character context prose, given to every Clip's render "
        "and to the LLM prompt-assist calls made against this project's clips (see "
        "integrations/llm.py's extra_context).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return self.title or f"Project({self.id})"


class ProjectResource(models.Model):
    """A character sheet / voice reference / world-reference image, audio,
    or video clip shared by every Clip in the project -- distinct from a
    per-Clip ClipReferenceAsset, which only that one Clip's render sees.
    """

    class Kind(models.TextChoices):
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"
        AUDIO = "audio", "Audio"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="resources")
    kind = models.CharField(max_length=8, choices=Kind.choices)
    order = models.PositiveIntegerField(default=0)
    file = models.FileField(upload_to=project_resource_upload_path)
    label = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text='e.g. "Alice — character sheet". Blank falls back to the same '
        '"Picture N"/"Video N"/"Audio N" token convention as ReferenceAsset.label.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["kind", "order"]

    def __str__(self) -> str:
        return self.label or self.token_label

    @property
    def token_label(self) -> str:
        """Falls back to ReferenceAsset's own "<Picture N>" token convention
        (see that model's .label) when no human label is set, scoped to
        this project rather than a job."""
        kind_labels = {self.Kind.IMAGE: "Picture", self.Kind.VIDEO: "Video", self.Kind.AUDIO: "Audio"}
        same_kind_ids = list(
            self.project.resources.filter(kind=self.kind).order_by("order", "id").values_list("id", flat=True)
        )
        position = same_kind_ids.index(self.id) + 1 if self.id in same_kind_ids else 1
        return f"{kind_labels[self.kind]} {position}"


class Clip(models.Model):
    """One box on the Director board -- a single GenerationJob-backed
    render, positioned in `order` within its Project. `continues_previous`
    means "splice motion/audio continuity from whichever Clip is
    immediately before me in `order`" (see integrations/motion_context.py)
    -- deliberately positional rather than an explicit FK to a specific
    predecessor, matching the user's own "video-editor timeline" framing:
    reordering the board is what changes what a continuation box continues
    from, and always marks it dirty (see services.py) rather than trying to
    preserve a now-stale relationship.
    """

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="clips")
    order = models.PositiveIntegerField()

    continues_previous = models.BooleanField(
        default=False,
        help_text="Splice motion/audio continuity from the immediately-preceding Clip's render. "
        "Only meaningful when mode is in CONTINUATION_CAPABLE_MODES and this isn't the "
        "project's first Clip -- enforced in the API layer, not here.",
    )
    mode = models.CharField(max_length=8, choices=Mode.choices)
    prompt = models.TextField(blank=True, default="")
    improved_prompt = models.TextField(blank=True, default="")

    preset = models.ForeignKey(RenderPreset, on_delete=models.PROTECT, related_name="director_clips")
    duration = models.ForeignKey(RenderDuration, on_delete=models.PROTECT, related_name="director_clips")
    aspect_ratio = models.CharField(
        max_length=10,
        help_text="Same shape as GenerationJob.aspect_ratio. Locked to the predecessor's value "
        "when continues_previous -- context frames must match resolution.",
    )
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()

    needs_render = models.BooleanField(
        default=True,
        help_text="Dirty flag -- shown as the red border on the board. See services.py's "
        "mark_dirty_cascade()/mark_project_dirty() for the only places this is set True, "
        "and the job_finished signal receiver in signals.py for the only place it's set False.",
    )
    render_chain_target = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Set on the *head* Clip of an in-flight chain-render request to the Clip the "
        "user actually asked to render -- signals.py's auto-advance walks forward "
        "creating each next continuation Clip's job as its predecessor finishes, until "
        "it reaches this target (or a gap/failure stops it). Null when idle.",
    )
    current_job = models.ForeignKey(
        GenerationJob, on_delete=models.SET_NULL, null=True, blank=True, related_name="director_clip"
    )
    checkpoint_filename_prefix = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Set once this Clip successfully renders -- the filename_prefix passed to "
        "MiniMaxH3MotionContextSaveLatent, consumed by the *next* continuation Clip's "
        "MiniMaxH3MotionContextLoadLatent. Lives entirely as ComfyUI-side state (a "
        "safetensors file on its own disk) -- this is just the string needed to find it "
        "again, never any latent bytes.",
    )
    checkpoint_clip_index = models.PositiveIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["project", "order"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "order"],
                name="unique_project_clip_order",
                # Checked at transaction commit, not per-statement -- a
                # reorder (see director/api.py's reorder_clip) writes every
                # affected sibling's new `order` one row at a time inside
                # one atomic block, which otherwise collides mid-loop
                # whenever two rows' positions swap (row A's new order
                # temporarily equals row B's still-old order). Postgres-only
                # feature (this deployment's only backend, see
                # docker-compose.yml) -- Django's deferrable constraints
                # aren't supported on SQLite/older MySQL.
                deferrable=models.Deferrable.DEFERRED,
            )
        ]

    def __str__(self) -> str:
        return f"Clip({self.id}, project={self.project_id}, order={self.order})"


class ClipReferenceAsset(models.Model):
    """Per-Clip reference image/audio/video -- same shape as
    generation.models.ReferenceAsset, but scoped to a Clip instead of a
    GenerationJob (a Clip only gets a real GenerationJob once it actually
    renders, see services.py).
    """

    clip = models.ForeignKey(Clip, on_delete=models.CASCADE, related_name="references")
    kind = models.CharField(max_length=8, choices=ReferenceAsset.Kind.choices)
    order = models.PositiveIntegerField(default=0)
    file = models.FileField(upload_to=clip_reference_upload_path)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["kind", "order"]

    def __str__(self) -> str:
        return self.label

    @property
    def label(self) -> str:
        """Same "<Picture N>"/"<Video N>"/"<Audio N>" token convention as
        ReferenceAsset.label, scoped to this clip."""
        kind_labels = {
            ReferenceAsset.Kind.IMAGE: "Picture",
            ReferenceAsset.Kind.VIDEO: "Video",
            ReferenceAsset.Kind.AUDIO: "Audio",
        }
        same_kind_ids = list(
            self.clip.references.filter(kind=self.kind).order_by("order", "id").values_list("id", flat=True)
        )
        position = same_kind_ids.index(self.id) + 1 if self.id in same_kind_ids else 1
        return f"{kind_labels[self.kind]} {position}"
