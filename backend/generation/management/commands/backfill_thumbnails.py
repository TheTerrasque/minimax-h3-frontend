"""Generates GenerationJob.thumbnail_file for existing done video-content-
type jobs that don't have one yet -- see integrations/media_post.extract_thumbnail()
and generation/tasks.py's _finish_job_from_history() (the normal, per-job
path new renders already go through).

Needs real access to the actual media files -- run this against a container
that has the media volume mounted (backend/qcluster do; the one-shot
`migrate` service may not, depending on deployment -- see
docker-compose.yml's migrate service comment). Idempotent and safe to
re-run anytime: only touches jobs with thumbnail_file still blank, and a
failure on one job (missing/corrupt video file, ffmpeg error) never affects
any other -- just gets logged and skipped.

Usage:
    docker compose exec backend python manage.py backfill_thumbnails
    docker compose exec backend python manage.py backfill_thumbnails --dry-run
"""

from __future__ import annotations

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from generation.models import CONTENT_TYPE_BY_MODE, ContentType, GenerationJob
from integrations import media_post

_VIDEO_MODES = [mode for mode, content_type in CONTENT_TYPE_BY_MODE.items() if content_type == ContentType.VIDEO]


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="List what would be backfilled without doing it."
        )

    def handle(self, *args, **options):
        jobs = GenerationJob.objects.filter(
            status=GenerationJob.Status.DONE, mode__in=_VIDEO_MODES, thumbnail_file=""
        ).exclude(video_file="")

        if not jobs:
            self.stdout.write("Nothing to backfill -- every eligible job already has a thumbnail.")
            return

        if options["dry_run"]:
            self.stdout.write(f"Would backfill {jobs.count()} job(s): {', '.join(str(j.id) for j in jobs)}")
            return

        backfilled = 0
        for job in jobs:
            try:
                job.video_file.open("rb")
                try:
                    video_bytes = job.video_file.read()
                finally:
                    job.video_file.close()
                thumbnail_bytes = media_post.extract_thumbnail(video_bytes)
                job.thumbnail_file.save(f"{job.id}.png", ContentFile(thumbnail_bytes), save=False)
                job.save(update_fields=["thumbnail_file"])
                backfilled += 1
            except Exception as exc:  # noqa: BLE001 -- best-effort, see module docstring
                self.stdout.write(self.style.WARNING(f"  skipping job {job.id}: {exc}"))

        self.stdout.write(self.style.SUCCESS(f"Backfilled {backfilled}/{jobs.count()} job(s)."))
