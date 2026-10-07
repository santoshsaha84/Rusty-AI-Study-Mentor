#!/usr/bin/env bash
# Deploy Rusty to GCP from the per-environment files:
#   backend/.env.<env>    app runtime config + DEPLOY_* settings  (from backend/.env.<env>.template)
#   frontend/.env.<env>   frontend build config                    (from frontend/.env.<env>.template)
#
# Usage (repo root, Cloud Shell or any shell with gcloud/firebase/npm, logged in):
#   deploy/deploy.sh <staging|production> [step...]
# Steps (default: all, in this order):
#   build     build + push the API image with Cloud Build
#   migrate   create/update the rusty-migrate job and run `alembic upgrade head`
#   jobs      create/update the rusty-ingest job
#   api       deploy the rusty-api Cloud Run service
#   frontend  build the PWA and deploy Firebase Hosting + Firestore rules
#
# Resources (project, Cloud SQL, bucket, service accounts, secrets) must already exist —
# see doc/GCP_DEPLOYMENT_PLAN.md sections 11.x. This script never creates them.
# TAG defaults to the current git commit; set TAG=... to redeploy an existing image.
set -euo pipefail

ENV_NAME="${1:-}"
shift || true
case "$ENV_NAME" in
  staging|production) ;;
  *) echo "usage: deploy/deploy.sh <staging|production> [build|migrate|jobs|api|frontend ...]" >&2; exit 1 ;;
