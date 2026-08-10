"""Auth+ownership-gated serving of ProjectResource.file/ClipReferenceAsset.file
-- same shape and reasoning as generation/media_views.py's
serve_protected_media(), duplicated rather than extended there to keep
generation ignorant of director (see director/apps.py's ready()); wired in
ahead of generation's own catch-all in config/urls.py so these two path
prefixes are handled here instead.
"""

from __future__ import annotations

from django.http import Http404, HttpRequest, HttpResponse
from django.views.static import serve as serve_static

from .models import ClipReferenceAsset, Project, ProjectResource


def _owner_id_for_path(path: str) -> int | None:
    if path.startswith("director_resources/"):
        return ProjectResource.objects.filter(file=path).values_list("project__user_id", flat=True).first()
    if path.startswith("director_clip_references/"):
        return (
            ClipReferenceAsset.objects.filter(file=path)
            .values_list("clip__project__user_id", flat=True)
            .first()
        )
    if path.startswith("director_assembled_videos/"):
        return Project.objects.filter(assembled_video_file=path).values_list("user_id", flat=True).first()
    return None


def serve_protected_media(request: HttpRequest, path: str, document_root: str) -> HttpResponse:
    if not request.user.is_authenticated:
        raise Http404
    owner_id = _owner_id_for_path(path)
    if owner_id is None:
        raise Http404
    if owner_id != request.user.id and not request.user.is_staff:
        raise Http404
    return serve_static(request, path, document_root=document_root)
