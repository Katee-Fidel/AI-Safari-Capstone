#!/usr/bin/env sh
set -eu

python -m pip install --upgrade pip

# Force wheel-preference to avoid maturin/Rust builds where possible.
# IMPORTANT: do NOT use --only-binary=:all: because Render/Python may not have wheels for the pinned versions.
# Note: requires Render to call this script during build.
python -m pip install --prefer-binary -r requirements.render.txt


