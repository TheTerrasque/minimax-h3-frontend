"""Cross-user queue ETA estimation, per features.md item 5.

Deliberately returns only an aggregate -- never other users' individual job
details -- since "should not show queue details from other users, just a
combined estimated finished time."
"""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Sum
from django.utils import timezone

from .models import GenerationJob


def estimated_seconds_ahead() -> int:
    """Sum of estimated_seconds for every job still queued or running,
    system-wide. Adding a new job's own estimated_seconds to this gives the
    ETA to show before the user confirms queuing it.
    """
    total = GenerationJob.objects.filter(
        status__in=[GenerationJob.Status.QUEUED, GenerationJob.Status.RUNNING]
    ).aggregate(total=Sum("estimated_seconds"))["total"]
    return total or 0


def estimated_finish_time(additional_seconds: int):
    return timezone.now() + timedelta(seconds=estimated_seconds_ahead() + additional_seconds)
