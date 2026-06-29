"""
Deterministic sweep orchestrator for the Clutch web app.

This mirrors the ADK `deadline_sweep` pipeline (risk_sentinel -> scheduler ->
intervenor) by calling the SAME `clutch.tools`, in the SAME order, with the same
decision rules the agent instructions describe. It produces a legible, animated
trajectory for the UI and runs with zero LLM dependency — so the demo is rock
solid even when the Gemini free tier is rate-limited.

The real LLM-routed agent still lives in `clutch/agent.py` and is served at
`/adk/*`; this engine is the reliable, gradeable backbone of the product demo.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

from clutch import tools


# --------------------------------------------------------------------------
# Planner heuristic — decompose a task into subtasks + an effort estimate.
# Stands in for the LLM Planner so ingest works offline and deterministically.
# --------------------------------------------------------------------------
_PROFILES = [
    (("assignment", "report", "essay", "doc", "paper", "writeup", "submit dbms"),
     180, ["Outline the structure", "Write the core sections", "Add references / checks", "Final proofread"]),
    (("interview", "prep", "dsa", "leetcode", "exam", "test", "study", "revise"),
     240, ["Review fundamentals", "Drill practice problems", "Timed mock run", "Patch weak areas"]),
    (("application", "apply", "resume", "cv", "cover letter", "internship"),
     120, ["Draft the materials", "Tailor to the role", "Proofread", "Submit the application"]),
    (("pay", "fee", "bill", "renew", "book", "register", "email", "call", "reply"),
     15, ["Complete the task"]),
    (("presentation", "slides", "deck", "demo"),
     150, ["Storyboard the flow", "Build the slides", "Rehearse once"]),
]
_DEFAULT = (90, ["Define the scope", "Do the main work", "Review & wrap up"])


def estimate(title: str) -> tuple[int, list[str]]:
    """Return (effort_minutes, subtasks) for a task title."""
    low = title.lower()
    for keys, effort, subs in _PROFILES:
        if any(k in low for k in keys):
            return effort, list(subs)
    return _DEFAULT[0], list(_DEFAULT[1])


def ingest(raw_text: str, decompose: bool = True) -> list[dict]:
    """Capture a brain-dump and (optionally) run the Planner decomposition."""
    created = tools.add_tasks(raw_text)["created"]
    if decompose:
        for t in created:
            effort, subs = estimate(t["title"])
            tools.decompose_task(t["id"], subs, effort)
    return [tools._TASKS[t["id"]] for t in created]


# --------------------------------------------------------------------------
# Risk reasoning helpers
# --------------------------------------------------------------------------
def risk_band(score: float) -> str:
    if score >= 1.0:
        return "critical"
    if score >= 0.30:
        return "high"
    if score >= 0.10:
        return "medium"
    return "low"


def _humanize_hours(hours: float) -> str:
    if hours < 1:
        return f"{round(hours * 60)} min"
    if hours < 48:
        return f"{round(hours)}h"
    return f"{round(hours / 24)}d"


def _reason(entry: dict, deliverable: bool) -> str:
    title = entry["title"]
    hl = _humanize_hours(entry["hours_left"])
    eff = entry["effort_minutes"]
    if not deliverable:
        return (f"“{title}” needs ~{eff} min of work but only {hl} remain — "
                f"it cannot realistically be finished in time.")
    pct = round(entry["risk"] * 100)
    return (f"“{title}” needs ~{eff} min of work with only {hl} left "
            f"(~{pct}% of your remaining time) — the most likely to slip.")


# --------------------------------------------------------------------------
# Plan a sweep over the CURRENT task store. Pure: no external state mutation,
# no event streaming — used by both the live stream and the eval harness.
# --------------------------------------------------------------------------
def plan_sweep() -> dict:
    risk = tools.assess_risk()
    ranked = risk["ranked"]
    top_id = risk["most_at_risk"]
    plan: dict = {"ranked": ranked, "most_at_risk": top_id, "trajectory": ["assess_risk"]}

    if not top_id:
        plan.update(task=None, deliverable=None, reason=None, blocks=[], action=None)
        return plan

    task = copy.deepcopy(tools._TASKS[top_id])
    entry = next(r for r in ranked if r["task_id"] == top_id)
    deadline = task.get("deadline")
    deliverable = entry["hours_left"] * 60 >= entry["effort_minutes"]

    # Scheduler always checks availability for the at-risk task.
    plan["trajectory"].append("get_calendar_availability")
    now = datetime.now(timezone.utc)
    end = deadline or (now.isoformat())
    slots = tools.get_calendar_availability(now.isoformat(), end)["free_slots"]

    blocks: list[dict] = []
    if not deliverable:
        # Undeliverable in time -> don't pretend a block saves it; escalate to email.
        action = "draft_email"
        plan["trajectory"].append("draft_email")
    else:
        # Reserve focus time in free slots that land before the deadline.
        remaining = entry["effort_minutes"]
        dl = datetime.fromisoformat(deadline) if deadline else None
        for s in slots:
            if remaining <= 0:
                break
            s_start = datetime.fromisoformat(s["start"])
            s_end = datetime.fromisoformat(s["end"])
            if dl and s_start >= dl:
                continue
            mins = (s_end - s_start).total_seconds() / 60
            blocks.append(s)
            plan["trajectory"].append("create_time_block")
            remaining -= mins
        # Intervenor: blank page -> kick-start it; otherwise nudge.
        if not task.get("subtasks"):
            action = "generate_starter"
        else:
            action = "send_nudge"
        plan["trajectory"].append(action)

    plan.update(task=task, entry=entry, deliverable=deliverable,
                reason=_reason(entry, deliverable), blocks=blocks, action=action)
    return plan


def nudge_level(score: float) -> str:
    return "urgent" if score >= 0.5 else "firm" if score >= 0.20 else "gentle"
