"""
Tools for Clutch.

Every tool runs out-of-the-box with an in-memory store so `adk web` works on
first launch. Swap the marked sections for Firestore + Google Calendar when ready.

ADK tool convention: plain Python functions with typed args and a docstring
(the model reads the docstring to decide when/how to call them). Return a dict;
include a "status" key.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

# --------------------------------------------------------------------------
# In-memory store.  TODO: replace with Firestore (google-cloud-firestore).
# Persisting here keeps the demo deterministic and the agent runnable offline.
# --------------------------------------------------------------------------
_TASKS: dict[str, dict] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_deadline(text: str) -> str | None:
    """Very light deadline heuristic for the stub. The model normally supplies
    structured deadlines; this just keeps brain-dump ingest functional.
    Replace with a real parser or let the model fill the field."""
    return None


# --------------------------------------------------------------------------
# Task tools
# --------------------------------------------------------------------------
def add_tasks(raw_text: str) -> dict:
    """Capture tasks from a free-text brain dump into the store.

    Args:
        raw_text: messy text describing one or more tasks, e.g.
            "submit DBMS assignment Tue, prep DSA interview Thursday 6pm".
    Returns:
        dict: status and the list of created tasks (id, title, deadline, effort_minutes).
    """
    created = []
    for line in [p.strip() for p in raw_text.replace("\n", ",").split(",") if p.strip()]:
        tid = str(uuid.uuid4())[:8]
        _TASKS[tid] = {
            "id": tid,
            "title": line,
            "deadline": _parse_deadline(line),  # ISO string or None
            "effort_minutes": None,             # filled by decompose_task
            "subtasks": [],
            "status": "open",
            "created_at": _now().isoformat(),
        }
        created.append(_TASKS[tid])
    return {"status": "success", "created": created}


def list_tasks() -> dict:
    """Return all open tasks with their deadlines, effort, and subtasks."""
    return {"status": "success", "tasks": [t for t in _TASKS.values() if t["status"] == "open"]}


def decompose_task(task_id: str, subtasks: list[str], effort_minutes: int) -> dict:
    """Attach subtasks and a total effort estimate to a task.

    Args:
        task_id: the task to update.
        subtasks: 2-5 concrete subtasks.
        effort_minutes: realistic total remaining effort in minutes.
    """
    t = _TASKS.get(task_id)
    if not t:
        return {"status": "error", "message": f"no task {task_id}"}
    t["subtasks"] = subtasks
    t["effort_minutes"] = effort_minutes
    return {"status": "success", "task": t}


def assess_risk() -> dict:
    """Score slip-risk for every open task and return them ranked, worst first.

    Risk = deadline proximity x effort remaining x calendar scarcity.
    Returns the ranked list plus the single highest-risk task_id (or None if all safe).
    """
    ranked = []
    for t in _TASKS.values():
        if t["status"] != "open":
            continue
        effort = t.get("effort_minutes") or 60
        deadline = t.get("deadline")
        if deadline:
            hours_left = max((datetime.fromisoformat(deadline) - _now()).total_seconds() / 3600, 0.1)
        else:
            hours_left = 72.0  # unknown deadline = treat as moderately distant
        # crude pressure score; higher = more at risk
        score = round((effort / 60.0) / hours_left, 3)
        ranked.append({"task_id": t["id"], "title": t["title"], "risk": score,
                       "hours_left": round(hours_left, 1), "effort_minutes": effort})
    ranked.sort(key=lambda r: r["risk"], reverse=True)
    top = ranked[0]["task_id"] if ranked and ranked[0]["risk"] > 0.5 else None
    return {"status": "success", "ranked": ranked, "most_at_risk": top}


# --------------------------------------------------------------------------
# Calendar tools.  TODO: replace stubs with Google Calendar API.
# Use OAuth in "testing" mode with your own account (no app verification needed).
# Docs: https://developers.google.com/calendar/api/quickstart/python
# --------------------------------------------------------------------------
def get_calendar_availability(start_iso: str, end_iso: str) -> dict:
    """Return free time slots between two ISO timestamps.

    Args:
        start_iso: window start, ISO 8601.
        end_iso: window end, ISO 8601.
    """
    # STUB: pretend the next two evenings are free.
    base = _now().replace(minute=0, second=0, microsecond=0)
    slots = [
        {"start": (base + timedelta(hours=5)).isoformat(),
         "end": (base + timedelta(hours=7)).isoformat()},
        {"start": (base + timedelta(days=1, hours=5)).isoformat(),
         "end": (base + timedelta(days=1, hours=7)).isoformat()},
    ]
    return {"status": "success", "free_slots": slots}


def create_time_block(task_id: str, start_iso: str, end_iso: str) -> dict:
    """Create a real calendar event reserving time for a task.

    Args:
        task_id: task the block is for.
        start_iso / end_iso: ISO 8601 bounds.
    """
    t = _TASKS.get(task_id)
    title = t["title"] if t else task_id
    # STUB: real impl calls service.events().insert(...). Return a fake event id for now.
    return {"status": "success",
            "event": {"id": str(uuid.uuid4())[:8], "summary": f"Focus: {title}",
                      "start": start_iso, "end": end_iso}}


# --------------------------------------------------------------------------
# Intervention tools
# --------------------------------------------------------------------------
def draft_email(task_id: str, intent: str) -> dict:
    """Draft an email for a task (e.g. extension request or status update).

    Args:
        task_id: task the email concerns.
        intent: short description, e.g. "request 2-day extension".
    """
    t = _TASKS.get(task_id, {"title": task_id})
    body = (f"Subject: Regarding {t['title']}\n\n"
            f"Hi,\n\nI'm writing regarding {t['title']}. {intent}.\n\n"
            f"Thank you for your understanding.\nBest regards")
    return {"status": "success", "draft": body}


def generate_starter(task_id: str) -> dict:
    """Generate a first draft / outline so a task is no longer a blank page.

    Args:
        task_id: task to kick-start.
    """
    t = _TASKS.get(task_id, {"title": task_id, "subtasks": []})
    outline = t.get("subtasks") or ["Define scope", "Draft core", "Review & polish"]
    return {"status": "success", "starter": {"title": t["title"], "outline": outline}}


def send_nudge(task_id: str, level: str) -> dict:
    """Send an escalating reminder for a task.

    Args:
        task_id: task to nudge about.
        level: "gentle" | "firm" | "urgent".
    """
    t = _TASKS.get(task_id, {"title": task_id})
    # STUB: log now; wire to email/FCM push later.
    return {"status": "success", "nudge": {"task": t["title"], "level": level,
                                           "sent_at": _now().isoformat()}}
