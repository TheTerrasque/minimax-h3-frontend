# Widens GenerationJob.aspect_ratio (was max_length=8, only ever holding one
# of resolution.ASPECT_RATIOS' fixed presets) to also fit a custom "W:H"
# ratio -- see resolution.is_valid_aspect_ratio() and
# frontend/src/features/generate/GenerateScreen.tsx's "match first frame"
# option, which computes an uploaded i2v first frame's own aspect ratio
# client-side rather than forcing it into the nearest preset.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("generation", "0014_generationjob_steps_snapshot"),
    ]

    operations = [
        migrations.AlterField(
            model_name="generationjob",
            name="aspect_ratio",
            field=models.CharField(
                max_length=10,
                help_text='e.g. "16:9" -- see resolution.ASPECT_RATIOS, or a custom "W:H" ratio '
                "(see resolution.is_valid_aspect_ratio) matching an uploaded first frame.",
            ),
        ),
    ]
