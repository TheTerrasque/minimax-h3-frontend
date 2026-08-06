# Requested change: offer a tighter, closely-spaced spread of low-megapixel
# options (0.2/0.3/0.4/0.5/0.6) instead of 0009's widely-spaced Draft/
# Standard/High quality (0.2/1.0/2.0) tiers -- "for now", i.e. this is
# expected to be replaced again once real manage.py benchmark_render_times
# data exists for this range.
#
# Doesn't touch the existing 0.2MP "Draft" tier at all (per mode) -- it
# already matches the lowest of the new choices, and at least one real
# GenerationJob already references it (RenderPreset.jobs is PROTECT, so
# deleting a referenced row would fail loudly rather than orphan a job).
# Only the old 1.0MP/2.0MP tiers are dropped and replaced.
#
# estimated_render_seconds values for the new 0.3-0.6MP tiers are, like
# 0009's, rough unbenchmarked guesses -- tune via admin once real sweep data
# exists.

from django.db import migrations

# (label, megapixels, steps, is_draft, [(duration_seconds, estimated_render_seconds), ...])
NEW_TIERS = [
    ("Low", 0.3, 20, False, [(3.0, 140), (5.0, 200), (8.0, 300), (12.0, 430)]),
    ("Medium", 0.4, 20, False, [(3.0, 160), (5.0, 230), (8.0, 340), (12.0, 490)]),
    ("High", 0.5, 20, False, [(3.0, 180), (5.0, 260), (8.0, 380), (12.0, 550)]),
    ("Max", 0.6, 20, False, [(3.0, 200), (5.0, 290), (8.0, 420), (12.0, 610)]),
]
MODES = ["t2v", "i2v", "r2v"]
OLD_LABELS = ["Standard", "High quality"]


def forward(apps, schema_editor):
    RenderPreset = apps.get_model("generation", "RenderPreset")
    RenderDuration = apps.get_model("generation", "RenderDuration")

    RenderPreset.objects.filter(mode__in=MODES, label__in=OLD_LABELS).delete()

    for mode in MODES:
        for label, megapixels, steps, is_draft, durations in NEW_TIERS:
            preset = RenderPreset.objects.create(
                mode=mode, label=label, megapixels=megapixels, steps=steps, is_draft=is_draft
            )
            for duration_seconds, estimated_render_seconds in durations:
                RenderDuration.objects.create(
                    preset=preset,
                    duration_seconds=duration_seconds,
                    estimated_render_seconds=estimated_render_seconds,
                )


def backward(apps, schema_editor):
    RenderPreset = apps.get_model("generation", "RenderPreset")
    RenderPreset.objects.filter(mode__in=MODES, label__in=[t[0] for t in NEW_TIERS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("generation", "0009_seed_resolution_duration_catalog"),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]
