# Clutch — autonomous deadline agent

Vibe2Ship PS1 ("The Last-Minute Life Saver"). An ADK multi-agent app on Gemini 3 Flash
that runs deadline sweeps on a schedule and **acts before you miss things** — blocking
calendar time, drafting the email, or generating the first draft. See `PRODUCT_SPEC.md`
for the full design and rationale.

## Layout
```
clutch/
  clutch/
    __init__.py
    agent.py        # root_agent + planner + deadline_sweep (sentinel→scheduler→intervenor)
    tools.py        # runnable in-memory tools; TODOs to swap in Firestore + Calendar
  eval/
    deadline_sweep.evalset.json
  requirements.txt
  .env.example
```

## 1. Setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then paste your AI Studio key into GOOGLE_API_KEY
```

## 2. Run locally (dev UI with the trajectory trace)
```bash
adk web            # run from the parent dir containing the clutch/ package
# open http://localhost:8000 → select "clutch" → chat
# the Trace/Graph tab shows the agent's tool trajectory — this is your activity view
```
Try: paste *"submit DBMS assignment Tue, prep DSA interview Thursday 6pm, pay hostel fee"*,
then ask *"run a deadline sweep."*

## 3. Evaluate (the differentiator — produce reliability numbers)
Author cases canonically in the `adk web` **Eval tab** ("Add current session"), then:
```bash
adk eval clutch eval/deadline_sweep.evalset.json --print_detailed_results
```
Report aggregate trajectory + intervention scores in the submission Doc.

## 4. Deploy to Cloud Run (mandatory gate)
```bash
gcloud auth login
gcloud config set project $GOOGLE_CLOUD_PROJECT
adk deploy cloud_run \
  --project=$GOOGLE_CLOUD_PROJECT \
  --region=$GOOGLE_CLOUD_LOCATION \
  --service_name=clutch \
  --with_ui
# choose "allow unauthenticated" for a public demo link
```

## 5. Make it autonomous (Cloud Scheduler → the agent acts on its own)
```bash
gcloud scheduler jobs create http clutch-sweep \
  --schedule="0 */3 * * *" \
  --uri="https://<CLOUD_RUN_URL>/run" --http-method=POST \
  --location=$GOOGLE_CLOUD_LOCATION \
  --message-body='{"appName":"clutch","userId":"demo","sessionId":"sweep","newMessage":{"role":"user","parts":[{"text":"Run a deadline sweep and intervene on the most at-risk task."}]}}'
```

## Build order
deploy stub → ingest + planner → sentinel + scheduler (real Calendar) → intervenor →
Cloud Scheduler autonomy → eval set → React frontend → Doc + **Final Submit** on BlockseBlock.

## Notes
- Tools ship as in-memory stubs so the agent runs immediately; swap Firestore + Calendar incrementally.
- Calendar: OAuth in **testing mode** with your own account — avoids verification.
- ADK 2.0 changed some APIs; if an import breaks, check https://google.github.io/adk-docs/.
- Secrets go in Cloud Run env vars / Secret Manager, never in the repo.
