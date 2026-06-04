from .base import *  # noqa: F401, F403

DEBUG = True
ALLOWED_HOSTS = ["*"]

# Relaxed CORS for local frontend dev
CORS_ALLOW_ALL_ORIGINS = True

# Allow unauthenticated requests locally for easier API testing
REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] = [  # noqa: F405
    "rest_framework.permissions.AllowAny"
]
