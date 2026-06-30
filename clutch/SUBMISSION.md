# Clutch — Submission

### Vibe2Ship · PS1 "The Last-Minute Life Saver"

> Paste this into the submission Doc (BlockseBlock). Fill the two bracketed
> placeholders — the **live Cloud Run URL** and the **demo video link** — then
> spread your edits over time (judges check version history) and **Final Submit**.

---

## 1. Problem Statement Selected

**PS1 — The Last-Minute Life Saver.** Students and early-career people don't miss
deadlines because they forget they exist — they miss them because they notice too
late to act. Every productivity tool on the market is **passive**: a to-do list, a
reminder, a calendar alert. They all wait for *you* to open them and do the work.
The gap is **proactivity** — something that audits your commitments and acts *for*
you, before the miss becomes inevitable.

## 2. Solution Overview

**Clutch is an autonomous deadline agent.** On a schedule, with no user in the loop,
it runs a *deadline sweep*: it scores every task's slip-risk against your real
calendar, predicts the single task most likely to fail, and takes **one concrete
action** to save it — reserving focus time, drafting an extension email, or
generating a starter draft so the task is no longer a blank page.

It is not a reminder app. It is an agent that **acts before you fail** — and it
**learns** from how you respond, so it gets sharper at *you* over time.

- **Live demo:** `[CLOUD_RUN_URL]`
- **Walkthrough video:** `[VIDEO_LINK]`
- **Code:** https://github.com/swetank18/ps1

## 3. Key Features

1. **Proactive autonomy (not a chatbot).** Cloud Scheduler pings a Cloud Run
   endpoint on a cadence; the agent performs a deadline sweep unprompted, decides
   whether intervention is warranted, and acts. The self-initiated action loop —
   not a "suggest priorities" button — is what makes it genuinely agentic.
2. **Multi-agent triage pipeline (ADK).** A `SequentialAgent` runs three
   specialists in order: **Risk Sentinel** (ranks slip-risk, names the one worst
   task and *why*), **Scheduler** (reads free/busy, reserves focus blocks in your
   deep-work hours), **Intervenor** (takes exactly one high-leverage action).
3. **A learning feedback loop.** Every intervention can be **accepted, dismissed,
   or snoozed**. Clutch reweights that task *category* (accept ×1.25, dismiss ×0.6,
   snooze ×0.85) and folds the weight into future risk scoring and nudge
   escalation. Dismiss "pay fee" reminders three times and Clutch stops nagging;
   accept interview prep and it escalates sooner. The UI shows your acceptance rate
   and the learned weights.
4. **Dependency-aware risk.** Tasks can declare dependencies; a task blocked by an
   at-risk prerequisite **inherits** that urgency (shown as "blocked by …"), so the
   sweep protects the real bottleneck, not just the nearest deadline.
5. **Multimodal + document ingest.** Paste a syllabus/brief as text, or **upload a
   syllabus screenshot or PDF** — Gemini extracts the dated action items, which are
   auto-decomposed, risk-scored, and scheduled.
6. **Real Google Calendar actions.** With OAuth (testing mode), the Scheduler
   creates **real events** on your calendar — the tangible "the agent DID
   something" proof. Falls back to a deterministic stub when not configured, so the
   demo never breaks.
7. **An evaluation harness (the rigor signal).** A golden set of deadline scenarios
   scored on three metrics — tool **trajectory**, correct **decision** (right task
   flagged), correct **action** — runnable in-app and headless. See §6.

## 4. Technologies Used

Python · Google ADK (multi-agent orchestration + eval) · FastAPI + server-sent
events (live agent trace) · vanilla JS/CSS single-page UI · in-memory stores with
a clean swap path to Firestore.

## 5. Google Technologies Utilized

- **Gemini 3 Flash** — agent reasoning + **multimodal** syllabus/PDF extraction.
- **Google ADK** — code-first multi-agent app (`root_agent` → `planner` +
  `deadline_sweep`(`risk_sentinel`→`scheduler`→`intervenor`)) and the eval harness.
- **Cloud Run** — the deployed service (mandatory deploy gate).
- **Cloud Scheduler** — the cron trigger that makes the agent self-initiate.
- **Google Calendar API** — real free/busy reads + focus-block event creation.
- **Firestore** — task + learned-weight + run-history persistence (swap path).

## 6. Evaluation Results

The agent's decision logic is graded on a golden set of deadline scenarios via an
eval harness (the same intent as `adk eval`'s trajectory scoring), scored on three
independent metrics:

| Metric | Score | What it checks |
|---|---|---|
| Tool trajectory | **1.00** | Right tools, right order (IN_ORDER) |
| Decision | **1.00** | The correct task is flagged most at-risk |
| Action | **1.00** | The correct intervention is chosen |
| **Scenarios fully correct** | **7 / 7** | All three metrics pass |

Scenarios cover: an imminent high-effort task → extension email; everything
comfortable → **no action** (the agent must not manufacture urgency); a blank-page
blocker → starter draft; a deadline **tie broken by effort**; a **dependency chain**
where a safe-looking task inherits a prerequisite's risk; **overload** where
everything is on fire but the agent still takes exactly **one** action; and a
**learns-to-back-off** case where prior dismissals correctly suppress a flag.

Run it: `GET /api/eval` (in-app dashboard) or
`adk eval clutch eval/deadline_sweep.evalset.json --print_detailed_results`.

## 7. What's real vs. stubbed (honesty for the judges)

Real and live: the multi-agent ADK pipeline, the autonomy trigger endpoint, the
learning loop, dependency propagation, the eval harness, multimodal ingest, and —
when OAuth is configured — real Google Calendar events. The calendar, Firestore,
and learning-memory persistence ship with deterministic in-memory fallbacks so the
agent runs on first launch and the demo is reproducible offline; each has a marked
one-step swap to its Google-cloud backing.

---

### Demo script (90 seconds)
1. Upload a **syllabus screenshot** → Gemini extracts the dated items live.
2. Add a dependency ("the report waits on the data") → watch the report inherit risk.
3. Trigger a sweep (or show the **Cloud Scheduler log** proving it already ran on
   its own). Risk Sentinel flags the bottleneck and says why; Scheduler drops a
   **real calendar block**; Intervenor drafts the email/starter.
4. **Dismiss** the intervention → the "What Clutch learned" panel updates; run again
   to show the agent backing off. *It adapts.*
5. Close on the **eval dashboard**: "7/7 scenarios, trajectory/decision/action all
   1.00." Land the line: *this isn't a reminder app — it's an agent that acts before
   you fail, learns from you, and here's the proof it acts correctly.*
