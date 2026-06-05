#!/usr/bin/env sh
set -eu

python -m pip install --upgrade pip

# Install using binary-first strategy.
# Do NOT use --only-binary=:all: because Render/Python may not have wheels for every pinned version.
python -m pip install --only-binary=:all: -r requirements.render.txt --no-deps




