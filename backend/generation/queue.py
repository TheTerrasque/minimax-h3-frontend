"""Cross-user queue ETA estimation, per features.md item 5.

Deliberately returns only aggregates/derived timestamps -- never other
users' individual job details -- since "should not show queue details from
other users, just a combined estimated finished time." expected_finish_times()
computes a real per-job number, but generation/api.py only ever attaches it
to the requesting user's own jobs when serializing a response; the dict
itself covers every user's active jobs since the FIFO order (and therefore
each job's timing) genuinely depends on all of them, not just one user's.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.db.models import Sum
from django.utils import timezone

from .models import GenerationJob

_ACTIVE_STATUSES = [GenerationJob.Status.QUEUED, GenerationJob.Status.PROCESSING]


def estimated_seconds_ahead() -> int:
    """Sum of estimated_seconds for every job still queued or processing,
    system-wide. Adding a new job's own estimated_seconds to this gives the
    ETA to show before the user confirms queuing it.
    """
    total = GenerationJob.objects.filter(status__in=_ACTIVE_STATUSES).aggregate(
        total=Sum("estimated_seconds")
    )["total"]
    return total or 0


def estimated_finish_time(additional_seconds: int):
    return timezone.now() + timedelta(seconds=estimated_seconds_ahead() + additional_seconds)


def expected_finish_times() -> dict[int, datetime]:
    """Per-job expected finish time for every job still queued/processing,
    system-wide, walked in the same FIFO order tasks.process_queue()'s
    _claim_next_job() claims them in (created_at, id) -- a PROCESSING job
    (there's at most one, by construction, see tasks.py) is always the
    oldest active job, so a plain created_at/id ordering already puts it
    first without needing to special-case it in the query.

    Each job's expected finish is the previous job's expected finish plus
    its own estimated_seconds; the PROCESSING job's expected finish is its
    own started_at plus estimated_seconds (rather than chaining off "now",
    since it already started).
    """
    jobs = list(
        GenerationJob.objects.filter(status__in=_ACTIVE_STATUSES).order_by("created_at", "id")
    )
    cursor = timezone.now()
    result: dict[int, datetime] = {}
    for job in jobs:
        if job.status == GenerationJob.Status.PROCESSING and job.started_at:
            cursor = job.started_at + timedelta(seconds=job.estimated_seconds)
        else:
            cursor = cursor + timedelta(seconds=job.estimated_seconds)
        result[job.id] = cursor
    return result
