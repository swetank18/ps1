# Clutch — autonomous deadline agent

**Vibe2Ship · PS1 "The Last-Minute Life Saver."** Clutch doesn't remind you — it
**acts before you slip**. It runs a *deadline sweep* on a schedule, audits your tasks
against your calendar, predicts the single task most likely to slip, and takes one
concrete action to save it: block the time, draft the email, or generate the first draft.

The project lives in [`clutch/`](clutch/).

## Quick start
```bash
cd clutch
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # paste your AI Studio key into GOOGLE_API_KEY

# A) the polished web app (task board + live agent trace + eval dashboard)
./run_web.sh                # → http://localhost:8080

# B) the raw ADK dev UI (trajectory/graph trace)
adk web                     # → http://localhost:8000, pick "clutch"
```

## What's inside
| Path | What |
|---|---|
| `clutch/clutch/agent.py` | ADK multi-agent: `root_agent` → `planner` + `deadline_sweep` (sentinel → scheduler → intervenor) |
| `clutch/clutch/tools.py` | Agent tools (in-memory stubs; swap for Firestore + Calendar) with a real NL deadline parser |
| `clutch/server/` | FastAPI backend — ingest, live SSE sweep, in-browser eval harness |
| `clutch/web/` | Single-page UI (task board, animated sweep trace, interventions, eval) |
| `clutch/eval/` | Golden `adk eval` scenarios |
| `clutch/PRODUCT_SPEC.md` | Full product brief & rationale |

## Google tech
Gemini 3 Flash · Google ADK · Cloud Run · Cloud Scheduler · Firestore · Calendar API.
