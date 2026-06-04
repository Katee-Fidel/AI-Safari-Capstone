# TODO

## Goal: Fix Render deployment error: `error: metadata-generation-failed` while generating package metadata for `pydantic-core`

- [ ] Gather detailed build logs from Render (the full traceback) and identify the failing wheel/build step for `pydantic-core`.
- [ ] Inspect `requirements.txt` and dependency graph to confirm why `pydantic-core` is being built from source (missing manylinux wheel / build toolchain).
- [ ] Update packaging/dependency strategy (pin/upgrade `pydantic`/`pydantic-core` and/or add a prebuilt-wheel workaround) and adjust Render build settings if needed.
- [ ] Remove/adjust any dependencies that force source builds (if present) and re-run `pip install -r requirements.txt` locally with `--no-build-isolation` / `--prefer-binary` to reproduce.
- [ ] Re-test: build in a clean environment (or Docker) if possible; otherwise run local `pip install` and `python -c "import pydantic_core"`.
- [x] Re-deploy to Render and confirm the build succeeds.


