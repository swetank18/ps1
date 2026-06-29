"""
Clutch — autonomous deadline agent (Vibe2Ship PS1).

Multi-agent ADK app:
  root_agent (Clutch)         LLM-routed orchestrator: ingest, chat, can trigger the sweep
  ├─ planner                  decomposes tasks into subtasks + effort estimates
  └─ deadline_sweep           SequentialAgent run autonomously by Cloud Scheduler:
       ├─ risk_sentinel       ranks tasks by slip-risk, surfaces the worst one
       ├─ scheduler           reads calendar, creates real time-blocks
       └─ intervenor          takes ONE concrete action on the at-risk task

NOTE ON ADK VERSIONS: ADK 2.0 introduced breaking changes. If an import or
signature below doesn't match your installed `google-adk`, check the current
surface at https://google.github.io/adk-docs/ . The shape here follows the
2.x LlmAgent / SequentialAgent pattern.
"""

from google.adk.agents import LlmAgent, SequentialAgent

from . import tools

MODEL = "gemini-3-flash-preview"  # free-tier capable, agentic; swap to gemini-flash-latest if needed


planner = LlmAgent(
    name="planner",
    model=MODEL,
    description="Breaks tasks into concrete subtasks with effort estimates.",
    instruction=(
        "You turn each open task into 2-5 concrete subtasks, each with an effort "
        "estimate in minutes. Be realistic, not optimistic. Call list_tasks to see "
        "what exists, then decompose_task for each task that lacks subtasks. "
        "Never invent tasks the user didn't give you."
    ),
    tools=[tools.list_tasks, tools.decompose_task],
)


risk_sentinel = LlmAgent(
    name="risk_sentinel",
    model=MODEL,
    description="Scores slip-risk for every task and names the single most at-risk one.",
    instruction=(
        "Call assess_risk. Slip-risk rises with deadline proximity, effort remaining, "
        "and how little free calendar time exists before the deadline. Identify exactly "
        "ONE task as most at-risk and state, in one sentence, WHY. If every task is "
        "comfortably ahead of schedule, say so and recommend NO intervention — do not "
        "manufacture urgency."
    ),
    tools=[tools.list_tasks, tools.assess_risk],
)


scheduler = LlmAgent(
    name="scheduler",
    model=MODEL,
    description="Protects time for at-risk work by creating real calendar blocks.",
    instruction=(
        "For the at-risk task identified by risk_sentinel, call get_calendar_availability "
        "to find free slots before its deadline, then create_time_block to reserve enough "
        "time for the remaining effort. Prefer focused blocks of 60-90 minutes. Do not "
        "double-book over existing events."
    ),
    tools=[tools.get_calendar_availability, tools.create_time_block],
)


intervenor = LlmAgent(
    name="intervenor",
    model=MODEL,
    description="Takes exactly one concrete action to save the at-risk task.",
    instruction=(
        "Take exactly ONE high-leverage action on the at-risk task, then stop:\n"
        "- If it's genuinely undeliverable in time: draft_email to request an extension or "
        "send a status update.\n"
        "- If the blocker is a blank page: generate_starter to produce a first draft/outline.\n"
        "- Otherwise: send_nudge with an escalation level matching the urgency.\n"
        "Explain the action you took in one sentence. Never take more than one action per sweep."
    ),
    tools=[tools.draft_email, tools.generate_starter, tools.send_nudge],
)


# The autonomous pipeline triggered by Cloud Scheduler.
deadline_sweep = SequentialAgent(
    name="deadline_sweep",
    description="Autonomous sweep: assess risk, protect time, intervene on the worst task.",
    sub_agents=[risk_sentinel, scheduler, intervenor],
)


# Interactive orchestrator. This is the entrypoint ADK serves.
root_agent = LlmAgent(
    name="clutch",
    model=MODEL,
    description="Proactive deadline agent that plans, schedules, and intervenes before deadlines slip.",
    instruction=(
        "You are Clutch, a proactive productivity agent. You do not just remind — you act.\n"
        "- When the user gives a brain-dump of tasks, call add_tasks to capture them, then "
        "delegate to `planner` to decompose them.\n"
        "- When the user (or a scheduled trigger) asks to run a deadline sweep, delegate to "
        "`deadline_sweep`, which will assess risk, block time, and intervene.\n"
        "- Be concise. Surface what you did and why. Always prefer taking a concrete action "
        "over offering advice."
    ),
    sub_agents=[planner, deadline_sweep],
    tools=[tools.add_tasks, tools.list_tasks],
)
