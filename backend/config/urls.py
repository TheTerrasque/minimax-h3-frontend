from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from rest_framework.permissions import AllowAny

from director.media_views import serve_protected_media as serve_protected_director_media
from generation.media_views import serve_protected_media

urlpatterns = [
    path("admin/", admin.site.urls),
    # Media (uploaded reference assets + generated videos) served directly
    # by Django -- see ARCHITECTURE.md "Docker Compose service graph". This
    # was previously never actually wired up here at all (not a DEBUG-only
    # omission -- django.contrib.staticfiles never auto-serves MEDIA_ROOT,
    # only django.contrib.staticfiles.urls does that for STATIC_ROOT), so
    # every video_url the API returned 404'd. Unconditional (not gated on
    # DEBUG) since this deployment has no separate media server yet.
    # serve_protected_media (not a bare django.views.static.serve) checks
    # the requesting user actually owns the job/reference the path resolves
    # to -- see that module's docstring for why this exists.
    #
    # director's own file kinds (ProjectResource/ClipReferenceAsset) live
    # under distinct path prefixes and are handled by director's own
    # ownership check (see director/media_views.py) -- listed first since
    # Django tries re_paths in order and this is the more specific match;
    # everything else still falls through to generation's catch-all below.
    re_path(
        rf"^{settings.MEDIA_URL.lstrip('/')}(?P<path>director_(?:resources|clip_references|assembled_videos)/.*)$",
        serve_protected_director_media,
        {"document_root": settings.MEDIA_ROOT},
    ),
    re_path(
        rf"^{settings.MEDIA_URL.lstrip('/')}(?P<path>.*)$",
        serve_protected_media,
        {"document_root": settings.MEDIA_ROOT},
    ),
    path("", include("accounts.urls")),
    path("accounts/", include("allauth.urls")),
    path("api/", include("accounts.api_urls")),
    path("api/", include("generation.urls")),
    path("api/", include("director.urls")),
    # API docs -- browsable without login (the schema itself isn't
    # sensitive); the endpoints it documents still require auth to call.
    path(
        "api/schema/",
        SpectacularAPIView.as_view(permission_classes=[AllowAny]),
        name="schema",
    ),
    path(
        "api/schema/swagger-ui/",
        SpectacularSwaggerView.as_view(url_name="schema", permission_classes=[AllowAny]),
        name="swagger-ui",
    ),
    path(
        "api/schema/redoc/",
        SpectacularRedocView.as_view(url_name="schema", permission_classes=[AllowAny]),
        name="redoc",
    ),
]
