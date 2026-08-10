"""Connects generation.signals.job_finished to Director Mode's clip-chain
state -- the one place director reaches into generation's lifecycle,
without generation needing to know director exists (see generation/
signals.py's own docstring, DirectorConfig.ready()).
"""

from __future__ import annotations

from django.dispatch import receiver

from generation.models import GenerationJob
from generation.signals import job_finished

from .models import Clip
from .services import _build_job_for_clip


def _advance_chain(finished_clip: Clip) -> None:
    """Called after `finished_clip`'s job succeeds -- if it was rendered as
    part of a chain-render request (see services.render_clip()), creates
    the next continuation Clip's job too, carrying the chain target
    forward, until that target is reached or a gap/already-clean Clip stops
    it. finished_clip.render_chain_target is always cleared by the caller
    once this returns, regardless of whether it advanced anything."""
    target_id = finished_clip.render_chain_target_id
    if target_id is None or finished_clip.id == target_id:
        return

    next_clip = (
        Clip.objects.filter(project_id=finished_clip.project_id, order__gt=finished_clip.order)
        .order_by("order")
        .first()
    )
    if next_clip is None or not next_clip.continues_previous or not next_clip.needs_render:
        # Gap (deleted/reordered), a fresh scene, or already clean -- the
        # chain stops here; whatever asked for `target_id` will need a new
        # render request if it still wants it.
        return

    next_clip.render_chain_target_id = target_id
    next_clip.save(update_fields=["render_chain_target"])
    _build_job_for_clip(next_clip)


@receiver(job_finished)
def on_job_finished(sender, job, **kwargs) -> None:
    clip = Clip.objects.select_related("project").filter(current_job=job).first()
    if clip is None:
        return  # not a director job

    succeeded = job.status == GenerationJob.Status.DONE and not job.error_message
    if succeeded:
        clip.needs_render = False
        clip.save(update_fields=["needs_render"])
        _advance_chain(clip)
    # Failure or cancellation: leave needs_render=True so it's still shown
    # dirty/re-renderable; either way the chain (if any) stops at this Clip.
    Clip.objects.filter(pk=clip.pk).update(render_chain_target=None)
