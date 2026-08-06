"""Auth+ownership-gated serving of GenerationJob.video_file/ReferenceAsset.file.

Everything under MEDIA_ROOT used to be served by a bare, unconditional
django.views.static.serve mount (see config/urls.py's history) -- that has
no concept of who's asking, so once filenames stopped being predictable
(see generation/models.py's upload_to callables), the remaining gap was
that *any* URL, guessed or not, still worked for anyone, logged in or not.
This wraps the same underlying django.views.static.serve (keeping its
Range/ETag/conditional-GET handling intact -- needed for <video> seeking)
behind a check that the requesting user actually owns the GenerationJob or
ReferenceAsset the requested path resolves to.

Deliberately not nginx X-Accel-Redirect: that would need frontend (nginx)
to also mount the media_data volume (it currently has zero access to app
data, on purpose) and a path-translation contract between the two
containers, for a performance win this app's actual scale (small,
invite-only) doesn't need. A plain Django-served response is the simplest
correct option, matching this project's existing bias for that (see
ARCHITECTURE.md's Docker Compose service graph on whitenoise).
"""

from __future__ import annotations

from django.http import Http404, HttpRequest, HttpResponse
from django.views.static import serve as serve_static

from .models import GenerationJob, ReferenceAsset


def _owner_id_for_path(path: str) -> int | None:
    if path.startswith("generated_videos/"):
        return GenerationJob.objects.filter(video_file=path).values_list("user_id", flat=True).first()
    if path.startswith("references/"):
        return ReferenceAsset.objects.filter(file=path).values_list("job__user_id", flat=True).first()
    return None


def serve_protected_media(request: HttpRequest, path: str, document_root: str) -> HttpResponse:
    """Drop-in replacement for django.views.static.serve, gated by
    ownership. 404 (not 403) for both "not logged in" and "logged in as
    someone else" -- same not-found-rather-than-forbidden convention
    generation/api.py already uses for cross-user job access, so this
    doesn't confirm a given path even exists to someone who isn't its owner.
    """
    if not request.user.is_authenticated:
        raise Http404
    owner_id = _owner_id_for_path(path)
    if owner_id is None:
        raise Http404
    # Staff can view any user's files (matches /admin/ itself already
    # exposing every job/reference row -- the admin's own FileField widgets
    # link straight to these same URLs, so blocking staff here would just
    # break those links without adding any real protection: staff already
    # has full DB read access).
    if owner_id != request.user.id and not request.user.is_staff:
        raise Http404
    return serve_static(request, path, document_root=document_root)
