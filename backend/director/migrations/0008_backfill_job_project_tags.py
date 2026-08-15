# Backfills JobProjectTag for every Clip.current_job association that's
# still traceable at migration time -- see JobProjectTag's own docstring
# on why this table exists and what it deliberately can't recover: a job
# already orphaned (superseded by a later re-render) before this
# migration ran has no surviving trace of which project it came from,
# since Clip.current_job only ever remembers the *current* job, not its
# history. This only stops the bleeding going forward for jobs that are
# still a Clip's current_job right now.

from django.db import migrations


def backfill(apps, schema_editor):
    Clip = apps.get_model("director", "Clip")
    JobProjectTag = apps.get_model("director", "JobProjectTag")
    clips = Clip.objects.filter(current_job__isnull=False).values_list("current_job_id", "project_id")
    JobProjectTag.objects.bulk_create(
        [JobProjectTag(job_id=job_id, project_id=project_id) for job_id, project_id in clips],
        ignore_conflicts=True,
    )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("director", "0007_jobprojecttag"),
    ]

    operations = [
        migrations.RunPython(backfill, noop_reverse),
    ]
