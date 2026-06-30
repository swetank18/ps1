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
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from clutch import tools, vision
from . import orchestrator as orch

load_dotenv()

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")

app = FastAPI(title="Clutch", description="Autonomous deadline agent")

# Backend-side history the tools don't persist themselves.
STATE: dict = {"interventions": [], "blocks": [], "sweeps": [], "feedback": []}

SEED = ("finish internship application in 8 hours, "
        "prep DSA interview tomorrow 6pm, "
        "submit DBMS assignment Friday, "
        "pay hostel fee")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def snapshot() -> dict:
    enriched = []
    ranked = {r["task_id"]: r for r in tools.assess_risk()["ranked"]}
    titles = {t["id"]: t["title"] for t in tools.list_tasks()["tasks"]}
    for t in tools.list_tasks()["tasks"]:
        r = ranked.get(t["id"], {})
        enriched.append({**t,
                         "risk": r.get("risk", 0),
                         "band": orch.risk_band(r.get("risk", 0)),
                         "confidence": r.get("confidence"),
                         "blocked_by": r.get("blocked_by"),
                         "depends_on_titles": [titles.get(d, d) for d in t.get("depends_on", [])],
                         "hours_left": r.get("hours_left")})
    enriched.sort(key=lambda t: t["risk"], reverse=True)
    return {"tasks": enriched,
            "interventions": STATE["interventions"],
            "blocks": STATE["blocks"],
            "sweeps": STATE["sweeps"],
            "learning": learning_summary()}


def learning_summary() -> dict:
    """Surface what Clutch has learned + how the user responds to interventions."""
    fb = STATE["feedback"]
    counts = {"accept": 0, "dismiss": 0, "snooze": 0}
    for f in fb:
        counts[f["action"]] = counts.get(f["action"], 0) + 1
    prefs = {cat: p for cat, p in tools._PREFS.items()
             if p["accepts"] or p["dismisses"] or p["snoozes"]}
    acted = sum(1 for s in STATE["sweeps"] if s.get("at_risk"))
    return {"prefs": prefs, "counts": counts, "feedback_total": len(fb),
            "sweeps_total": len(STATE["sweeps"]), "interventions_total": acted}


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
    tools._PREFS.clear()
    STATE["interventions"].clear()
    STATE["blocks"].clear()
    STATE["sweeps"].clear()
    STATE["feedback"].clear()
    return snapshot()


# --------------------------------------------------------------------------
# Document / syllabus ingest — paste prose (a syllabus, an assignment brief, an
# email) and Clutch pulls out the dated action items. Deterministic by default
# (line/bullet split + date parser, incl. month names like "Oct 14"); if a
# Gemini key is present we let the model do the extraction for messier text.
# --------------------------------------------------------------------------
class DocBody(BaseModel):
    text: str
    use_llm: bool = False


_DATE_CUE = __import__("re").compile(
    r"\b(\d{4}-\d{2}-\d{2}|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|"
    r"mon|tue|wed|thu|fri|sat|sun|today|tonight|tomorrow|\bin \d+)",
    __import__("re").I,
)


def _doc_lines(text: str) -> list[str]:
    """Split a document into candidate task lines, keeping only ones with a date cue."""
    import re
    out = []
    for raw in text.splitlines():
        line = re.sub(r"^\s*(?:[-*•▪◦\d.)\]]+\s*)+", "", raw).strip()
        line = re.sub(r"^(due|deadline|submit|assignment)\s*[:\-]\s*", "", line, flags=re.I).strip()
        if len(line) >= 4 and _DATE_CUE.search(line):
            out.append(line)
    return out


@app.post("/api/ingest/document")
def ingest_document(body: DocBody):
    lines = _doc_lines(body.text)
    if not lines:
        return {"created": [], "skipped": "no dated action items found", "state": snapshot()}
    created = orch.ingest("\n".join(lines), decompose=True)
    return {"created": created, "detected": len(lines), "state": snapshot()}


