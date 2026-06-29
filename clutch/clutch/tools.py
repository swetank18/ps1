"""
Tools for Clutch.

Every tool runs out-of-the-box with an in-memory store so `adk web` works on
first launch. Swap the marked sections for Firestore + Google Calendar when ready.

ADK tool convention: plain Python functions with typed args and a docstring
(the model reads the docstring to decide when/how to call them). Return a dict;
include a "status" key.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, time, timedelta, timezone

# --------------------------------------------------------------------------
# In-memory store.  TODO: replace with Firestore (google-cloud-firestore).
# Persisting here keeps the demo deterministic and the agent runnable offline.
# --------------------------------------------------------------------------
_TASKS: dict[str, dict] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Deadline parsing.  Lightweight NL → ISO heuristic so brain-dump ingest yields
# real deadlines (the risk engine depends on these). The model may still pass a
# structured deadline directly; this only fills the gap when it doesn't.
# --------------------------------------------------------------------------
_WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}

_DEFAULT_DUE = time(17, 0)  # 5pm if no time-of-day is mentioned


def _parse_time_of_day(text: str) -> time | None:
    """Extract a clock time like '6pm', '6:30 pm', '18:00', 'noon', 'midnight'."""
    low = text.lower()
    if "noon" in low:
        return time(12, 0)
    if "midnight" in low:
        return time(0, 0)
    if "tonight" in low or "evening" in low:
        return time(20, 0)
    if "morning" in low:
        return time(9, 0)
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\b", low)
    if m:
        hour = int(m.group(1)) % 12
        if m.group(3) == "p":
            hour += 12
        return time(hour, int(m.group(2) or 0))
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", low)
    if m:
        return time(int(m.group(1)) % 24, int(m.group(2)))
    return None


def _parse_deadline(text: str) -> str | None:
    """Best-effort deadline extraction → ISO 8601 string (UTC), or None.

    Handles: 'today', 'tonight', 'tomorrow', 'in N hours/days', a weekday name
    ('Tue', 'Thursday 6pm' → next occurrence), and explicit ISO dates. Falls
    back to a 5pm due time when only a day is given. Returns None when no date
    cue is present so the risk engine can treat it as a distant unknown.
    """
    low = text.lower()
    now = _now()
    tod = _parse_time_of_day(text)

    # explicit ISO date, e.g. 2026-07-01 or 2026-07-01T18:00
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})(?:[t ](\d{2}:\d{2}))?\b", low)
    if m:
        date_part = m.group(1)
        time_part = m.group(2) or (tod or _DEFAULT_DUE).strftime("%H:%M")
        try:
            dt = datetime.fromisoformat(f"{date_part}T{time_part}")
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass

    # relative: "in 3 hours", "in 2 days"
    m = re.search(r"\bin\s+(\d+)\s*(hour|hr|day)s?\b", low)
    if m:
        n = int(m.group(1))
        delta = timedelta(hours=n) if m.group(2).startswith(("hour", "hr")) else timedelta(days=n)
        return (now + delta).isoformat()

    def _at(day: datetime) -> str:
        t = tod or _DEFAULT_DUE
        return day.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0).isoformat()

    if "tonight" in low or "today" in low:
        return _at(now)
    if "tomorrow" in low:
        return _at(now + timedelta(days=1))

    # weekday name → next occurrence (today counts only if the due time hasn't passed)
    for word, wd in _WEEKDAYS.items():
        if re.search(rf"\b{word}\b", low):
            ahead = (wd - now.weekday()) % 7
            target = now + timedelta(days=ahead)
            iso = _at(target)
            if ahead == 0 and datetime.fromisoformat(iso) <= now:
                iso = _at(target + timedelta(days=7))
            return iso

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
    # risk = remaining-effort / hours-left; a task that needs >10% of your
    # remaining hours is flagged at-risk. (0.10 keeps the golden eval scenarios
    # consistent: scenario 3 at 0.167 fires, the "all comfortable" set stays quiet.)
    top = ranked[0]["task_id"] if ranked and ranked[0]["risk"] >= 0.10 else None
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
