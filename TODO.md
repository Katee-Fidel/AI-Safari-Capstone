# TODO

## Completed
- [x] services.py: Harden Node 4 sentinel for empty `recommendations` (fallback + compliance check).
- [x] services.py: Fix production signal emission by ensuring `ledger` is in scope for `_emit_completion_signal`.
- [x] views.py: Remove unused `DRFValidationError` import.
- [x] production.py: Fix `STATICFILES_STORAGE` override to use WhiteNoise manifest storage.
- [x] production.py: Avoid duplicating `WhiteNoiseMiddleware`.

## Remaining
- [ ] requirements.txt vs requirements.render.txt: ensure CrewAI/CrewAI-tools/langchain versions are aligned intentionally (right now both appear aligned, but verify Render build uses requirements.render.txt exactly).
- [ ] Run sanity checks locally:
  - [x] `python -m compileall agent_pride config` (avoid venv/site-packages)
  - [x] `python manage.py check`
  - [x] `python manage.py makemigrations --check --dry-run`



