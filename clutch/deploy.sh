#!/usr/bin/env bash
# One-command Cloud Run deploy for Clutch.
# Prereqs: gcloud installed + `gcloud auth login` done, billing enabled on the project.
#
#   GOOGLE_CLOUD_PROJECT=my-proj ./deploy.sh
#   (or run `gcloud config set project my-proj` first)
set -euo pipefail
cd "$(dirname "$0")"

PROJECT="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
SERVICE="${SERVICE:-clutch}"

if [[ -z "${PROJECT}" || "${PROJECT}" == "(unset)" ]]; then
  echo "✖ No project set. Run: gcloud config set project <PROJECT_ID>  (or export GOOGLE_CLOUD_PROJECT)"; exit 1
fi

# Pull the Gemini key from .env (never bake it into the image).
KEY="$(grep -E '^GOOGLE_API_KEY=' .env 2>/dev/null | cut -d= -f2- || true)"
if [[ -z "${KEY}" || "${KEY}" == "your-key-here" ]]; then
  echo "✖ GOOGLE_API_KEY missing in .env. Run: cp .env.example .env  and paste your AI Studio key."; exit 1
fi
MODEL="$(grep -E '^MODEL=' .env 2>/dev/null | cut -d= -f2- || echo gemini-3-flash-preview)"

echo "▸ Project : ${PROJECT}"
echo "▸ Region  : ${REGION}"
echo "▸ Service : ${SERVICE}"

echo "▸ Enabling required APIs…"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  --project "${PROJECT}" --quiet

echo "▸ Building & deploying (Cloud Build → Cloud Run)…"
gcloud run deploy "${SERVICE}" \
  --source . \
  --project "${PROJECT}" \
  --region "${REGION}" \
  --platform managed \
  --allow-unauthenticated \
  --memory 1Gi \
  --set-env-vars "GOOGLE_API_KEY=${KEY},GOOGLE_GENAI_USE_VERTEXAI=FALSE,MODEL=${MODEL}" \
  --quiet

URL="$(gcloud run services describe "${SERVICE}" --project "${PROJECT}" --region "${REGION}" --format='value(status.url)')"
echo
echo "✅ Deployed: ${URL}"
echo "   • App / UI       : ${URL}"
echo "   • Autonomy trigger: POST ${URL}/api/sweep/run"
echo
echo "Next — make it autonomous with Cloud Scheduler:"
echo "  gcloud scheduler jobs create http clutch-sweep \\"
echo "    --schedule='0 */3 * * *' --uri='${URL}/api/sweep/run' \\"
echo "    --http-method=POST --location='${REGION}'"
