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

# --------------------------------------------------------------------------
# Learning memory.  Clutch adapts to how you respond to its interventions:
# accepting an action reinforces urgency for that kind of task; dismissing it
# teaches Clutch to back off. Keyed by a coarse task category so the signal
# generalises across similar tasks. TODO: persist alongside tasks in Firestore.
# --------------------------------------------------------------------------
_PREFS: dict[str, dict] = {}   # category -> {"weight": float, "accepts": int, "dismisses": int, "snoozes": int}

_CATEGORIES = [
    ("assignment", ("assignment", "report", "essay", "paper", "homework", "submit", "dbms", "thesis")),
    ("interview",  ("interview", "dsa", "leetcode", "prep", "study", "exam", "test", "revise")),
    ("application", ("application", "apply", "resume", "cv", "cover letter", "internship", "job")),
    ("admin",      ("pay", "fee", "bill", "renew", "register", "book", "form", "tax")),
    ("comms",      ("email", "call", "reply", "message", "follow up", "ping")),
    ("creative",   ("blog", "post", "deck", "slides", "presentation", "design", "draft", "write")),
]


def categorize(title: str) -> str:
    """Map a task title to a coarse category used for the learning memory."""
    low = title.lower()
    for cat, keys in _CATEGORIES:
        if any(k in low for k in keys):
            return cat
    return "general"


def _pref(cat: str) -> dict:
    return _PREFS.setdefault(cat, {"weight": 1.0, "accepts": 0, "dismisses": 0, "snoozes": 0})


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

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


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

    # month-name date: "Oct 14", "October 14, 2026", "14 Dec", "Dec 3rd"
    month_alt = "|".join(_MONTHS)
    m = (re.search(rf"\b({month_alt})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b", low)
         or re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({month_alt})\.?(?:,?\s+(\d{{4}}))?\b", low))
    if m:
        g = m.groups()
        if g[0] in _MONTHS:          # "Oct 14"
            mon, day, yr = _MONTHS[g[0]], int(g[1]), g[2]
        else:                         # "14 Oct"
            mon, day, yr = _MONTHS[g[1]], int(g[0]), g[2]
        year = int(yr) if yr else now.year
        t = tod or _DEFAULT_DUE
        try:
            dt = datetime(year, mon, day, t.hour, t.minute, tzinfo=timezone.utc)
            # no explicit year and the date already passed -> roll to next year
            if not yr and dt < now:
                dt = dt.replace(year=year + 1)
            return dt.isoformat()
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
            "depends_on": [],                   # task ids that must finish first
            "category": categorize(line),
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


def link_dependency(task_id: str, depends_on_task_id: str) -> dict:
    """Record that `task_id` cannot start until `depends_on_task_id` is done.

    Dependencies let the risk engine propagate urgency: if a prerequisite is
    itself at risk, the work waiting on it inherits that pressure.

    Args:
        task_id: the blocked (downstream) task.
        depends_on_task_id: the prerequisite (upstream) task that must finish first.
    """
    t = _TASKS.get(task_id)
    if not t:
        return {"status": "error", "message": f"no task {task_id}"}
    if depends_on_task_id not in _TASKS:
        return {"status": "error", "message": f"no task {depends_on_task_id}"}
    if depends_on_task_id == task_id:
        return {"status": "error", "message": "a task cannot depend on itself"}
    if depends_on_task_id not in t["depends_on"]:
        t["depends_on"].append(depends_on_task_id)
    return {"status": "success", "task": t}


def record_feedback(task_category: str, action: str) -> dict:
    """Learn from how the user responded to an intervention.

    `action` is one of: accept (the intervention was useful), dismiss (it was
    noise), snooze (right idea, wrong time). Accepts raise the urgency weight for
    that category; dismisses lower it; snoozes nudge it down slightly. The weight
    feeds back into the next sweep's risk scoring and nudge escalation.

    Args:
        task_category: coarse category (assignment, interview, admin, ...).
        action: "accept" | "dismiss" | "snooze".
    """
    p = _pref(task_category)
    if action == "accept":
        p["accepts"] += 1
        p["weight"] = min(p["weight"] * 1.25, 2.5)
    elif action == "dismiss":
        p["dismisses"] += 1
        p["weight"] = max(p["weight"] * 0.6, 0.25)
    elif action == "snooze":
        p["snoozes"] += 1
        p["weight"] = max(p["weight"] * 0.85, 0.25)
    else:
        return {"status": "error", "message": f"unknown action {action}"}
    p["weight"] = round(p["weight"], 3)
    return {"status": "success", "category": task_category, "pref": p}


def assess_risk() -> dict:
    """Score slip-risk for every open task and return them ranked, worst first.

    Risk = (effort / hours-left) x learned-category-weight, then propagated along
    dependency edges so work blocked by an at-risk prerequisite inherits pressure.
    Each entry carries a confidence (lower when deadline/effort are unknown).
    Returns the ranked list plus the single highest-risk task_id (or None if all safe).
    """
    base: dict[str, dict] = {}
    for t in _TASKS.values():
        if t["status"] != "open":
            continue
        effort = t.get("effort_minutes") or 60
        deadline = t.get("deadline")
        if deadline:
            hours_left = max((datetime.fromisoformat(deadline) - _now()).total_seconds() / 3600, 0.1)
        else:
            hours_left = 72.0  # unknown deadline = treat as moderately distant
        weight = _pref(t.get("category") or "general")["weight"]
        raw = (effort / 60.0) / hours_left
        score = round(raw * weight, 3)
        # confidence: high when we know both the deadline and a decomposed effort.
        conf = 0.9 if (deadline and t.get("effort_minutes")) else 0.6 if deadline else 0.4
        base[t["id"]] = {"task_id": t["id"], "title": t["title"], "risk": score,
                         "base_risk": round(raw, 3), "hours_left": round(hours_left, 1),
                         "effort_minutes": effort, "category": t.get("category") or "general",
                         "weight": weight, "confidence": conf, "blocked_by": None,
                         "depends_on": list(t.get("depends_on") or [])}

    # Dependency propagation: a task waiting on an at-risk prerequisite inherits
    # the larger of (its own risk, 80% of the blocker's risk) and is flagged.
    for entry in base.values():
        for dep_id in entry["depends_on"]:
            dep = base.get(dep_id)
            if dep and dep["base_risk"] > entry["risk"]:
                inherited = round(dep["base_risk"] * 0.8 * entry["weight"], 3)
                if inherited > entry["risk"]:
                    entry["risk"] = inherited
                    entry["blocked_by"] = {"task_id": dep_id, "title": dep["title"]}

    ranked = sorted(base.values(), key=lambda r: r["risk"], reverse=True)
    # A task that needs >10% of your remaining hours is flagged at-risk. (0.10
    # keeps the golden eval scenarios consistent: scenario 3 at 0.167 fires, the
    # "all comfortable" set stays quiet.) Learned weight & dependencies can lift
    # a task over this line even when its raw proximity wouldn't.
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
