# Two requested changes to the render catalog:
#
# 1. A genuine, non-draft 0.2MP tier ("Standard") alongside the existing
#    Draft one (also 0.2MP, but 8 steps and is_draft=True) -- same
#    megapixels, real step count, for users who want the cheapest *real*
#    quality option rather than a fast preview.
# 2. Every integer duration from 2 to 20 seconds, for every tier (previously
#    a curated 3/5/8/12 spread). Deliberately ADDS the missing seconds
#    rather than deleting-and-reseeding existing RenderDuration rows: past
#    GenerationJob rows PROTECT their RenderDuration FK, so wiping and
#    recreating (this project's usual reseed pattern, see 0009/0010) would
#    fail once any job has actually been queued against one -- true here
#    for real by now, unlike earlier catalog passes done before any real
#    job existed.
#
# estimated_render_seconds values are still rough, unbenchmarked guesses
# (see RenderPreset's docstring) -- computed from a simple formula
# (overhead + steps-scaled megapixels*duration term) rather than picked by
# hand one at a time, now that this is 19 durations x 6 tiers x 3 modes.
# Loosely sanity-checked against two real (if noisy -- taken mid-incident,
# see ARCHITECTURE.md's Verification) data points: a 0.4MP/20-step/8s job's
# real elapsed time (~515s) landed close to this formula's prediction
# (~495s). Tune for real once manage.py benchmark_render_times has been run.

from django.db import migrations

MODES = ["t2v", "i2v", "r2v"]
ALL_DURATION_SECONDS = list(range(2, 21))  # 2..20 inclusive


def _estimate_render_seconds(megapixels: float, steps: int, duration_seconds: float) -> int:
    steps_factor = steps / 8
    return round(15 + steps_factor * megapixels * 60 * duration_seconds)


def seed(apps, schema_editor):
    RenderPreset = apps.get_model("generation", "RenderPreset")
    RenderDuration = apps.get_model("generation", "RenderDuration")

    for mode in MODES:
        preset, created = RenderPreset.objects.get_or_create(
            mode=mode, megapixels=0.2, is_draft=False,
            defaults={"label": "Standard", "steps": 20},
        )
        if not created and (preset.label != "Standard" or preset.steps != 20):
            preset.label = "Standard"
            preset.steps = 20
            preset.save(update_fields=["label", "steps"])

    for preset in RenderPreset.objects.filter(mode__in=MODES, is_active=True):
        existing_seconds = set(preset.durations.values_list("duration_seconds", flat=True))
        for sec in ALL_DURATION_SECONDS:
            if float(sec) in existing_seconds:
                continue
            RenderDuration.objects.create(
                preset=preset,
                duration_seconds=float(sec),
                estimated_render_seconds=_estimate_render_seconds(preset.megapixels, preset.steps, sec),
            )


def unseed(apps, schema_editor):
    # Only the new Standard preset is removed (and only if nothing came to
    # reference it) -- the added duration rows for pre-existing presets are
    # left in place, same PROTECT reasoning as above applies in reverse.
    RenderPreset = apps.get_model("generation", "RenderPreset")
    RenderPreset.objects.filter(mode__in=MODES, label="Standard", megapixels=0.2, is_draft=False).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("generation", "0011_alter_generationjob_video_file_and_more"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
