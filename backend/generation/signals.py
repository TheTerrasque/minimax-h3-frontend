"""Signals generation emits for other apps to react to, without generation
itself needing to know who's listening. Currently one: job_finished, sent
by tasks.py._execute_job() at the very end of every job (success, failure,
*and* cancellation alike -- receivers check job.status/error_message to
tell them apart, same as everywhere else in this codebase that already
does). director/signals.py is the one receiver today (see that module and
DirectorConfig.ready()), driving clip-chain state/auto-advance -- but
nothing here references director, keeping the dependency one-directional.
"""

from __future__ import annotations

import django.dispatch

# providing_args removed in modern Django -- documented here instead: sends
# a single `job` kwarg, the just-finished GenerationJob instance.
job_finished = django.dispatch.Signal()
