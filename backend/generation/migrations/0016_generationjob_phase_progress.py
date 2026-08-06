# Adds GenerationJob.phase/progress_current/progress_total -- live
# sub-state of a PROCESSING job, written by integrations/comfyui.py's
# stream_execution_progress() as it watches ComfyUI's WebSocket events, and
# read by QueueSidebar/JobModal's progress bar. Blank/null outside of an
# actual render; tasks.py resets all three back to blank on completion or
# failure.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("generation", "0015_aspect_ratio_custom"),
    ]

    operations = [
        migrations.AddField(
            model_name="generationjob",
            name="phase",
            field=models.CharField(
                blank=True,
                choices=[
                    ("preparing", "Preparing"),
                    ("rendering", "Rendering"),
                    ("finishing", "Finishing"),
                ],
                default="",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="generationjob",
            name="progress_current",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                help_text="Sampler step reached so far -- only set during Phase.RENDERING.",
            ),
        ),
        migrations.AddField(
            model_name="generationjob",
            name="progress_total",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                help_text="Total sampler steps for this job -- only set during Phase.RENDERING.",
            ),
        ),
    ]