@app.post("/api/ingest/image")
async def ingest_image(file: UploadFile = File(...)):
    """Multimodal ingest: a syllabus/brief screenshot or PDF -> Gemini -> dated tasks."""
    data = await file.read()
    if not data:
        return {"created": [], "error": "empty file", "state": snapshot()}
    out = vision.extract_task_lines(data, file.content_type or "application/octet-stream")
    if out["status"] != "success":
        return {"created": [], "error": out["message"], "state": snapshot()}
    if not out["lines"]:
        return {"created": [], "error": "no dated action items found in the document", "state": snapshot()}
    created = orch.ingest("\n".join(out["lines"]), decompose=True)
    return {"created": created, "detected": len(out["lines"]), "state": snapshot()}


# --------------------------------------------------------------------------
class LinkBody(BaseModel):
    task_id: str
    depends_on: str


@app.post("/api/link")
def link(body: LinkBody):
    res = tools.link_dependency(body.task_id, body.depends_on)
    return {"result": res, "state": snapshot()}


# --------------------------------------------------------------------------
# Feedback — the learning loop. The UI posts accept/dismiss/snooze on an
# intervention; Clutch reweights that task category for future sweeps.
# --------------------------------------------------------------------------
class FeedbackBody(BaseModel):
    intervention_id: str
    action: str  # accept | dismiss | snooze


@app.post("/api/feedback")
def feedback(body: FeedbackBody):
    rec = next((i for i in STATE["interventions"] if i.get("id") == body.intervention_id), None)
    if not rec:
        return {"status": "error", "message": "unknown intervention"}
    if rec.get("feedback"):
        return {"status": "noop", "message": "already recorded", "state": snapshot()}
    cat = rec.get("category", "general")
    out = tools.record_feedback(cat, body.action)
    if out.get("status") != "success":
        return out
    rec["feedback"] = body.action
    STATE["feedback"].insert(0, {"intervention_id": body.intervention_id, "category": cat,
                                 "action": body.action, "at": _now()})
    return {"status": "success", "learned": out["pref"], "state": snapshot()}


