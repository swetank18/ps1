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


_PEAK_HOURS = range(9, 18)  # treat 9am–6pm as deep-work hours


def _reason(entry: dict, deliverable: bool, free_min: float | None = None) -> str:
    title = entry["title"]
    hl = _humanize_hours(entry["hours_left"])
    eff = entry["effort_minutes"]
    blocked = entry.get("blocked_by")
    if blocked:
        return (f"“{title}” is blocked by “{blocked['title']}”, which is itself at risk — "
                f"if that slips, this slips with it.")
    if not deliverable:
        return (f"“{title}” needs ~{eff} min of work but only {hl} remain — "
                f"it cannot realistically be finished in time.")
    pct = round(entry["base_risk"] * 100)
    tail = ""
    if free_min is not None and free_min < eff:
        tail = f", and only ~{round(free_min)} min of calendar is free before then"
    if entry.get("weight", 1.0) > 1.15:
        tail += " (you usually act on these, so Clutch is weighting it up)"
    elif entry.get("weight", 1.0) < 0.85:
        tail += " (you tend to dismiss these, so Clutch is weighting it down)"
    return (f"“{title}” needs ~{eff} min of work with only {hl} left "
            f"(~{pct}% of your remaining time){tail} — the most likely to slip.")


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
    dl = datetime.fromisoformat(deadline) if deadline else None
    end = deadline or (now.isoformat())
    slots = tools.get_calendar_availability(now.isoformat(), end)["free_slots"]

    # Calendar scarcity: how much free time actually lands before the deadline.
    def _eligible(s: dict) -> bool:
        return not (dl and datetime.fromisoformat(s["start"]) >= dl)

    free_min = sum((datetime.fromisoformat(s["end"]) - datetime.fromisoformat(s["start"])).total_seconds() / 60
                   for s in slots if _eligible(s))
    plan["free_min"] = round(free_min)
    plan["scarcity"] = round(min(entry["effort_minutes"] / free_min, 9.9), 2) if free_min else None

    blocks: list[dict] = []
    if not deliverable:
        # Undeliverable in time -> don't pretend a block saves it; escalate to email.
        action = "draft_email"
        plan["trajectory"].append("draft_email")
    else:
        # Reserve focus time, preferring deep-work (peak) hours, in slots before
        # the deadline. Stable sort keeps chronological order within each bucket.
        remaining = entry["effort_minutes"]
        eligible = sorted((s for s in slots if _eligible(s)),
                          key=lambda s: 0 if datetime.fromisoformat(s["start"]).hour in _PEAK_HOURS else 1)
        for s in eligible:
            if remaining <= 0:
                break
            mins = (datetime.fromisoformat(s["end"]) - datetime.fromisoformat(s["start"])).total_seconds() / 60
            peak = datetime.fromisoformat(s["start"]).hour in _PEAK_HOURS
            blocks.append({**s, "peak": peak})
            plan["trajectory"].append("create_time_block")
            remaining -= mins
        # Intervenor: blank page -> kick-start it; otherwise nudge.
        if not task.get("subtasks"):
            action = "generate_starter"
        else:
            action = "send_nudge"
        plan["trajectory"].append(action)

    plan.update(task=task, entry=entry, deliverable=deliverable,
                reason=_reason(entry, deliverable, free_min), blocks=blocks, action=action)
    return plan


def nudge_level(score: float, weight: float = 1.0) -> str:
    # Learned weight shifts the escalation: categories you act on get firmer
    # nudges sooner; ones you dismiss stay gentle longer.
    s = score * weight
    return "urgent" if s >= 0.5 else "firm" if s >= 0.20 else "gentle"
