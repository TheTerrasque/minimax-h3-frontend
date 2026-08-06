# Snapshots RenderPreset.steps onto GenerationJob at queue time, matching
# the existing megapixels/aspect_ratio/width/height/duration_seconds
# snapshot fields (see GenerationJob's own field comments) -- needed by
# the admin catalog's curve-fit estimator (generation/admin_api.py),
# which now pools completed jobs across quality levels and needs each
# job's *actual* steps count to compute its workload
# (steps * megapixels * duration_seconds). Without this, the estimator
# would have to read RenderPreset.steps live, which silently mis-attributes
# old completed jobs to whatever steps count the preset happens to have
# *now* if an admin has since edited it through the catalog tool.

from django.db import migrations, models


def backfill_steps(apps, schema_editor):
    GenerationJob = apps.get_model("generation", "GenerationJob")
    for job in GenerationJob.objects.select_related("preset").all():
        job.steps = job.preset.steps
        job.save(update_fields=["steps"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("generation", "0013_renderpreset_sort_order"),
    ]

    operations = [
        migrations.AddField(
            model_name="generationjob",
            name="steps",
            field=models.PositiveIntegerField(
                default=20,
                help_text="Snapshot of preset.steps at queue time -- also used as the "
                "workload dimension (steps * megapixels * duration_seconds) by the admin "
                "catalog's curve-fit estimator, see generation/admin_api.py.",
            ),
        ),
        migrations.RunPython(backfill_steps, noop),
    ]