# --------------------------------------------------------------------------
# Live sweep — streamed as SSE so the UI animates the agent's trajectory.
# --------------------------------------------------------------------------
def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def sweep_events(pace: float = 0.55):
    """Run the sweep, yielding (event, data) tuples and applying side effects.
    Shared by the live SSE stream (pace>0) and the autonomy trigger (pace=0)."""

    async def step(event, data):
        if pace:
            await asyncio.sleep(pace)
        return (event, data)

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
        yield await step("done", {"state": snapshot(), "trajectory": plan["trajectory"],
                                  "summary": "No action — every task is on track."})
        return

    task = plan["task"]
    yield await step("verdict", {"at_risk": True, "task": task,
                                 "band": orch.risk_band(plan["entry"]["risk"]),
                                 "confidence": plan["entry"].get("confidence"),
                                 "blocked_by": plan["entry"].get("blocked_by"),
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
            peak_n = sum(1 for b in plan["blocks"] if b.get("peak"))
            extra = f" · {peak_n} in your deep-work hours" if peak_n else ""
            scar = f" Only ~{plan['free_min']} min was free before the deadline." if plan.get("free_min") is not None else ""
            yield await step("note", {"agent": "scheduler",
                                      "message": f"Reserved {len(created_blocks)} focus block(s) "
                                                 f"(~{round(mins)} min) before the deadline{extra}.{scar}"})
    else:
        yield await step("note", {"agent": "scheduler",
                                  "message": "Not enough runway to schedule around — escalating instead."})

    # ---- Stage 3: Intervenor ----
    yield await step("stage", {"agent": "intervenor", "label": "Intervenor",
                               "desc": "Taking one concrete action to save the task"})
    action = plan["action"]
    cat = task.get("category", "general")
    iid = uuid.uuid4().hex[:8]
    if action == "draft_email":
        out = tools.draft_email(task["id"], "request a short extension / send a status update")["draft"]
        record = {"id": iid, "category": cat, "type": "email", "icon": "✉️", "task": task["title"],
                  "title": "Extension email drafted", "body": out, "at": _now()}
    elif action == "generate_starter":
        out = tools.generate_starter(task["id"])["starter"]
        record = {"id": iid, "category": cat, "type": "starter", "icon": "📝", "task": task["title"],
                  "title": "Starter outline generated", "body": out, "at": _now()}
    else:
        level = orch.nudge_level(plan["entry"]["risk"], plan["entry"].get("weight", 1.0))
        out = tools.send_nudge(task["id"], level)["nudge"]
        record = {"id": iid, "category": cat, "type": "nudge", "icon": "🔔", "task": task["title"],
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
    async def gen():
        async for event, data in sweep_events():
            yield _sse(event, data)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/sweep/run")
async def sweep_run():
    """Non-streaming trigger for autonomy (Cloud Scheduler hits this on a cron)."""
    out = {"at_risk": False, "summary": None, "trajectory": None}
    async for event, data in sweep_events(pace=0.0):
        if event == "verdict":
            out["at_risk"] = data.get("at_risk", False)
        elif event == "done":
            out["summary"] = data.get("summary")
            out["trajectory"] = data.get("trajectory")
    return out


# --------------------------------------------------------------------------
# In-browser eval harness — runs the golden scenarios through the same engine
# and checks the produced tool trajectory against the expected one.
# --------------------------------------------------------------------------
# Each scenario declares the expected tool TRAJECTORY, the DECISION (which task
# should be flagged most at-risk, by title — or None for "stay quiet"), and the
# expected ACTION. `deps` links task i -> prerequisite j; `feedback` pre-seeds
# the learning memory so we can test that Clutch adapts. Scoring reports all
# three metrics separately, the way `adk eval` scores trajectory + response.
SCENARIOS = [
    {"name": "imminent_high_effort_task",
     "given": "Task A due in 2h with ~4h effort; B & C due next week, low effort.",
     "tasks": [("Ship launch deck", "in 2 hours", 240, ["a", "b"]),
               ("Reply to mentor", "in 7 days", 15, ["x"]),
               ("Plan next sprint", "in 6 days", 60, ["y"])],
     "expected": ["assess_risk", "get_calendar_availability", "draft_email"],
     "decision": "Ship launch deck", "action": "draft_email"},
    {"name": "all_tasks_comfortable",
     "given": "3 tasks, all 5+ days out with small effort and an open calendar.",
     "tasks": [("Read a chapter", "in 6 days", 30, ["a"]),
               ("Water the plants", "in 5 days", 10, ["b"]),
               ("Tidy desk", "in 7 days", 20, ["c"])],
     "expected": ["assess_risk"],
     "decision": None, "action": None},
    {"name": "blank_page_blocker",
     "given": "1 task due in 18h with ~3h effort, free evening, no subtasks yet.",
     "tasks": [("Write blog post", "in 18 hours", 180, [])],
     "expected": ["assess_risk", "get_calendar_availability", "create_time_block", "generate_starter"],
     "decision": "Write blog post", "action": "generate_starter"},
    {"name": "tie_broken_by_effort",
     "given": "Two tasks due in 6h; one is 20 min, one is 5h. Break the tie by effort.",
     "tasks": [("Email the TA", "in 6 hours", 20, ["x"]),
               ("Grade lab reports", "in 6 hours", 300, ["y"])],
     "expected": ["assess_risk", "get_calendar_availability", "create_time_block", "send_nudge"],
     "decision": "Grade lab reports", "action": "send_nudge"},
    {"name": "dependency_chain",
     "given": "A report due in 30h is safe alone, but it's blocked by data collection due in 3h.",
     "tasks": [("Write the report", "in 30 hours", 120, ["a"]),
               ("Collect the data", "in 3 hours", 60, ["b"])],
     "deps": [(0, 1)],  # 'Write the report' depends on 'Collect the data'
     "expected": ["assess_risk", "get_calendar_availability", "send_nudge"],
     "decision": "Collect the data", "action": "send_nudge",
     "expect_dependency_detected": True},
    {"name": "overload_takes_exactly_one",
     "given": "Three tasks all on fire — the agent must still take exactly ONE action.",
     "tasks": [("Finish slide deck", "in 2 hours", 240, ["a"]),
               ("Fix prod bug", "in 3 hours", 180, ["b"]),
               ("Submit the form", "in 4 hours", 120, ["c"])],
     "expected": ["assess_risk", "get_calendar_availability", "draft_email"],
     "decision": "Finish slide deck", "action": "draft_email"},
    {"name": "learns_to_back_off",
     "given": "An admin task that normally flags — but the user has dismissed it 3x, so Clutch backs off.",
     "tasks": [("Pay hostel fee", "in 2 hours", 15, ["x"])],
     "feedback": [("admin", "dismiss", 3)],
     "expected": ["assess_risk"],
     "decision": None, "action": None},
]


def _run_scenario(sc: dict) -> dict:
    saved_tasks, saved_prefs = dict(tools._TASKS), {k: dict(v) for k, v in tools._PREFS.items()}
    tools._TASKS.clear()
    tools._PREFS.clear()
    try:
        for cat, action, times in (sc.get("feedback") or []):
            for _ in range(times):
                tools.record_feedback(cat, action)
        ids = []
        for title, when, effort, subs in sc["tasks"]:
            created = tools.add_tasks(f"{title} {when}")["created"][0]
            tools.decompose_task(created["id"], list(subs), effort)
            ids.append(created["id"])
        for blocked_i, blocker_i in (sc.get("deps") or []):
            tools.link_dependency(ids[blocked_i], ids[blocker_i])
        plan = orch.plan_sweep()
        got = plan["trajectory"]
        ranked = plan["ranked"]
        decision = next((r["title"] for r in ranked if r["task_id"] == plan["most_at_risk"]), None)
        dep_detected = any(r.get("blocked_by") for r in ranked)
    finally:
        tools._TASKS.clear(); tools._TASKS.update(saved_tasks)
        tools._PREFS.clear(); tools._PREFS.update(saved_prefs)

    traj_ok = got == sc["expected"]
    decision_ok = (decision.startswith(sc["decision"]) if (decision and sc["decision"])
                   else decision == sc["decision"])
    action_ok = plan.get("action") == sc["action"]
    if sc.get("expect_dependency_detected"):
        decision_ok = decision_ok and dep_detected
    passed = traj_ok and decision_ok and action_ok
    return {"name": sc["name"], "given": sc["given"],
            "expected": sc["expected"], "got": got, "trajectory_ok": traj_ok,
            "decision_expected": sc["decision"], "decision_got": decision, "decision_ok": decision_ok,
            "action_expected": sc["action"], "action_got": plan.get("action"), "action_ok": action_ok,
            "passed": passed}


@app.get("/api/eval")
def run_eval():
    results = [_run_scenario(sc) for sc in SCENARIOS]
    n = len(results)
    passed = sum(r["passed"] for r in results)

    def _avg(key):
        return round(sum(1 for r in results if r[key]) / n, 2)

    return {"results": results, "passed": passed, "total": n,
            "score": round(passed / n, 2),
            "metrics": {"trajectory": _avg("trajectory_ok"),
                        "decision": _avg("decision_ok"),
                        "action": _avg("action_ok")}}


# --------------------------------------------------------------------------
# Static UI
# --------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


app.mount("/", StaticFiles(directory=WEB_DIR), name="web")
