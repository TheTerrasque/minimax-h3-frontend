# Seeds an initial RenderPreset/RenderDuration catalog for the four new
# image/audio modes (t2i/r2i/t2a/r2a) added by the previous migration --
# without this they'd have no selectable quality/duration at all. Tier
# labels are deliberately distinct from every existing video-mode tier
# name (Draft/Lowest/Low/Medium/High/Max) -- the admin catalog groups
# RenderPreset rows into one "quality level" per matching label across
# modes (see admin_api.py), and image/audio's megapixels/steps scale mean
# something different from video's, so sharing a label would pair up
# rows that don't actually correspond.
#
# estimated_render_seconds values are anchored to real renders against the
# live ComfyUI instance during development (not benchmark_render_times.py --
# that command's sweep ranges assume video's shape, see its own --modes
# comment): image at 1.0/2.0MP measured 76s/94s (extrapolated to 4.0MP);
# audio at 32x32 measured ~62-94s across 5-30s duration and 10/20 steps.
# Rough, not from a real regression fit like the video tiers below 20
# real samples -- tune via admin once there's more real usage data.

from django.db import migrations

IMAGE_TIERS = [
    # (label, megapixels, steps, single duration's estimated_render_seconds)
    ("Standard", 1.0, 20, 75),
    ("Sharp", 2.0, 20, 95),
    ("Ultra", 4.0, 20, 135),
]
IMAGE_MODES = ["t2i", "r2i"]

# (label, steps, [(duration_seconds, estimated_render_seconds), ...])
AUDIO_TIERS = [
    ("Fast", 10, [(5.0, 62), (10.0, 65), (15.0, 69), (20.0, 72), (30.0, 79)]),
    ("Rich", 20, [(5.0, 68), (10.0, 71), (15.0, 75), (20.0, 78), (30.0, 86)]),
]
AUDIO_MODES = ["t2a", "r2a"]
# 32x32 (RESOLUTION_MULTIPLE's own floor, see resolution.py) at "1:1" --
# visual output is discarded (see integrations/media_post.py), so this is
# picked purely to make compute_resolution(megapixels, "1:1") land on
# exactly 32x32, not for any visual-quality reason.
AUDIO_MEGAPIXELS = 32 * 32 / 1_000_000


def seed(apps, schema_editor):
    RenderPreset = apps.get_model("generation", "RenderPreset")
    RenderDuration = apps.get_model("generation", "RenderDuration")

    for mode in IMAGE_MODES:
        for sort_order, (label, megapixels, steps, estimated_render_seconds) in enumerate(IMAGE_TIERS):
            preset = RenderPreset.objects.create(
                mode=mode, label=label, megapixels=megapixels, steps=steps, sort_order=sort_order
            )
            RenderDuration.objects.create(
                preset=preset, duration_seconds=0.0, estimated_render_seconds=estimated_render_seconds
            )

    for mode in AUDIO_MODES:
        for sort_order, (label, steps, durations) in enumerate(AUDIO_TIERS):
            preset = RenderPreset.objects.create(
                mode=mode, label=label, megapixels=AUDIO_MEGAPIXELS, steps=steps, sort_order=sort_order
            )
            for duration_seconds, estimated_render_seconds in durations:
                RenderDuration.objects.create(
                    preset=preset,
                    duration_seconds=duration_seconds,
                    estimated_render_seconds=estimated_render_seconds,
                )


def unseed(apps, schema_editor):
    RenderPreset = apps.get_model("generation", "RenderPreset")
    image_audio_labels = [t[0] for t in IMAGE_TIERS] + [t[0] for t in AUDIO_TIERS]
    RenderPreset.objects.filter(
        mode__in=IMAGE_MODES + AUDIO_MODES, label__in=image_audio_labels
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("generation", "0017_alter_benchmarkresult_mode_alter_generationjob_mode_and_more"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
