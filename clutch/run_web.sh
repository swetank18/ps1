#!/usr/bin/env bash
# Launch the Clutch web app (FastAPI backend + UI).  Run from the clutch/ dir.
set -e
cd "$(dirname "$0")"
source .venv/bin/activate 2>/dev/null || true
exec uvicorn server.app:app --host 0.0.0.0 --port 8080 "$@"
