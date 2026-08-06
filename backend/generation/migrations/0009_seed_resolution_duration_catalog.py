# Replaces the old flat (width, height, duration, estimated_seconds)
# RenderPreset rows -- schema-incompatible after 0006/0007's redesign into
# RenderPreset ("quality tier": megapixels + steps) x RenderDuration
# (selectable clip lengths per tier, each independently estimated) -- with a
# fresh seed of the new shape.
#
# estimated_render_seconds values below are STILL rough, mostly-unbenchmarked
# guesses (see RenderPreset's docstring) -- tune via admin once
# manage.py benchmark_render_times has real data. The one exception: the
# draft tier's numbers are anchored to a real data point (0.2MP/8 steps/3s
# actually took ~71s on the real ComfyUI instance, see ARCHITECTURE.md's
# "Verification" section) and extrapolated linearly from there; standard/high
# are scaled-up guesses, not measured.

from django.db import migrations

# (label, megapixels, steps, is_draft, [(duration_seconds, estimated_render_seconds), ...])
TIERS = [
    ("Draft", 0.2, 8, True, [(3.0, 70), (5.0, 100), (8.0, 150), (12.0, 210)]),
    ("Standard", 1.0, 20, False, [(3.0, 130), (5.0, 190), (8.0, 280), (12.0, 400)]),
    ("High quality", 2.0, 28, False, [(3.0, 220), (5.0, 320), (8.0, 470), (12.0, 680)]),
]
MODES = ["t2v", "i2v", "r2v"]


def seed(apps, schema_editor):
    RenderPreset = apps.get_model("generation", "RenderPreset")
    RenderDuration = apps.get_model("generation", "RenderDuration")

    # Old-schema rows are no longer meaningful under the new (megapixels,
    # steps) shape -- clear and reseed rather than trying to map them 1:1.
    # Safe: GenerationJob.preset/duration are PROTECT, so this only succeeds
    # if nothing actually references the old rows yet.
    RenderPreset.objects.all().delete()

    for mode in MODES:
        for label, megapixels, steps, is_draft, durations in TIERS:
            preset = RenderPreset.objects.create(
                mode=mode, label=label, megapixels=megapixels, steps=steps, is_draft=is_draft
            )
            for duration_seconds, estimated_render_seconds in durations:
                RenderDuration.objects.create(
                    preset=preset,
                    duration_seconds=duration_seconds,
                    estimated_render_seconds=estimated_render_seconds,
                )


def unseed(apps, schema_editor):
    RenderPreset = apps.get_model("generation", "RenderPreset")
    RenderPreset.objects.filter(mode__in=MODES, label__in=[t[0] for t in TIERS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("generation", "0008_generationjob_duration_required"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
