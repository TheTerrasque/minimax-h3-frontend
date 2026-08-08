"""Best-effort attempt to backfill GenerationJob.thumbnail_file for existing
done video-content-type jobs, via the reusable `backfill_thumbnails`
management command (generation/management/commands/backfill_thumbnails.py) --
see that command's docstring for the actual logic.

Deliberately best-effort, not authoritative: this needs real access to the
media files, which the one-shot `migrate` service may not have depending on
deployment (see docker-compose.yml's migrate service comment) -- if this
whole step fails outright (not found, permission error, no ffmpeg, etc.),
it's swallowed here rather than blocking the rest of the deploy, with a
pointer to run the command manually against a container that does have
media access (backend/qcluster both mount it). The command itself already
handles per-job failures gracefully; this only guards against the command
being unable to run *at all* in this environment.
"""

from __future__ import annotations

from django.db import migrations


def backfill_thumbnails(apps, schema_editor):
    from django.core.management import call_command

    try:
        call_command("backfill_thumbnails")
    except Exception as exc:  # noqa: BLE001 -- see module docstring
        print(
            "  backfill_video_thumbnails: couldn't run here "
            f"({exc}) -- run `manage.py backfill_thumbnails` manually against a "
            "container with real media access (e.g. `docker compose exec backend ...`)."
        )


class Migration(migrations.Migration):

    dependencies = [
        ('generation', '0020_generationjob_title_thumbnail'),
    ]

    operations = [
        migrations.RunPython(backfill_thumbnails, reverse_code=migrations.RunPython.noop),
    ]
