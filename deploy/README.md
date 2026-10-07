# Deploying Rusty to GCP

Local development is unchanged: `APP_ENV=development` (the default) reads `backend/.env` and uses Ollama, the Docker Postgres and dev tokens. Nothing in this folder is needed to run locally.

GCP environments are selected by `APP_ENV`, and each has its own env files:

| Environment | Backend config | Frontend config | LLM | Ingestion |
|---|---|---|---|---|
| `development` | `backend/.env` | none (Vite proxy + dev tokens) | Ollama | in-process |
| `staging` | `backend/.env.staging` | `frontend/.env.staging` | Gemini | Cloud Storage + `rusty-ingest` job |
| `production` | `backend/.env.production` | `frontend/.env.production` | Gemini | Cloud Storage + `rusty-ingest` job |

The real env files are gitignored. Create each one from its `*.template` and replace every `<...>` placeholder. The app refuses to start outside development while any placeholder, emulator host, Ollama provider, `*` origin or short `SECRET_KEY` remains, and `deploy.sh` refuses to deploy a file that still contains `<...>`.

## What goes where

- **Non-secret runtime config** (model names, project, bucket, CORS, pool sizes): `backend/.env.<env>`. `deploy.sh` sends it to Cloud Run as env vars.
- **Secrets** (`DATABASE_URL`, `SECRET_KEY`, `GEMINI_API_KEY`, `SMTP_PASSWORD`): Secret Manager only. List them in `DEPLOY_SECRETS`. They are never sent as plain env vars, even if they are in the file.
- **Deploy settings** (`DEPLOY_*`: image, Cloud SQL instance, service accounts, instance counts): also in `backend/.env.<env>`. The app ignores them.
- **Frontend build config** (`VITE_API_URL`, Firebase web config): `frontend/.env.<env>`. These values are public, but restrict the browser API key to your Hosting domains.

### Gemini authentication

| `GEMINI_AUTH` | How it authenticates | Extra setup |
|---|---|---|
| `adc` (recommended) | Vertex AI with the Cloud Run service account; no key to rotate | `roles/aiplatform.user` on the API and jobs service accounts |
| `api_key` | Gemini API key | Create secret `GEMINI_API_KEY`, add `GEMINI_API_KEY=GEMINI_API_KEY:latest` to `DEPLOY_SECRETS`, grant accessor to both service accounts |

Models are set with `GEMINI_MODEL` and `VERTEX_EMBED_MODEL` (`EMBED_DIM` must stay 1024 to match `vector(1024)`). The data-residency choice is `VERTEX_AI_LOCATION`: `global` is the cost option, `asia-south1` with `gemini-3.5-flash` is India-resident. See [plan §5](../doc/GCP_DEPLOYMENT_PLAN.md).

> Local and cloud embeddings are different vector spaces. Re-upload every PDF in each GCP environment; never copy the local `chunks` table.

## One-time setup per environment

Provision the resources by following [doc/GCP_DEPLOYMENT_PLAN.md](../doc/GCP_DEPLOYMENT_PLAN.md) §11.2–11.10: project and APIs, Artifact Registry `rusty`, service accounts, Cloud SQL `rusty-db`, the secrets `DATABASE_URL` and `SECRET_KEY`, Firestore, Firebase (register the web app), and the bucket `<PROJECT_ID>-textbooks`. This script deliberately does not create them. These IAM grants are needed **in addition to** the plan:

- The jobs service account needs `roles/secretmanager.secretAccessor` on `SECRET_KEY` too. Jobs load the same validated config as the API.
- After the first `jobs` step: grant the API service account `roles/run.jobsExecutor` on `rusty-ingest` (plan §12.3).

Then:

```bash
cp backend/.env.staging.template backend/.env.staging     # fill in
cp frontend/.env.staging.template frontend/.env.staging   # fill in
gcloud auth login && firebase login
```

## Deploy

From the repo root, in Cloud Shell or Git Bash with `gcloud`, `firebase` and `npm` installed:

```bash
deploy/deploy.sh staging                  # build, migrate, jobs, api, frontend
deploy/deploy.sh staging api frontend     # just some steps
TAG=abc1234 deploy/deploy.sh production api   # redeploy an existing image
```

| Step | Does |
|---|---|
| `build` | `gcloud builds submit` with [cloudbuild.yaml](../cloudbuild.yaml). The image is built from the repo root with [backend/Dockerfile](../backend/Dockerfile) |
| `migrate` | Creates or updates the `rusty-migrate` job and runs `alembic upgrade head`. This is the only way schema reaches GCP; `create_all` runs only in development |
| `jobs` | Creates or updates `rusty-ingest` (`python -m rag.ingestion.job`) |
| `api` | Deploys `rusty-api` and checks `/health` |
| `frontend` | Builds with `vite --mode <env>` and deploys Firebase Hosting plus `firestore.rules`. If `VITE_API_URL` is empty, it uses the deployed `rusty-api` URL |

Check after deploying: `curl $API_URL/ready` returns 200 when the database is reachable and 503 when it is not.

### How PDF ingestion works on GCP

When `TEXTBOOK_BUCKET` is set, an admin upload is stored at `gs://<bucket>/textbooks/<textbook_id>.pdf`, the textbook row is set to `processing`, and one `rusty-ingest` execution starts. Each execution processes every textbook still in `processing`, so retries and duplicate executions are safe (a Postgres advisory lock is taken per textbook). "Regenerate summaries" re-runs the whole ingestion from the stored PDF. If a job dies, run `gcloud run jobs execute rusty-ingest --region asia-south1` to pick up anything left in `processing`.

## Still open before production (not GCP wiring)

From the plan's blocker list, these remain:

- **B9**: safeguarding flags are not written to Firestore, and email alerts are a log-only stub. The `SMTP_*` settings are placeholders until then; the alert receiver and Eventarc trigger are in plan §12.4.
- **B11**: the admin user-management endpoints (`/admin/users`, `/admin/onboard`, …) don't exist. Students can't be onboarded in GCP without them or a bootstrap script.
- The login lockout is held in process memory per worker. Move it to Firestore or Memorystore.
- A GitHub Actions deploy workflow with Workload Identity Federation (plan §17).
