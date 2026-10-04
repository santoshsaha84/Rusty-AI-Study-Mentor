# Local Launch Guide — Rusty AI Study Mentor

How to get Rusty running on a fresh computer (Windows, macOS or Linux) for local development and testing. Everything runs locally: no GCP account, no real Firebase project, no API keys.

---

## 1. Prerequisites

Install these before you start:

| Tool | Version | Notes |
|---|---|---|
| Git | any recent | |
| Docker Desktop | any recent | Must be **running** (it hosts PostgreSQL + Firebase emulators) |
| Python | **3.11 or 3.12** | **3.13 does not work** — pinned `asyncpg`/`pydantic` have no 3.13 builds. Production and CI use 3.11. If you only have 3.13, the setup script can fetch 3.11 via [uv](https://docs.astral.sh/uv/) |
| Node.js | **20+** | Comes with npm |
| Ollama | any recent | [ollama.com](https://ollama.com). Runs the local LLM + embedding models |

Hardware: about **4 GB free disk** for the two models (~3.1 GB) and Docker images, and **8 GB+ RAM** recommended.

Check what you have:

```bash
git --version
docker --version && docker info --format "{{.ServerVersion}}"   # second command fails if Docker isn't running
python --version          # Windows: py -0p  lists all installed versions
node --version
ollama --version
```

---

## 2. Get the code

```bash
git clone git@github.com:JampaniTejasai/Rusty-AI-Study-Mentor.git
cd Rusty-AI-Study-Mentor
```

---

## 3. One-command setup (recommended)

The setup script checks the prerequisites, pulls the Ollama models, starts Docker services, creates the Python virtualenv, writes `backend/.env`, runs database migrations and installs frontend packages. It is safe to re-run.

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

**macOS / Linux:**
```bash
./scripts/setup.sh
```

**If port 5432 is already in use** (another PostgreSQL install or container), use a different host port:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -PostgresPort 5433   # Windows
```
```bash
POSTGRES_PORT=5433 ./scripts/setup.sh                                            # macOS / Linux
```

When it prints `Setup complete`, go to [Start the app](#5-start-the-app).

---

## 4. Manual setup (if you prefer, or the script fails)

Run these from the repo root.

### 4.1 Ollama models
```bash
ollama pull qwen2.5:3b                # chat model, ~1.9 GB
ollama pull snowflake-arctic-embed2   # embedding model, ~1.2 GB (1024-dim)
```

### 4.2 PostgreSQL + Firebase emulators
```bash
docker compose up -d
```
If 5432 is taken, set `POSTGRES_PORT` first (`$env:POSTGRES_PORT="5433"` in PowerShell, `export POSTGRES_PORT=5433` in bash) and use **every** `docker compose` command with that same variable set.

### 4.3 Backend
```bash
cd backend
python3.11 -m venv venv               # Windows: py -3.11 -m venv venv
source venv/bin/activate              # Windows: venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
```

Put the repo root on the venv's import path (needed on Windows, harmless elsewhere — see [Troubleshooting](#backend-fails-with-modulenotfounderror-no-module-named-rag)):
```bash
python -c "import sysconfig, pathlib; pathlib.Path(sysconfig.get_paths()['purelib'], 'rusty_repo_root.pth').write_text(str(pathlib.Path('..').resolve()))"
```

Create the config file and set a secret key:
```bash
cp .env.template .env                 # Windows: copy .env.template .env
python -c "import secrets; print(secrets.token_hex(32))"
```
Edit `backend/.env`: paste the printed value into `SECRET_KEY=`, and if you changed the Postgres port, change `localhost:5432` in `DATABASE_URL` to match.

Run migrations:
```bash
alembic upgrade head
```

### 4.4 Frontend
```bash
cd ../frontend
npm install
```

---

## 5. Start the app

Docker services and Ollama must be running (Ollama usually starts automatically; if not, run `ollama serve`). Then open **two terminals**:

**Terminal 1 — backend (port 8000):**
```bash
cd backend
source venv/bin/activate              # Windows: venv\Scripts\activate
uvicorn app.main:app --port 8000 --no-server-header --reload
```
Wait for `Application startup complete.`

**Terminal 2 — frontend (port 5173):**
```bash
cd frontend
npm run dev
```

Open **http://localhost:5173**.

After a reboot, only these are needed: start Docker Desktop, `docker compose up -d` (with `POSTGRES_PORT` set if you use a non-default port), then the two terminals above.

---

## 6. Log in

In local development (`APP_ENV=development` in `backend/.env`) these fixed accounts are built in. They do not work in production.

| Role | Student ID | PIN |
|---|---|---|
| Student (Class 8) | `KHEL-2026-001` | `1234` |
| Teacher | `KHEL-2026-T01` | `9999` |
| Admin | `KHEL-2026-ADM1` | `0000` |

Login is rate-limited to **5 attempts per minute**; wait a minute if you get "too many requests".

---

## 7. Load a textbook (required before asking questions)

Rusty only answers from uploaded textbooks, so a fresh install answers nothing until you add one. A sample Class 8 maths book is included at `rag/data/maths-08.pdf`.

Upload it either through the admin screens (log in as Admin) or with curl:

```bash
curl -X POST http://localhost:8000/admin/upload-pdf \
  -H "Authorization: Bearer dev-admin-token" \
  -F "file=@rag/data/maths-08.pdf;type=application/pdf" \
  -F "class_num=8" \
  -F "subject=mathematics"
```

Ingestion runs in the background (several minutes for a full book on a laptop CPU). Check progress:

```bash
curl -s http://localhost:8000/admin/textbooks -H "Authorization: Bearer dev-admin-token"
```

When the status is `ready`, log in as the Class 8 student and ask a maths question.

---

## 8. Verify everything works

| Check | Command / URL | Expected |
|---|---|---|
| Backend health | http://localhost:8000/health | `{"status":"ok"}` |
| Frontend → backend proxy | http://localhost:5173/api/health | `{"status":"ok"}` |
| Firebase emulator UI | http://localhost:4000 | Emulator dashboard |
| Backend tests | `cd backend && python -m pytest tests/ -q` (venv active) | all pass |
| Frontend tests | `cd frontend && npm test` | all pass |

---

## 9. Ports used

| Port | Service |
|---|---|
| 5173 | Frontend (Vite dev server; proxies `/api/*` → 8000) |
| 8000 | Backend (FastAPI) |
| 5432 (or `POSTGRES_PORT`) | PostgreSQL + pgvector (Docker) |
| 9099 | Firebase Auth emulator (Docker) |
| 8080 | Firestore emulator (Docker) |
| 4000 | Firebase emulator UI (Docker) |
| 11434 | Ollama |

---

## 10. Updating after `git pull`

New code can add Python packages or database migrations. After pulling, re-run the setup script (it skips what is already done), or manually:

```bash
cd backend
pip install -r requirements.txt -r requirements-dev.txt   # venv active
alembic upgrade head
cd ../frontend && npm install
```
Then restart the backend.

---

## 11. Stopping and resetting

```bash
docker compose down        # stop containers, keep data
docker compose down -v     # stop containers AND delete the database (re-run `alembic upgrade head` afterwards)
```
Stop the backend and frontend with `Ctrl+C` in their terminals.

---

## 12. Troubleshooting

### `Bind for 0.0.0.0:5432 failed: port is already allocated`
Something else (a local PostgreSQL, or another project's container — check with `docker ps`) owns port 5432. Re-run setup with a different port (section 3), or set `POSTGRES_PORT` and update `DATABASE_URL` in `backend/.env` to the same port.

### `pip install` fails building `asyncpg` or `pydantic-core`
You are on Python 3.13. Delete `backend/venv` and recreate it with Python 3.11 or 3.12. Without 3.11/3.12 installed you can use uv: `uv venv --python 3.11 venv`, then `uv pip install --python venv/... -r requirements.txt -r requirements-dev.txt` (the Windows setup script does this automatically).

### Backend fails with `ModuleNotFoundError: No module named 'rag'`
`backend/rag` and `backend/safeguarding` are git symlinks to the repo-root folders. On Windows, git checks them out as small text files unless Developer Mode and `core.symlinks=true` are enabled. The `rusty_repo_root.pth` file from setup (section 4.3) fixes the import; re-run that step if you recreated the venv.

### `Safeguarding phrases file not found`
Run the backend from inside `backend/`. The app looks for `./safeguarding/phrases_v1.json` and falls back to the repo-root `safeguarding/` folder. If you override `PHRASES_FILE_PATH`, it must be an environment variable (not just a `backend/.env` entry) pointing to an existing file.

### Study answers are empty or "not in your textbook"
No textbook is loaded for that class + subject, or ingestion has not finished. See section 7. Answers are restricted to the student's class and subject by design.

### Textbook stuck in `processing` (can't delete it)
If the backend was restarted or crashed mid-ingestion, the record can stay in `processing`, and the delete endpoint refuses to remove it. Check the backend terminal for the error, then mark it failed and delete it:
```bash
docker compose exec -T postgres psql -U rusty -d rusty_dev -c "UPDATE textbooks SET status='failed' WHERE status='processing'"
curl -X DELETE http://localhost:8000/admin/textbooks/<textbook_id> -H "Authorization: Bearer dev-admin-token"
```
Then upload it again.

### Backend can't reach Ollama / answers time out
Check `ollama list` shows both models and `curl http://localhost:11434/api/tags` responds. The first answer after startup is slow while the model loads into memory.

### Config change has no effect
Settings are cached; restart the backend after editing `backend/.env`. Environment variables in your shell override values in `.env`.

### `npm install` reports vulnerabilities
These are in dev dependencies and do not block local running. Don't run `npm audit fix --force` casually — it can upgrade to breaking major versions.
