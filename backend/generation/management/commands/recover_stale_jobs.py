"""Run once at qcluster container startup (see docker-compose.yml's
qcluster command) so a job orphaned by a previous restart -- see
generation.tasks.recover_orphaned_processing_jobs()'s docstring -- gets
recovered immediately, rather than sitting stuck until someone happens to
queue a new job (process_queue() also calls this, but only runs when
triggered by a job creation).
"""

from django.core.management.base import BaseCommand

from generation.tasks import recover_orphaned_processing_jobs


class Command(BaseCommand):
    help = __doc__

    def handle(self, *args, **options):
        recover_orphaned_processing_jobs()
        self.stdout.write(self.style.SUCCESS("Done."))
