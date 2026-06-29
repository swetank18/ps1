# Clutch — Product Spec & Build Brief
### Vibe2Ship · PS1 "The Last-Minute Life Saver"

> Hand this file to Claude Code as the project brief. It is the source of truth for what to build and why.

---

## 0. The one-sentence pitch
**Clutch is an autonomous deadline agent: it wakes itself up on a schedule, audits your tasks against your real calendar, predicts the one most likely to slip, and takes a concrete action to save it — drafting the email, blocking the time, or generating the first draft — before you've even noticed.**

The name = the player who performs under pressure. Rename freely; the concept is what matters.

---

## 1. Why this wins (read before building)

The rubric: Agentic Depth 20% + Problem Solving & Impact 20% + Innovation 20% = **60% of the score**. Two design decisions capture almost all of it, and almost no other team will make them:

**1. Proactive autonomy, not a chatbot.** Every other PS1 team builds a to-do app with a "suggest priorities" button — reactive, you have to open it. Clutch runs **unprompted**: Cloud Scheduler pings a Cloud Run endpoint on a cadence, the agent performs a *deadline sweep* on its own, decides if intervention is needed, and acts. That self-initiated action loop is what "agentic" actually means, and it's the single biggest differentiator.

**2. A real evaluation harness.** ADK ships a built-in eval framework that scores **tool trajectories** (did the agent call the right tools in the right order) and **response quality**. You define a golden set of deadline scenarios and run `adk eval` to produce hard numbers on agent reliability. Putting "our agent makes the correct intervention decision on 18/20 held-out scenarios; trajectory score 0.94" in your submission Doc is a rigor signal judges essentially never see at a hackathon — and it directly evidences Agentic Depth + Technical Implementation. This is your wheelhouse; lean into it.

Everything else (calendar, Maps, vision) is supporting cast.

---

## 2. Stack (all verified current, June 2026)

| Layer | Choice | Why |
|---|---|---|
| Agent framework | **Google ADK** (`google-adk`, Python 2.0 GA) | Code-first multi-agent orchestration, built-in tools, sessions, **native Cloud Run deploy**, and the eval harness. This is the spine. |
| Model | **`gemini-3-flash-preview`** | Free tier in the Gemini API (~1,500 req/day), Pro-level reasoning at Flash speed. Pro models are paid-only as of Apr 2026, so Flash is also the cost-correct call. Supports function calling + built-in tools. |
| Deploy | **Cloud Run** via `adk deploy cloud_run` | One command, auto-builds the container (no Dockerfile), satisfies the mandatory "deploy on Google Cloud" gate. |
| Autonomy trigger | **Cloud Scheduler** → Cloud Run | Cron-style pings that make the agent self-initiate. The mechanism behind "proactive." |
| Storage | **Firestore** (native mode) | Tasks + run history. Generous free tier, zero schema overhead. |
| Calendar | **Google Calendar API** (OAuth in *testing* mode) | The tangible "the agent DID something" proof. Testing mode + your own account = no OAuth verification headache. |
| Maps (optional) | **Grounding with Google Maps** (built-in Gemini 3 tool) | For tasks with a location/commute, the agent reasons about travel time. Cheap extra Google-tech credit. |
| Vision (optional) | **Gemini multimodal** | Paste a screenshot/PDF of a syllabus or assignment → auto-extract tasks + deadlines. Strong demo moment if time allows. |
| Frontend | **React + Vite + Tailwind** calling the agent's `/run` API | Task board + live agent-activity trace + the interventions it took. The polish layer for Product Experience (10%). |

**Google-tech checklist for the 15% criterion:** Gemini 3 Flash · ADK · Cloud Run · Firestore · Cloud Scheduler · Calendar API · Maps grounding. Call every one out explicitly in the submission Doc.

---

## 3. Architecture

