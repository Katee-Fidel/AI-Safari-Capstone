#!/usr/bin/env sh
set -eu

python -m pip install --upgrade pip setuptools wheel

pip install -r requirements.render.txt

python manage.py collectstatic --no-input
python manage.py migrate
