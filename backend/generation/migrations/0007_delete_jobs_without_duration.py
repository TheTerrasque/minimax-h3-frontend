# Split out of what's now 0008 (AlterField(duration, ..., null=False)) into
# its own migration/transaction: Postgres refuses an ALTER TABLE on a table
# that had a DELETE with pending FK-cascade trigger events earlier in the
# SAME transaction ("cannot ALTER TABLE ... because it has pending trigger
# events") -- hit this for real running the combined version. Each migration
# file is its own transaction, so this just needs to be a separate one that
# commits before 0008 runs.

from django.db import migrations


def delete_jobs_without_duration(apps, schema_editor):
    """0006 added GenerationJob.duration as nullable (with megapixels/
    aspect_ratio/width/height/duration_seconds all backed by throwaway
    temp defaults, see that migration) so it could apply against any
    pre-existing rows without a real value to backfill. Making it NOT NULL
    in 0008 needs those rows gone first -- they predate the new resolution/
    duration catalog entirely and never had meaningful values for any of
    these fields to begin with, so there's nothing worth preserving.
    """
    GenerationJob = apps.get_model("generation", "GenerationJob")
    GenerationJob.objects.filter(duration__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('generation', '0006_resolution_duration_catalog'),
    ]

    operations = [
        migrations.RunPython(delete_jobs_without_duration, migrations.RunPython.noop),
    ]
