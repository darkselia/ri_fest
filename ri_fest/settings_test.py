"""Isolated settings for pytest; no persistent database or real secrets."""

import os

os.environ["DJANGO_SECRET_KEY"] = "synthetic-test-key-not-for-deployment"
os.environ.update(
    DB_NAME="synthetic_db",
    DB_USER="synthetic_user",
    DB_PASSWORD="$synthetic@password#%:/",
    DB_HOST="127.0.0.1",
    DB_PORT="65432",
)

from .settings import *  # noqa: E402, F403

DEBUG = False
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    "auth": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "synthetic-auth-tests",
    },
}
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    },
}
MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    },
}