```
Cloud Scheduler ──(cron ping)──►  Cloud Run service (ADK app)
                                         │
   React UI ──(/run API)──────────►  root_agent  "Clutch"  (LlmAgent, routes)
                                         │
                 ┌───────────────────────┼───────────────────────┐
                 ▼                       ▼                        ▼
            Planner               deadline_sweep            (interactive
          (LlmAgent)            (SequentialAgent)            chat & ingest)
       decompose tasks                 │
                          ┌────────────┼────────────┐
                          ▼            ▼             ▼
                    Risk Sentinel  Scheduler    Intervenor
                    rank slip-risk  block time   take ONE action
                          │            │             │
                          └──── tools ─┴──── tools ───┘
                                         │
                    Firestore  ·  Google Calendar API  ·  Maps grounding
```

**Multi-agent design (this is the agentic depth):**
- **`root_agent` (Clutch)** — LLM-routed orchestrator. Handles interactive requests (brain-dump ingest, questions) and can invoke the sweep on demand.
- **`deadline_sweep` (SequentialAgent)** — the autonomous pipeline triggered by Cloud Scheduler. Runs three specialists in order:
  - **Risk Sentinel** — scores every task's slip-risk = f(deadline proximity, effort remaining, calendar capacity). Surfaces the single most at-risk task.
  - **Scheduler** — reads calendar free/busy and **creates real time-blocks** for the at-risk work.
  - **Intervenor** — takes exactly **one** concrete action on the at-risk task: draft an extension email, generate a starter draft/outline, or fire an escalating nudge.
- **Planner** — on ingest, decomposes each task into subtasks with effort estimates.

Why specialists instead of one mega-prompt: narrow agents with clear tool sets produce clean, gradeable trajectories — which is exactly what the eval harness rewards and what makes the demo legible.

---

## 4. Tools (Python functions the agents call)

| Tool | Owner agent | Does |
|---|---|---|
| `add_tasks(raw_text)` | root | Parse a messy brain-dump (or vision input) into structured tasks → Firestore |
| `list_tasks()` | all | Return current open tasks |
| `decompose_task(task_id)` | Planner | Subtasks + per-subtask effort estimate (minutes) |
| `assess_risk()` | Sentinel | Rank tasks by slip-risk; return the top at-risk task + reasoning |
| `get_calendar_availability(start, end)` | Scheduler | Free/busy from Google Calendar |
| `create_time_block(task_id, start, end)` | Scheduler | Create a real Calendar event |
| `draft_email(task_id, intent)` | Intervenor | Draft extension request / status update |
| `generate_starter(task_id)` | Intervenor | Produce the first draft/outline so the task is no longer a blank page |
| `send_nudge(task_id, level)` | Intervenor | Escalating reminder (log now; wire to email/push later) |

Plus built-in Gemini tools where useful: **Maps grounding** (commute-aware scheduling), **Google Search / URL context** (research-type tasks).

The scaffold ships runnable in-memory stubs for all of these so the agent works on first run; swap in Firestore + Calendar incrementally.

---

## 5. Evaluation harness — your differentiator