esac
STEPS=("$@")
[ ${#STEPS[@]} -eq 0 ] && STEPS=(build migrate jobs api frontend)

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_ENV="$ROOT/backend/.env.$ENV_NAME"
FRONTEND_ENV="$ROOT/frontend/.env.$ENV_NAME"

die() { echo "ERROR: $*" >&2; exit 1; }

# Secrets never travel as plain env vars — Secret Manager only (DEPLOY_SECRETS).
SECRET_KEYS=" DATABASE_URL SECRET_KEY GEMINI_API_KEY SMTP_PASSWORD ENV_FILE "

# Parse KEY=VALUE lines (no `source`: values contain <, &, commas). Fills CFG_* vars + APP_KEYS.
APP_KEYS=()
load_env_file() {
  local file="$1" line key value
  [ -f "$file" ] || die "$file not found — copy ${file#"$ROOT"/}.template and fill it in."
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
    [[ "$line" == *=* ]] || continue
    key="${line%%=*}"; value="${line#*=}"
    [[ "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]] || die "bad key '$key' in $file"
    if [[ "$value" == *"<"*">"* ]]; then
      die "$key in ${file#"$ROOT"/} still holds a placeholder: $value"
    fi
    printf -v "CFG_$key" '%s' "$value"
    if [[ "$file" == "$BACKEND_ENV" && "$key" != DEPLOY_* && "$SECRET_KEYS" != *" $key "* ]]; then
      APP_KEYS+=("$key")
    fi
  done < "$file"
}

cfg() { local v="CFG_$1"; printf '%s' "${!v:-${2:-}}"; }

load_env_file "$BACKEND_ENV"
[ "$(cfg APP_ENV)" = "$ENV_NAME" ] || die "APP_ENV in $BACKEND_ENV must be '$ENV_NAME'"
[ "$(cfg LLM_PROVIDER)" = "vertex" ] || die "LLM_PROVIDER must be 'vertex' on GCP"

PROJECT="$(cfg GCP_PROJECT_ID)"; [ -n "$PROJECT" ] || die "GCP_PROJECT_ID is empty"
REGION="$(cfg GCP_REGION asia-south1)"
IMAGE="$(cfg DEPLOY_IMAGE "$REGION-docker.pkg.dev/$PROJECT/rusty/api")"
SQL_INSTANCE="$(cfg DEPLOY_SQL_INSTANCE "$PROJECT:$REGION:rusty-db")"
API_SERVICE="$(cfg DEPLOY_API_SERVICE rusty-api)"
API_SA="$(cfg DEPLOY_API_SA "rusty-api@$PROJECT.iam.gserviceaccount.com")"
JOBS_SA="$(cfg DEPLOY_JOBS_SA "rusty-jobs@$PROJECT.iam.gserviceaccount.com")"
INGEST_JOB="$(cfg INGEST_JOB_NAME rusty-ingest)"
SECRETS="$(cfg DEPLOY_SECRETS "DATABASE_URL=DATABASE_URL:latest,SECRET_KEY=SECRET_KEY:latest")"
if [ "$(cfg GEMINI_AUTH adc)" = "api_key" ] && [[ "$SECRETS" != *GEMINI_API_KEY=* ]]; then
  die "GEMINI_AUTH=api_key: add GEMINI_API_KEY=GEMINI_API_KEY:latest to DEPLOY_SECRETS"
fi
TAG="${TAG:-$(git -C "$ROOT" rev-parse --short HEAD)}"
if [ -n "$(git -C "$ROOT" status --porcelain 2>/dev/null)" ] && [[ " ${STEPS[*]} " == *" build "* ]]; then
  echo "WARNING: uncommitted changes will be built into image tag $TAG" >&2
fi

# Cloud Run env-vars file (YAML) from the app keys of backend/.env.<env>.
ENV_YAML="$(mktemp)"
trap 'rm -f "$ENV_YAML"' EXIT
yaml_quote() { local v="${1//\\/\\\\}"; printf '"%s"' "${v//\"/\\\"}"; }
{
  for key in "${APP_KEYS[@]}"; do
    printf '%s: %s\n' "$key" "$(yaml_quote "$(cfg "$key")")"
  done
  printf 'WEB_CONCURRENCY: %s\n' "$(yaml_quote "$(cfg DEPLOY_WEB_CONCURRENCY 2)")"
} > "$ENV_YAML"

GC=(gcloud --project="$PROJECT" --quiet)
echo "==> $ENV_NAME | project $PROJECT | region $REGION | image $IMAGE:$TAG | steps: ${STEPS[*]}"

step_build() {
  "${GC[@]}" builds submit "$ROOT" --config="$ROOT/cloudbuild.yaml" \
    --substitutions="_IMAGE=$IMAGE,_TAG=$TAG"
}

job_common=(--image="$IMAGE:$TAG" --region="$REGION" --service-account="$JOBS_SA"
  --set-cloudsql-instances="$SQL_INSTANCE" --set-secrets="$SECRETS" --env-vars-file="$ENV_YAML")

step_migrate() {
  "${GC[@]}" run jobs deploy rusty-migrate "${job_common[@]}" \
    --command=alembic --args=upgrade,head --task-timeout=10m --max-retries=0
  "${GC[@]}" run jobs execute rusty-migrate --region="$REGION" --wait
}

step_jobs() {
  "${GC[@]}" run jobs deploy "$INGEST_JOB" "${job_common[@]}" \
    --command=python --args=-m,rag.ingestion.job \
    --cpu=2 --memory=4Gi --task-timeout=60m --max-retries=1
}

step_api() {
  "${GC[@]}" run deploy "$API_SERVICE" \
    --image="$IMAGE:$TAG" --region="$REGION" --service-account="$API_SA" \
    --add-cloudsql-instances="$SQL_INSTANCE" --set-secrets="$SECRETS" --env-vars-file="$ENV_YAML" \
    --cpu=1 --memory=1Gi --concurrency=40 --timeout=120 --cpu-boost \
    --min-instances="$(cfg DEPLOY_MIN_INSTANCES 0)" --max-instances="$(cfg DEPLOY_MAX_INSTANCES 2)" \
    --allow-unauthenticated
  local url
  url="$("${GC[@]}" run services describe "$API_SERVICE" --region="$REGION" --format='value(status.url)')"
  echo "API: $url"
  curl -fsS "$url/health" && echo
}

step_frontend() {
  load_env_file "$FRONTEND_ENV"
  local api_url
  api_url="$(cfg VITE_API_URL)"
  if [ -z "$api_url" ]; then
    api_url="$("${GC[@]}" run services describe "$API_SERVICE" --region="$REGION" --format='value(status.url)')"
  fi
  [ -n "$api_url" ] || die "VITE_API_URL is empty and $API_SERVICE is not deployed yet"
  (cd "$ROOT/frontend" && npm ci && VITE_API_URL="$api_url" npm run "build:$ENV_NAME")
  (cd "$ROOT" && firebase deploy --only hosting,firestore:rules --project "$PROJECT" --non-interactive)
}

for s in "${STEPS[@]}"; do
  case "$s" in
    build|migrate|jobs|api|frontend) echo "--> $s"; "step_$s" ;;
    *) die "unknown step '$s'" ;;
  esac
done
echo "==> done"
