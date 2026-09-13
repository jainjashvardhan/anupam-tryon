#!/usr/bin/env bash
# Deploys the try-on backend to Google Cloud Run.
#
# Reads GEMINI_API_KEY and SHOP_PIN from .env and passes them as Cloud Run environment
# variables at deploy time - they are never baked into the container image and .env is
# never uploaded (see .gcloudignore).
#
# Usage:  ./deploy.sh
set -euo pipefail

cd "$(dirname "$0")"

PROJECT_ID="${PROJECT_ID:-anupam-mall}"
REGION="${REGION:-asia-south1}"      # Mumbai - closest to India
SERVICE="${SERVICE:-shop-tryon}"

# Pull config out of .env without printing it.
set -a; source .env; set +a
: "${SHOP_PIN:?SHOP_PIN missing from .env}"
GCP_LOCATION="${GCP_LOCATION:-global}"

echo "Deploying $SERVICE to project $PROJECT_ID ($REGION)..."

gcloud run deploy "$SERVICE" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --source . \
  --allow-unauthenticated \
  --memory 512Mi \
  --timeout 120 \
  --max-instances 3 \
  --set-env-vars "GCP_PROJECT=${PROJECT_ID},GCP_LOCATION=${GCP_LOCATION},SHOP_PIN=${SHOP_PIN},STORAGE_BUCKET=${STORAGE_BUCKET:-}"

echo
echo "Deployed. URL:"
gcloud run services describe "$SERVICE" \
  --project "$PROJECT_ID" --region "$REGION" \
  --format 'value(status.url)'
echo "Shop PIN: ${SHOP_PIN}"