Build a golden eval set of deadline scenarios, each specifying the **expected tool trajectory** and a reference outcome. Examples:
- *"3 tasks, one due in 2h with ~4h work left"* → expect `assess_risk` → `get_calendar_availability` → `draft_email`. The at-risk task must be the 2h one.
- *"All tasks comfortably ahead of schedule"* → expect `assess_risk` and **no** intervention (don't over-act).
- *"Two tasks tie on deadline"* → expect the agent to break the tie by effort remaining.

Run with `adk eval`, scoring `tool_trajectory_avg_score` (IN_ORDER) + `final_response_match_v2` (LLM-judge) + `multi_turn_trajectory_quality_v1`.

**Canonical way to author cases without schema drift:** run `adk web`, interact to create a correct session, open the **Eval tab**, "Add current session" to save it as a case, then run `adk eval` headless / in CI. Report the aggregate scores in the Doc and the demo.

---

## 6. Deploy path (the mandatory gate)

```bash
# one-time
gcloud auth login
gcloud config set project <PROJECT_ID>
export GOOGLE_CLOUD_PROJECT=<PROJECT_ID>
export GOOGLE_CLOUD_LOCATION=us-central1

# deploy the agent to Cloud Run (auto-builds container, no Dockerfile)
adk deploy cloud_run \
  --project=$GOOGLE_CLOUD_PROJECT \
  --region=$GOOGLE_CLOUD_LOCATION \
  --service_name=clutch \
  --with_ui
# choose "allow unauthenticated" so the public demo link works
```

**Autonomy via Cloud Scheduler** (this is what makes it proactive — don't skip it):
```bash
gcloud scheduler jobs create http clutch-sweep \
  --schedule="0 */3 * * *" \
  --uri="https://<CLOUD_RUN_URL>/run" \
  --http-method=POST \
  --message-body='{"appName":"clutch","userId":"demo","sessionId":"sweep","newMessage":{"role":"user","parts":[{"text":"Run a deadline sweep and intervene on the most at-risk task."}]}}' \
  --location=$GOOGLE_CLOUD_LOCATION
```

Deploy a hello-agent **first**, before building features, to prove the pipeline while there's no pressure.

---

## 7. The 90-second demo script (what judges actually see)

1. Paste a messy brain-dump: *"submit DBMS assignment Tue, internship application Friday, prep DSA interview Thursday 6pm, pay hostel fee."* → Clutch parses + decomposes them live (Planner trace visible).
2. Trigger a sweep (or show the Cloud Scheduler log proving it already ran on its own). Risk Sentinel flags the DSA interview as most at-risk and **says why**.
3. Scheduler shows two real calendar blocks it just created. Cut to the actual Google Calendar to prove it.
4. Intervenor shows the action it took unprompted — a drafted prep plan + a calendar hold, or a drafted extension email for the assignment.
5. Close on the **eval dashboard**: "correct intervention on 18/20 held-out scenarios, trajectory score 0.94." Land the line: *this isn't a reminder app — it's an agent that acts before you fail, and here's the proof it acts correctly.*

---

## 8. Rubric → build order (do the high-value things first)

| Criterion | % | Earned by | Priority |
|---|---|---|---|
| Agentic Depth | 20 | Multi-agent sweep + autonomy + eval scores | **1** |
| Problem Solving & Impact | 20 | The proactive intervention (prevents the miss) | **1** |
| Innovation & Creativity | 20 | Self-initiating agent + action, not suggestion | **2** |
| Usage of Google Tech | 15 | The full stack in §2, named explicitly | **2** |
| Product Experience & Design | 10 | Clean React UI + legible activity trace | **3** |
| Technical Implementation | 10 | Working ADK loop + eval harness | **2** |
| Completeness & Usability | 5 | Live link works end-to-end through eval | **3** |

Build order: deploy stub → ingest + Planner → Sentinel + Scheduler (real Calendar) → Intervenor → Cloud Scheduler autonomy → eval set → React polish → Doc + Final Submit.

---

## 9. Submission Doc outline (fill as you build, spread edits — they check version history)
1. **Problem Statement Selected** — PS1, and the specific gap: reminders are passive; Clutch acts.
2. **Solution Overview** — the autonomous deadline agent; the sweep loop.
3. **Key Features** — proactive sweeps, multi-agent triage, real calendar actions, drafted interventions, eval-proven reliability.
4. **Technologies Used** — Python, ADK, React/Vite/Tailwind, Firestore.
5. **Google Technologies Utilized** — Gemini 3 Flash, ADK, Cloud Run, Cloud Scheduler, Calendar API, Maps grounding.
6. **Evaluation results** — the trajectory/intervention scores (your differentiator — give it its own section).

---

## 10. Known gotchas
- Cloud Run deploy is the gate — no other host qualifies. Prove it hour one.
- Calendar OAuth: stay in **testing mode**, demo with your own account; full verification eats hours you don't have.
- Secrets (Gemini key, OAuth creds) → Cloud Run env vars / Secret Manager, never in the repo.
- ADK had breaking API changes at 2.0 — if an import or signature in the scaffold doesn't match your installed version, check `google.github.io/adk-docs` for the current surface.
- Keep the Cloud Run link and the public Google Doc alive through the whole eval window.
- **Final Submit** on BlockseBlock is compulsory and irreversible — without it your entry sits in Drafts.
