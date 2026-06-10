from .base import *  # noqa: F401, F403
import os

import dj_database_url

DEBUG = False

render_hostname = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
configured_hosts = os.environ.get("ALLOWED_HOSTS", "")
ALLOWED_HOSTS = [host for host in configured_hosts.split(",") if host]
if render_hostname:
    ALLOWED_HOSTS.append(render_hostname)
if not ALLOWED_HOSTS:
    ALLOWED_HOSTS = ["localhost"]

# Enforce HTTPS
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Railway/Postgres
DATABASES = {
    "default": dj_database_url.config(
        default=os.environ.get("DATABASE_URL"),
        conn_max_age=600,
        ssl_require=True,
    )
}

# Static files (WhiteNoise)
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"


# WhiteNoiseMiddleware is already included in base settings.
# Avoid duplicating it here.
MIDDLEWARE = [*MIDDLEWARE]


CORS_ALLOWED_ORIGINS = [
    origin for origin in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",")
    if origin
]

CSRF_TRUSTED_ORIGINS = [
    origin for origin in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin
]
if render_hostname:
    CSRF_TRUSTED_ORIGINS.append(f"https://{render_hostname}")

