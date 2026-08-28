# AGENTS.md

Read **MEMORY.md** first — it contains key decisions, architecture notes, and conventions for this project. Update it when making significant changes or decisions.

## Quick Reference
- Python: always use `venv\Scripts\python.exe` (global python lacks deps). Windows/PowerShell environment.
- Tests: `venv\Scripts\python.exe manage.py test projects` from repo root.
- Migrations: `venv\Scripts\python.exe manage.py makemigrations projects` — applied automatically at Docker deploy via `entrypoint.sh`; no manual migrate needed for deployment.
