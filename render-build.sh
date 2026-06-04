#!/usr/bin/env sh
set -eu

python -m pip install --upgrade pip

# Force wheel-only installs to avoid maturin/Rust builds on Read-only filesystems.
# Note: requires Render to call this script during build.
python -m pip install --only-binary=:all: -r requirements.render.txt

