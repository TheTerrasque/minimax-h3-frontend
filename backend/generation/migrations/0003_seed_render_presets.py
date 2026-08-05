# Seeds a baseline set of RenderPresets so the app has something to offer
# out of the box. estimated_render_seconds values here are rough starting
# guesses (not yet benchmarked against real ComfyUI runs) -- tune them via
# Django admin once real render times are observed; nothing else depends on
# them being exact.

from django.db import migrations

# (mode, width, height, duration_seconds, steps, estimated_render_seconds, is_draft)
PRESETS = [
    ("t2v", 1344, 768, 5.0, 20, 180, False),
    ("t2v", 608, 320, 3.0, 8, 30, True),
    ("i2v", 1344, 768, 5.0, 20, 200, False),
    ("i2v", 608, 320, 3.0, 8, 35, True),
    ("r2v", 1344, 768, 5.0, 20, 220, False),
    ("r2v", 608, 320, 3.0, 8, 40, True),
]


def seed_presets(apps, schema_editor):
    RenderPreset = apps.get_model("generation", "RenderPreset")
    for mode, width, height, duration_seconds, steps, estimated_render_seconds, is_draft in PRESETS:
        RenderPreset.objects.get_or_create(
            mode=mode,
            width=width,
            height=height,
            duration_seconds=duration_seconds,
            is_draft=is_draft,
            defaults={"steps": steps, "estimated_render_seconds": estimated_render_seconds},
        )


def remove_presets(apps, schema_editor):
    RenderPreset = apps.get_model("generation", "RenderPreset")
    for mode, width, height, duration_seconds, steps, estimated_render_seconds, is_draft in PRESETS:
        RenderPreset.objects.filter(
            mode=mode,
            width=width,
            height=height,
            duration_seconds=duration_seconds,
            is_draft=is_draft,
        ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("generation", "0002_alter_renderpreset_options_renderpreset_is_draft_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_presets, remove_presets),
    ]
