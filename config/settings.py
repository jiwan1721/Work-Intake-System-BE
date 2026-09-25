"""Django settings for the AI-Assisted Work Intake System.

Everything that differs between environments comes from environment variables
(PLAN §11). There is no secret literal in this file: `DJANGO_SECRET_KEY` has a
development-only fallback that is refused when `DEBUG` is off.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

# python-dotenv is a dev convenience so `manage.py` works without exporting
# vars by hand. It is not in requirements.txt: under Docker Compose and in CI
# the environment is already populated, so the import is allowed to fail.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else default


# --- Core -------------------------------------------------------------------

DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "").strip()
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is false. "
            "See .env.example; never commit a real key."
        )
    SECRET_KEY = "django-insecure-dev-only-not-a-secret"

ALLOWED_HOSTS = [
    h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",") if h.strip()
]

# CORS (PLAN §11) — in debug mode allow all origins so the Vite dev server
# works without extra config. In production, set CORS_ALLOWED_ORIGINS explicitly.
if DEBUG:
    CORS_ALLOW_ALL_ORIGINS = True
else:
    CORS_ALLOWED_ORIGINS = [
        o.strip()
        for o in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",")
        if o.strip()
    ]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "django_currentuser",
    "rest_framework",
    "rest_framework_simplejwt",
    "drf_spectacular",
    "common",
    "users",
    "work_items",
]

if DEBUG:
    INSTALLED_APPS += ["django_extensions"]

AUTH_USER_MODEL = "users.User"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Tracks the authenticated user on each request so CurrentUserField
    # on BaseModel can record who created/modified each row.
    "django_currentuser.middleware.ThreadLocalUserMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

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

# --- Database ---------------------------------------------------------------

# Inside Docker the host is `db`; outside it is `localhost` (see .env.example).
DATABASES = {
    "default": dj_database_url.parse(
        os.environ.get("DATABASE_URL", "postgres://intake:intake@localhost:5432/intake"),
        conn_max_age=env_int("DB_CONN_MAX_AGE", 60),
        # Persistent connections outlive the server they point at. After a
        # database restart, failover or an idle timeout on a pooler, the first
        # request to reuse a dead connection fails with OperationalError —
        # which is a confusing 500 for whoever happens to arrive first. The
        # health check costs one cheap round trip per request and turns that
        # into a transparent reconnect.
        conn_health_checks=True,
    )
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- i18n / static ----------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# --- API (PLAN §8, §8.1) ----------------------------------------------------

REST_FRAMEWORK = {
    # URL path versioning: the version is visible in every log line and curl
    # command. There is deliberately no DEFAULT_VERSION, so an unversioned
    # /api/work-items 404s instead of silently resolving to v1.
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.NamespaceVersioning",
    "ALLOWED_VERSIONS": ["v1"],
    # Allows namespace-less routes (e.g. auth endpoints) to coexist with
    # versioned work_items routes without raising NotFound during schema generation.
    "DEFAULT_VERSION": "v1",
    "EXCEPTION_HANDLER": "work_items.api.exceptions.custom_exception_handler",
    "DEFAULT_PAGINATION_CLASS": "work_items.api.pagination.WorkItemPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "common.utils.authentication.CustomJWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}

# --- JWT (Simple JWT) --------------------------------------------------------
# Default: HS256 with DJANGO_SECRET_KEY.
# For RS256: set JWT_ALGORITHM=RS256 and point JWT_PRIVATE_KEY_PATH / JWT_PUBLIC_KEY_PATH
# at PEM files generated by `python manage.py generate_rsa_keys`.
_jwt_algorithm = os.environ.get("JWT_ALGORITHM", "HS256").upper()
_jwt_signing_key: str | bytes = SECRET_KEY
_jwt_verifying_key: str | bytes | None = None

if _jwt_algorithm == "RS256":
    _private_key_path = os.environ.get("JWT_PRIVATE_KEY_PATH", str(BASE_DIR / "key-files" / "private_key.pem"))
    _public_key_path = os.environ.get("JWT_PUBLIC_KEY_PATH", str(BASE_DIR / "key-files" / "public_key.pem"))
    try:
        _jwt_signing_key = Path(_private_key_path).read_bytes()
        _jwt_verifying_key = Path(_public_key_path).read_bytes()
    except FileNotFoundError as _e:
        raise ImproperlyConfigured(
            f"JWT_ALGORITHM=RS256 but key file not found: {_e}. "
            "Run: python manage.py generate_rsa_keys"
        ) from _e

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env_int("JWT_ACCESS_MINUTES", 60)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env_int("JWT_REFRESH_DAYS", 7)),
    "ROTATE_REFRESH_TOKENS": False,
    "BLACKLIST_AFTER_ROTATION": False,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": _jwt_algorithm,
    "SIGNING_KEY": _jwt_signing_key,
    "VERIFYING_KEY": _jwt_verifying_key,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "AUTH_TOKEN_CLASSES": ("rest_framework_simplejwt.tokens.AccessToken",),
    "TOKEN_TYPE_CLAIM": "token_type",
}

# When True, a token refresh invalidates the previous refresh token immediately
# (requires token_refresh_date to be updated on User — see CustomTokenRefreshSerializer).
INVALIDATE_TOKEN_ON_REFRESH = env_bool("JWT_INVALIDATE_ON_REFRESH", False)

# --- Email -------------------------------------------------------------------
# Console backend in dev (prints to terminal); switch to SMTP in production.

EMAIL_BACKEND = (
    "django.core.mail.backends.console.EmailBackend"
    if DEBUG
    else "django.core.mail.backends.smtp.EmailBackend"
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "noreply@workintake.local")

# Frontend origin used in password-reset emails.
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173")

SPECTACULAR_SETTINGS = {
    "TITLE": "AI-Assisted Work Intake System",
    "DESCRIPTION": (
        "Intake, AI triage and operator workflow for work items arriving from an external system."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": "/api/v1",
}

# --- Cache ------------------------------------------------------------------
# Default: in-process memory cache (fine for dev; doesn't survive restarts).
# Production: set CACHE_BACKEND + CACHE_LOCATION to use Redis, e.g.:
#   CACHE_BACKEND=django.core.cache.backends.redis.RedisCache
#   CACHE_LOCATION=redis://localhost:6379/1
CACHES = {
    "default": {
        "BACKEND": os.environ.get(
            "CACHE_BACKEND",
            "django.core.cache.backends.locmem.LocMemCache",
        ),
        "LOCATION": os.environ.get("CACHE_LOCATION", ""),
    }
}

# --- AI provider (PLAN §7, §11) ---------------------------------------------

AI_PROVIDER = os.environ.get("AI_PROVIDER", "mock").strip().lower()
AI_MODEL = os.environ.get("AI_MODEL", "").strip()
AI_API_KEY = os.environ.get("AI_API_KEY", "").strip()
AI_TIMEOUT_SECONDS = env_float("AI_TIMEOUT_SECONDS", 20.0)
AI_MAX_ATTEMPTS = env_int("AI_MAX_ATTEMPTS", 5)
# When True, any failure from the configured provider is silently caught and
# the mock is used instead. Useful in dev/demo when an API key is absent but
# you still want real-provider code paths exercised.
AI_FALLBACK_TO_MOCK = env_bool("AI_FALLBACK_TO_MOCK", False)

# LangChain provider — used when AI_PROVIDER=langchain.
# Selects which LLM backend LangChain routes to; AI_API_KEY and AI_MODEL are reused.
LANGCHAIN_BACKEND = os.environ.get("LANGCHAIN_BACKEND", "anthropic").strip().lower()

MOCK_AI_LATENCY_MS = env_int("MOCK_AI_LATENCY_MS", 800)
MOCK_AI_FAILURE_MODE = os.environ.get("MOCK_AI_FAILURE_MODE", "none").strip().lower()

# Optional shared secret for the external system's intake endpoint (BE-14).
# Empty means the endpoint is open, which is the default for local demos.
INTAKE_API_KEY = os.environ.get("INTAKE_API_KEY", "").strip()

# --- Logging ----------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "%(levelname)s %(asctime)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
}
