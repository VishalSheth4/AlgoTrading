"""
production_settings.py  —  Production overrides for the AlgoTrading dashboard.

Set the environment variable before starting:
    DJANGO_SETTINGS_MODULE=config.production_settings

Key env vars (set in .env or shell):
    DJANGO_SECRET_KEY   — required; generate with: python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
    ALLOWED_HOSTS       — comma-separated hostnames, e.g. "myserver.com,localhost"
    PORT                — server port (default 8765)
"""

import os
from config.settings import *  # noqa: F401,F403

# ── Security ───────────────────────────────────────────────────────────────────
DEBUG = False

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", SECRET_KEY)  # noqa: F405

_hosts = os.environ.get("ALLOWED_HOSTS", "")
ALLOWED_HOSTS = [h.strip() for h in _hosts.split(",") if h.strip()] or ["*"]

# ── WhiteNoise: serve static files from Django itself (no nginx required) ─────
MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",    # <- right after SecurityMiddleware
] + [m for m in MIDDLEWARE[2:] if "corsheaders" not in m]  # noqa: F405

STATIC_ROOT = BASE_DIR / "staticfiles"  # noqa: F405
STATICFILES_STORAGE = "whitenoise.storage.CompressedStaticFilesStorage"

# ── Logging ────────────────────────────────────────────────────────────────────
_LOG_DIR = BASE_DIR / "logs"  # noqa: F405
_LOG_DIR.mkdir(exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "{asctime} {levelname} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(_LOG_DIR / "django.log"),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 3,
            "formatter": "verbose",
        },
    },
    "root": {"handlers": ["console", "file"], "level": "INFO"},
    "loggers": {
        "django": {"handlers": ["console", "file"], "level": "WARNING", "propagate": False},
    },
}
