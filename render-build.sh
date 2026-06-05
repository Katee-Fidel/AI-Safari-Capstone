#!/usr/bin/env sh
set -eu

python -m pip install --upgrade pip setuptools wheel

pip install -r requirements.render.txt

DJANGO_SETTINGS_MODULE=config.settings.production python manage.py collectstatic --no-input
DJANGO_SETTINGS_MODULE=config.settings.production python manage.py migrate