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
}


# Django-Q2 -- ORM broker, no Redis/RabbitMQ. Multiple workers is fine even
# though ComfyUI itself only renders one job at a time: it serializes
# /prompt submissions on its own end regardless of how many workers here are
# concurrently polling for results (see resources/COMFYUI_API_GUIDE.md #7).
Q_CLUSTER = {
    "name": "mm_h3",
    "orm": "default",
    "workers": env.int("Q_CLUSTER_WORKERS", default=4),
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
LLM_API_KEY = env("LLM_API_KEY", default="")
LLM_MODEL = env("LLM_MODEL", default="")
