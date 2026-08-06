"""
Django settings for the MinimaxH3 Front backend.

See resources/COMFYUI_API_GUIDE.md and ARCHITECTURE.md for the surrounding
context. Config comes from environment variables (django-environ) -- see
.env.example at the repo root for the full list, consumed via docker-compose's
env_file: in normal (Docker) operation.
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent
RESOURCES_DIR = BASE_DIR / "resources"

env = environ.Env()
# Convenience for running manage.py directly against a local .env outside
# Docker (e.g. one-off checks); in the compose stack, env vars already come
# from env_file: and this is a no-op.
if (BASE_DIR / ".env").exists():
    environ.Env.read_env(str(BASE_DIR / ".env"))

SECRET_KEY = env("DJANGO_SECRET_KEY", default="django-insecure-dev-key-change-me")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    # third-party
    "rest_framework",
    "drf_spectacular",
    "django_q",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.openid_connect",
    # local
    "accounts",
    "generation",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Database -- Postgres in the compose stack; see docker-compose.yml's `db`
# service. Two separate containers (backend + qcluster) hit this
# concurrently, which is the reason it's Postgres rather than SQLite.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", default="mm_h3"),
        "USER": env("POSTGRES_USER", default="mm_h3"),
        "PASSWORD": env("POSTGRES_PASSWORD", default="mm_h3"),
        "HOST": env("DB_HOST", default="db"),
        "PORT": env("DB_PORT", default="5432"),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Password validation

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Internationalization

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


# Static & media files. nginx proxies /static/ and /media/ straight to this
# service (see frontend/nginx.conf) -- whitenoise serves static, Django
# serves media directly for now (see ARCHITECTURE.md "deferred").

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"


# Auth / allauth (OIDC login, session-cookie auth for the SPA -- see
# ARCHITECTURE.md "Why a reverse-proxy-fronted stack")

AUTH_USER_MODEL = "accounts.User"
SITE_ID = 1

# Both default to "/accounts/profile/", which doesn't exist here (Django
# serves a pure API + SPA, not server-rendered account pages) -- send the
# browser back to the SPA instead, both after login and after logout.
LOGIN_REDIRECT_URL = "/"
ACCOUNT_LOGOUT_REDIRECT_URL = "/"

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "optional"

# Invite-only signup -- see accounts/adapters.py and accounts/models.py::Invite.
ACCOUNT_ADAPTER = "accounts.adapters.NoSelfSignupAccountAdapter"
SOCIALACCOUNT_ADAPTER = "accounts.adapters.InviteGatedSocialAccountAdapter"

# Stable internal slug (used in URLs, e.g. reverse("openid_connect_login",
# kwargs={"provider_id": OIDC_PROVIDER_ID}) in accounts/views.py) -- distinct
# from OIDC_PROVIDER_NAME below, which is just the human-readable label.
OIDC_PROVIDER_ID = "oidc"

# Populated only when an OIDC provider is actually configured, so the app
# still boots cleanly in early dev before an IdP is wired up.
_oidc_client_id = env("OIDC_CLIENT_ID", default="")
SOCIALACCOUNT_PROVIDERS = {
    "openid_connect": {
        "APPS": (
            [
                {
                    "provider_id": OIDC_PROVIDER_ID,
                    "name": env("OIDC_PROVIDER_NAME", default="OIDC"),
                    "client_id": _oidc_client_id,
                    "secret": env("OIDC_CLIENT_SECRET", default=""),
                    "settings": {
                        "server_url": env("OIDC_ISSUER_URL", default=""),
                    },
                }
            ]
            if _oidc_client_id
            else []
        ),
    }
}


# Django REST Framework -- session auth only (see ARCHITECTURE.md; no CORS/
# JWT needed because nginx makes the SPA same-origin with the API).

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

# drf-spectacular -- auto-generated OpenAPI schema + browsable docs (see
# config/urls.py: /api/schema/, /api/schema/swagger-ui/, /api/schema/redoc/).
# Generated from the actual views, so it can't drift from reality the way
# hand-written API docs would; views use @extend_schema (generation/api.py)
# to describe request/response bodies since they're plain @api_view
# functions with manual dict validation, not DRF Serializers.
SPECTACULAR_SETTINGS = {
    "TITLE": "MinimaxH3 Front API",
    "DESCRIPTION": "Backend API for the MiniMax H3 ComfyUI video-generation frontend.",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
}


# Django-Q2 -- ORM broker, no Redis/RabbitMQ. Deliberately 1 worker: jobs
# are meant to be processed strictly one at a time, FIFO (see
# generation/tasks.py's process_queue()/module docstring) -- that ordering
# and one-at-a-time-ness is enforced there by an explicit DB claim query,
# not by Django-Q2 itself (its ORM broker's dequeue has no ORDER BY, so
# task pickup order isn't otherwise guaranteed), but a second worker slot
# would let two *different* jobs run in parallel regardless of claim order,
# which the DB-level row locking alone doesn't prevent. Don't raise this
# without redesigning that.
Q_CLUSTER = {
    "name": "mm_h3",
    "orm": "default",
    "workers": env.int("Q_CLUSTER_WORKERS", default=1),
    "timeout": 1200,
    "retry": 1500,
    "queue_limit": 50,
    "bulk": 1,
    "catch_up": False,
}


# ComfyUI / LLM integration endpoints -- see integrations/comfyui.py,
# integrations/llm.py, and resources/COMFYUI_API_GUIDE.md. Configured here
# per features.md item 12 ("Endpoints for comfyui and llm should be
# configured in django settings").
COMFYUI_BASE_URL = env("COMFYUI_BASE_URL", default="http://host.docker.internal:8000")
COMFYUI_OUTPUT_ROOT = env("COMFYUI_OUTPUT_ROOT", default="")

LLM_API_BASE_URL = env("LLM_API_BASE_URL", default="")
# Optional -- many self-hosted OpenAI-compatible servers (llama.cpp server,
# LM Studio, text-generation-webui, vLLM in permissive mode, etc.) don't
# require one at all. Sent as a Bearer token only when actually set (see
# integrations/llm._post_chat_completion()).
LLM_API_KEY = env("LLM_API_KEY", default="")
LLM_MODEL = env("LLM_MODEL", default="")
# LLM integration is entirely optional -- when either of the two *required*
# vars above (base URL, model) is unset, no AI features (refine button,
# chat) should be offered at all. LLM_API_KEY is deliberately not part of
# this gate -- it's optional, not required, see its own comment above. The
# frontend reads this via GET /api/config/; see integrations/llm.is_configured().
LLM_ENABLED = bool(LLM_API_BASE_URL and LLM_MODEL)

# Off by default -- sends actual reference image bytes to the LLM as vision
# content parts (see integrations/llm.chat_reply()) instead of just their
# <Picture N> labels. Only turn this on if LLM_MODEL is actually a
# vision-capable model; a text-only model receiving image_url content parts
# may error or silently ignore them, and it's real extra bandwidth/tokens
# either way.
LLM_VISION_ENABLED = env.bool("LLM_VISION_ENABLED", default=False)

# Optional pre/post hooks around the LLM call and a job's render -- dotted
# Python paths (same convention as ACCOUNT_ADAPTER above), each resolving to
# a callable(**context) -- see integrations/hooks.py for the exact context
# each one gets and run_hook()'s error handling. All four default to unset
# (no-op). Meant for site-specific glue that doesn't belong in the shipped
# codebase -- e.g. PRE_LLM_HOOK waking a model server before the first call,
# or POST_RENDER_HOOK pushing a desktop/phone notification when a render
# finishes. See backend/hooks_example.py for a starting template.
PRE_LLM_HOOK = env("PRE_LLM_HOOK", default="")
POST_LLM_HOOK = env("POST_LLM_HOOK", default="")
PRE_RENDER_HOOK = env("PRE_RENDER_HOOK", default="")
POST_RENDER_HOOK = env("POST_RENDER_HOOK", default="")
