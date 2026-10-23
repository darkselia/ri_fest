"""Django settings for the RI-fest project."""

import os
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, []),
)
environ.Env.read_env(BASE_DIR / ".env", overwrite=False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY.strip():
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must not be empty.")

DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")

INSTALLED_APPS = [
    "accounts.apps.AccountsConfig",
    "festivals.apps.FestivalsConfig",
    "applications.apps.ApplicationsConfig",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "ri_fest.urls"

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["accounts.backends.EmailVerificationBackend"]
LOGIN_URL = "accounts:login"
LOGOUT_REDIRECT_URL = "accounts:login"

# Set True to require confirmation. False signs new users in without confirmation.
EMAIL_VERIFICATION_REQUIRED = False
EMAIL_VERIFICATION_TIMEOUT = 24 * 60 * 60
PASSWORD_RESET_TIMEOUT = 24 * 60 * 60

CACHES = {
    "auth": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": "redis://redis:6379/2",
        "KEY_PREFIX": "ri-fest-auth",
        "OPTIONS": {"socket_connect_timeout": 2, "socket_timeout": 2},
    },
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
}
AUTH_RATE_LIMITS = {
    "login": {"email_ip": (10, 900), "ip": (60, 900)},
    "register": {"ip": (10, 3600)},
    "mail": {"email": (3, 900), "ip": (20, 3600)},
}

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
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

WSGI_APPLICATION = "ri_fest.wsgi.application"

database = {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": env.str("DB_NAME", default=""),
    "USER": env.str("DB_USER", default=""),
    "PASSWORD": os.environ.get("DB_PASSWORD", ""),
    "HOST": env.str("DB_HOST", default="127.0.0.1"),
    "OPTIONS": {"connect_timeout": 5},
}
for variable, field in {
    "DB_NAME": "NAME",
    "DB_USER": "USER",
    "DB_PASSWORD": "PASSWORD",
    "DB_HOST": "HOST",
}.items():
    if not database[field].strip():
        raise ImproperlyConfigured(f"{variable} must not be empty.")

try:
    database["PORT"] = env.int("DB_PORT", default=5432)
except (TypeError, ValueError):
    raise ImproperlyConfigured("DB_PORT must be an integer from 1 to 65535.") from None
if not 1 <= database["PORT"] <= 65535:
    raise ImproperlyConfigured("DB_PORT must be an integer from 1 to 65535.")

DATABASES = {"default": database}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "ru"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static", ("brand", BASE_DIR / "assets")]

DEFAULT_FROM_EMAIL = env.str("DEFAULT_FROM_EMAIL", default="no-reply@example.invalid")
email_use_tls = env.bool("EMAIL_USE_TLS", default=False)
email_use_ssl = env.bool("EMAIL_USE_SSL", default=False)
if email_use_tls and email_use_ssl:
    raise ImproperlyConfigured("EMAIL_USE_TLS and EMAIL_USE_SSL cannot both be True.")

MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "OPTIONS": {
            "host": env.str("EMAIL_HOST", default="mailpit"),
            "port": env.int("EMAIL_PORT", default=1025),
            "username": env.str("EMAIL_HOST_USER", default=""),
            "password": os.environ.get("EMAIL_HOST_PASSWORD", ""),
            "use_tls": email_use_tls,
            "use_ssl": email_use_ssl,
            "timeout": 10,
        },
    },
}

# The request-time calendar checks remain authoritative between scheduled runs.
CELERY_BROKER_URL = env.str("CELERY_BROKER_URL", default="redis://127.0.0.1:6379/0")
CELERY_TIMEZONE = "UTC"
CELERY_ENABLE_UTC = True
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_IGNORE_RESULT = True
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
# Synchronize stored states hourly; requests check calendar boundaries immediately.
FESTIVAL_SYNC_INTERVAL_SECONDS = 3600
CELERY_BEAT_SCHEDULE = {
    "sync-festival-states": {
        "task": "festivals.tasks.synchronize_festivals",
        "schedule": FESTIVAL_SYNC_INTERVAL_SECONDS,
        "options": {"expires": FESTIVAL_SYNC_INTERVAL_SECONDS},
    },
}
