"""
Clutch web backend (FastAPI).

Serves the single-page UI and exposes a clean API over the Clutch agent tools:
  GET  /api/state            current tasks + interventions + calendar blocks
  POST /api/ingest           capture a brain-dump (+ Planner decomposition)
  GET  /api/sweep            live deadline sweep as a Server-Sent-Events stream
  POST /api/seed             load a demo brain-dump
  POST /api/reset            clear everything
  GET  /api/eval             run the golden scenarios through the engine (trajectory check)
  GET  /api/health           model/key status

Run:  uvicorn server.app:app --reload --port 8080   (from the clutch/ dir)
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from clutch import tools
from . import orchestrator as orch

load_dotenv()

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")

app = FastAPI(title="Clutch", description="Autonomous deadline agent")

# Backend-side history the tools don't persist themselves.
STATE: dict = {"interventions": [], "blocks": [], "sweeps": []}

SEED = ("finish internship application in 8 hours, "
        "prep DSA interview tomorrow 6pm, "
        "submit DBMS assignment Friday, "
        "pay hostel fee")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def snapshot() -> dict:
    enriched = []
    ranked = {r["task_id"]: r for r in tools.assess_risk()["ranked"]}
    for t in tools.list_tasks()["tasks"]:
        r = ranked.get(t["id"], {})
        enriched.append({**t,
                         "risk": r.get("risk", 0),
                         "band": orch.risk_band(r.get("risk", 0)),
                         "hours_left": r.get("hours_left")})
    enriched.sort(key=lambda t: t["risk"], reverse=True)
    return {"tasks": enriched,
            "interventions": STATE["interventions"],
            "blocks": STATE["blocks"],
            "sweeps": STATE["sweeps"]}


# --------------------------------------------------------------------------
class IngestBody(BaseModel):
    text: str
    decompose: bool = True


@app.get("/api/health")
def health():
    key = os.environ.get("GOOGLE_API_KEY", "")
    return {"ok": True, "model": os.environ.get("MODEL", "gemini-3-flash-preview"),
            "key_present": bool(key) and key != "your-key-here",
            "task_count": len(tools.list_tasks()["tasks"])}


@app.get("/api/state")
def state():
    return snapshot()


@app.post("/api/ingest")
def ingest(body: IngestBody):
    created = orch.ingest(body.text, decompose=body.decompose)
    return {"created": created, "state": snapshot()}


@app.post("/api/seed")
def seed():
    orch.ingest(SEED, decompose=True)
    return snapshot()


@app.post("/api/reset")
def reset():
    tools._TASKS.clear()
    STATE["interventions"].clear()
    STATE["blocks"].clear()
    STATE["sweeps"].clear()
    return snapshot()


# --------------------------------------------------------------------------
# Live sweep — streamed as SSE so the UI animates the agent's trajectory.
# --------------------------------------------------------------------------
def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def sweep_stream():
    PACE = 0.55  # seconds between steps, for a legible live trace

    async def step(event, data):
        await asyncio.sleep(PACE)
        return _sse(event, data)

    plan = orch.plan_sweep()

    # ---- Stage 1: Risk Sentinel ----
    yield await step("stage", {"agent": "risk_sentinel", "label": "Risk Sentinel",
                               "desc": "Scoring slip-risk across every open task"})
    yield await step("tool", {"agent": "risk_sentinel", "name": "assess_risk",
                              "result": {"ranked": plan["ranked"][:5],
                                         "most_at_risk": plan["most_at_risk"]}})

    if not plan["most_at_risk"]:
        yield await step("verdict", {"at_risk": False,
                                     "message": "Every task is comfortably ahead of schedule. "
                                                "No intervention needed — Clutch will not manufacture urgency."})
        STATE["sweeps"].insert(0, {"at": _now(), "at_risk": False,
                                   "trajectory": plan["trajectory"], "summary": "No action — all clear."})
        yield _sse("done", {"state": snapshot(), "trajectory": plan["trajectory"]})
        return

    task = plan["task"]
    yield await step("verdict", {"at_risk": True, "task": task,
                                 "band": orch.risk_band(plan["entry"]["risk"]),
                                 "message": plan["reason"]})

    # ---- Stage 2: Scheduler ----
    yield await step("stage", {"agent": "scheduler", "label": "Scheduler",
                               "desc": "Protecting focus time on your calendar"})
    yield await step("tool", {"agent": "scheduler", "name": "get_calendar_availability",
                              "result": {"free_slots": len(plan["blocks"]) if plan["deliverable"] else "checked"}})
    created_blocks = []
    if plan["deliverable"]:
        for s in plan["blocks"]:
            ev = tools.create_time_block(task["id"], s["start"], s["end"])["event"]
            ev["task"] = task["title"]
            created_blocks.append(ev)
            STATE["blocks"].insert(0, ev)
            yield await step("tool", {"agent": "scheduler", "name": "create_time_block", "result": ev})
        if created_blocks:
            mins = sum((datetime.fromisoformat(b["end"]) - datetime.fromisoformat(b["start"])).total_seconds() / 60
                       for b in created_blocks)
            yield await step("note", {"agent": "scheduler",
                                      "message": f"Reserved {len(created_blocks)} focus block(s) "
                                                 f"(~{round(mins)} min) before the deadline."})
    else:
        yield await step("note", {"agent": "scheduler",
                                  "message": "Not enough runway to schedule around — escalating instead."})

    # ---- Stage 3: Intervenor ----
    yield await step("stage", {"agent": "intervenor", "label": "Intervenor",
                               "desc": "Taking one concrete action to save the task"})
    action = plan["action"]
    if action == "draft_email":
        out = tools.draft_email(task["id"], "request a short extension / send a status update")["draft"]
        record = {"type": "email", "icon": "✉️", "task": task["title"],
                  "title": "Extension email drafted", "body": out, "at": _now()}
    elif action == "generate_starter":
        out = tools.generate_starter(task["id"])["starter"]
        record = {"type": "starter", "icon": "📝", "task": task["title"],
                  "title": "Starter outline generated", "body": out, "at": _now()}
    else:
        level = orch.nudge_level(plan["entry"]["risk"])
        out = tools.send_nudge(task["id"], level)["nudge"]
        record = {"type": "nudge", "icon": "🔔", "task": task["title"],
                  "title": f"{level.capitalize()} nudge sent", "body": out, "at": _now()}
    STATE["interventions"].insert(0, record)
    yield await step("tool", {"agent": "intervenor", "name": action, "result": record})

    summary = f"{record['title']} for “{task['title']}”"
    if created_blocks:
        summary += f" · {len(created_blocks)} calendar block(s) reserved"
    STATE["sweeps"].insert(0, {"at": _now(), "at_risk": True,
                               "trajectory": plan["trajectory"], "summary": summary})
    yield await step("done", {"state": snapshot(), "trajectory": plan["trajectory"], "summary": summary})


@app.get("/api/sweep")
async def sweep():
    return StreamingResponse(sweep_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --------------------------------------------------------------------------
# In-browser eval harness — runs the golden scenarios through the same engine
# and checks the produced tool trajectory against the expected one.
# --------------------------------------------------------------------------
SCENARIOS = [
    {"name": "imminent_high_effort_task",
     "given": "Task A due in 2h with ~4h effort; B & C due next week, low effort.",
     "tasks": [("Ship launch deck", "in 2 hours", 240, ["a", "b"]),
               ("Reply to mentor", "in 7 days", 15, ["x"]),
               ("Plan next sprint", "in 6 days", 60, ["y"])],
     "expected": ["assess_risk", "get_calendar_availability", "draft_email"]},
    {"name": "all_tasks_comfortable",
     "given": "3 tasks, all 5+ days out with small effort and an open calendar.",
     "tasks": [("Read a chapter", "in 6 days", 30, ["a"]),
               ("Water the plants", "in 5 days", 10, ["b"]),
               ("Tidy desk", "in 7 days", 20, ["c"])],
     "expected": ["assess_risk"]},
    {"name": "blank_page_blocker",
     "given": "1 task due in 18h with ~3h effort, free evening, no subtasks yet.",
     "tasks": [("Write blog post", "in 18 hours", 180, [])],
     "expected": ["assess_risk", "get_calendar_availability", "create_time_block", "generate_starter"]},
]


def _run_scenario(sc: dict) -> dict:
    saved = dict(tools._TASKS)
    tools._TASKS.clear()
    try:
        for title, when, effort, subs in sc["tasks"]:
            created = tools.add_tasks(f"{title} {when}")["created"][0]
            tools.decompose_task(created["id"], list(subs), effort)
        plan = orch.plan_sweep()
        got = plan["trajectory"]
    finally:
        tools._TASKS.clear()
        tools._TASKS.update(saved)
    passed = got == sc["expected"]
    return {"name": sc["name"], "given": sc["given"], "expected": sc["expected"],
            "got": got, "passed": passed}


@app.get("/api/eval")
def run_eval():
    results = [_run_scenario(sc) for sc in SCENARIOS]
    passed = sum(r["passed"] for r in results)
    return {"results": results, "passed": passed, "total": len(results),
            "score": round(passed / len(results), 2)}


# --------------------------------------------------------------------------
# Static UI
# --------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


app.mount("/", StaticFiles(directory=WEB_DIR), name="web")
