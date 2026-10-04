# Local Launch Guide — Rusty AI Study Mentor

How to get Rusty running on a fresh computer (Windows, macOS or Linux) for local development and testing. Everything runs locally: no GCP account, no real Firebase project, no API keys.

---

## 1. Prerequisites

Install these before you start:

| Tool | Version | Notes |
|---|---|---|
| Git | any recent | |
| Docker Desktop | any recent | Must be **running** (it hosts PostgreSQL + Firebase emulators) |
| Python | **3.11 or 3.12** | **3.13 does not work** — pinned `asyncpg`/`pydantic` have no 3.13 builds. Production and CI use 3.11. On Windows, install from python.org with the **py launcher** ticked — `setup.ps1` needs `py`. If you only have 3.13, the setup script can fetch 3.11 via [uv](https://docs.astral.sh/uv/) (still through `py`) |
| Node.js | **20+** | Comes with npm |
| Ollama | any recent | [ollama.com](https://ollama.com). Runs the local LLM + embedding models |

Hardware: about **7–10 GB free disk** in total (Ollama itself ~1–2 GB, models ~3.1 GB, Docker images ~2 GB, Python venv + `node_modules` ~1.5 GB) and **8 GB+ RAM** recommended. **No GPU is needed** — see [Running without a GPU](#13-running-without-a-gpu).

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

**Windows, one command (recommended):**
```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-local.ps1
```
It starts Docker Desktop if needed, brings up PostgreSQL (on the port in `backend/.env`'s `DATABASE_URL`), and starts Ollama in the background if it isn't running, logging to `%LOCALAPPDATA%\Ollama\rusty-ollama-serve.log`. It then opens two windows: **Rusty backend** (port 8000) and **Rusty frontend** (port 5173). It also sets `DATABASE_URL` and `PYTHONUTF8=1` for the backend, which avoids two common Windows failures (see [Troubleshooting](#12-troubleshooting)). The Firebase emulators are skipped because the dev logins don't use them; add `-WithFirebase` to start them too. To stop, close the two windows, then run `docker compose stop`.

**Manually (any OS):** Docker services and Ollama must be running (Ollama usually starts automatically; if not, run `ollama serve`). Then open **two terminals**:

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

Open **http://localhost:5173**. If another app already uses 5173, Vite picks the next free port (5174, …) and prints it. The `/api` proxy still works, so use whatever URL it shows.

After a reboot, Windows users only need `scripts\start-local.ps1`. Otherwise: start Docker Desktop, run `docker compose up -d` (with `POSTGRES_PORT` set if you use a non-default port), then open the two terminals above.

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
| 5173 | Frontend (Vite dev server; proxies `/api/*` → 8000). Moves to 5174+ if taken |
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

If even `ollama ps` hangs, the server is frozen. On Windows this happens when `ollama serve` runs in a console window and someone clicks or selects text in it (QuickEdit mode pauses the program on its next log line). Kill it with `Get-Process ollama | Stop-Process`, then start it again without a console: either `scripts\start-local.ps1` or the Ollama Start-menu app. The same pause applies to the backend and frontend windows: if one seems stuck, click it and press **Esc**. Server logs: `%LOCALAPPDATA%\Ollama\rusty-ollama-serve.log` when started by the script, or `server.log` in the same folder when started by the Ollama app.

While Ollama is generating chapter summaries after an upload, new questions queue behind it, because it handles one request at a time.

### Config change has no effect
Settings are cached; restart the backend after editing `backend/.env`. Environment variables in your shell override values in `.env`.

### `ModuleNotFoundError: No module named 'psycopg2'` (alembic) or the backend connects to the wrong database
A `DATABASE_URL` environment variable is set on your machine, often by another project (e.g. a Prisma-style `postgresql://…`). Environment variables take priority over `backend/.env`, so Rusty uses that URL instead of its own. Check with `echo $env:DATABASE_URL` (PowerShell). Don't delete it if another project needs it. Override it just for the Rusty terminal instead:
```powershell
$env:DATABASE_URL = "postgresql+asyncpg://rusty:localdev@localhost:5432/rusty_dev"
```
`scripts\start-local.ps1` does this automatically, using the value from `backend/.env`.

### Backend crashes at startup with `UnicodeDecodeError: 'charmap' codec can't decode byte …`
Windows only. `slowapi` reads `backend/.env` with the Windows default code page (cp1252), and the file contains UTF-8 characters (the `—` in comments). Start Python in UTF-8 mode: `$env:PYTHONUTF8 = "1"` in the backend terminal, or set it permanently with `setx PYTHONUTF8 1`. `scripts\start-local.ps1` sets it for you.

### `docker compose up` fails: `ports are not available: exposing port TCP 0.0.0.0:8080`
Another program is using port 8080 (or 9099/4000), which the Firebase emulators need. Find it with `Get-NetTCPConnection -LocalPort 8080 -State Listen` (PowerShell). The emulators are optional locally, since the dev logins bypass Firebase. Start only PostgreSQL with `docker compose up -d postgres`; `scripts\start-local.ps1` does this by default. Because `setup.ps1` stops at this error, run its remaining steps by hand from [section 4.3](#43-backend) onwards.

### Ingestion fails with `500 Internal Server Error … /api/embeddings` and the Ollama log shows `device lost`
Ollama tried to use an old or unsupported GPU through Vulkan, and the driver crashed (seen with a GeForce GT 740). Force CPU-only mode by hiding the Vulkan GPUs, then restart Ollama (quit it from the system tray and start it again):
```powershell
setx GGML_VK_VISIBLE_DEVICES -1
```
`ollama ps` should then show `100% CPU`. Delete the failed textbook and upload it again.

### `npm install` reports vulnerabilities
These are in dev dependencies and do not block local running. Don't run `npm audit fix --force` casually — it can upgrade to breaking major versions.

---

## 13. Running without a GPU

No code or config changes are needed. Ollama runs the models on the CPU automatically when it finds no usable GPU. If it finds an old GPU that crashes, see the `device lost` entry in [Troubleshooting](#12-troubleshooting). Check what Ollama is using with `ollama ps`; the `PROCESSOR` column should read `100% CPU`.

**What to expect.** These figures were measured on an Intel i5-11400 (6 cores) with 32 GB RAM:

| Task | Time |
|---|---|
| Embedding one question | under a second |
| Ingesting `rag/data/maths-08.pdf` (20 pages, 30 chunks) | ~40 s |
| Study answer (`qwen2.5:3b`) | ~60–90 s. The screen stays on "generating" because answers arrive in one piece, not word by word |
| Chapter summaries after an upload | runs in the background for a long time on a full book; questions work as soon as the textbook is `ready` |

The models use about 3.5 GB of RAM while loaded.

**Making it faster:**
- `setx OLLAMA_KEEP_ALIVE 30m`, then restart Ollama. This keeps models in memory between questions instead of reloading them after 5 minutes.
- For quicker but weaker answers (noticeably weaker in Hindi), set `OLLAMA_MODEL=qwen2.5:1.5b` in `backend/.env`, run `ollama pull qwen2.5:1.5b`, and restart the backend.
- **Do not change `OLLAMA_EMBED_MODEL`.** The database column is fixed at 1024 dimensions, and every textbook would need re-ingesting.

**Installing Ollama and its models on another drive (Windows).** By default Ollama installs to `%LOCALAPPDATA%\Programs\Ollama` and stores models in `%USERPROFILE%\.ollama\models`, both on C:. To use D: instead:
```powershell
setx OLLAMA_MODELS "D:\Ollama\models"     # before pulling any model
OllamaSetup.exe /DIR="D:\Ollama"           # installer from ollama.com
```
Quit and restart Ollama after `setx` so it sees the new variable. A shell that was already open does not see it either, so open a new one. Python can go on D: too: in the python.org installer, choose *Customize installation*, set the install location, and keep the *py launcher* ticked. The backend `venv` and `node_modules` live inside the repo folder, so they sit on whichever drive you cloned to.

---

## 14. Inspecting the database (PostgreSQL + pgvector)

Textbook chunks, their embeddings, test data, the response cache and RAG traces all live in the local PostgreSQL container. pgvector is the `vector` extension inside it, not a separate service.

### 14.1 Connect

**psql inside the container** (no install needed; run from the repo root):
```bash
docker compose exec postgres psql -U rusty -d rusty_dev
```
Useful psql commands: `\dt` lists tables, `\d chunks` shows columns and indexes, `\x` toggles vertical output for wide rows, `\q` quits. For a one-off query, add `-c "SELECT …"`.

**GUI client** (DBeaver, pgAdmin, TablePlus, or the VS Code *PostgreSQL* extension):

| Setting | Value |
|---|---|
| Host | `localhost` |
| Port | `5432` (or your `POSTGRES_PORT`) |
| Database | `rusty_dev` |
| User / password | `rusty` / `localdev` (local dev only) |
| JDBC / plain URL | `postgresql://rusty:localdev@localhost:5432/rusty_dev`. Note: no `+asyncpg`, which is only for SQLAlchemy |

Avoid `SELECT *` on `chunks` or `response_cache`. Each `embedding` is 1024 numbers and floods the output, so select the columns you need.

### 14.2 Tables

| Table | Holds |
|---|---|
| `textbooks` | One row per uploaded PDF: `status` (`processing` / `ready` / `failed`), `chunk_count`, `error_message` |
| `chunks` | Textbook passages: `text_content`, `embedding vector(1024)`, `class_num`, `subject`, `chapter`, `page_num`, `language` |
| `chapter_summaries` | Pre-generated chapter summaries (EN/HI), filled in after ingestion |
| `response_cache` | Cached study answers, keyed by question embedding (cosine distance < 0.05) |
| `rag_traces` | One row per study/test/quiz request: timings, hit counts, errors, **query text** (admin-only data, auto-purged) |
| `tests`, `questions`, `test_attempts`, `attempt_answers`, `quiz_scores` | Practice tests, published quizzes and scores |

### 14.3 Handy queries

```sql
-- pgvector version
SELECT extversion FROM pg_extension WHERE extname = 'vector';

-- Upload status (why is my textbook stuck / failed?)
SELECT source_pdf, class_num, subject, status, chunk_count, language, error_message FROM textbooks;

-- Chunks per class/subject/chapter, and whether every chunk has a 1024-dim embedding
SELECT class_num, subject, language, coalesce(chapter, '(none)') AS chapter,
       count(*) AS chunks, count(embedding) AS embedded, min(vector_dims(embedding)) AS dims
FROM chunks GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4;

-- Peek at chunk text
SELECT page_num, section_type, left(text_content, 120) AS text
FROM chunks WHERE source_pdf = 'class8_mathematics_maths-08.pdf' ORDER BY page_num LIMIT 10;

-- Nearest neighbours of an existing chunk (<=> is cosine distance: 0 = identical, lower = more similar)
WITH ref AS (SELECT chunk_id, embedding FROM chunks WHERE page_num = 2 LIMIT 1)
SELECT c.page_num, round((c.embedding <=> ref.embedding)::numeric, 4) AS distance, left(c.text_content, 80) AS text
FROM chunks c, ref
WHERE c.class_num = 8 AND c.subject = 'mathematics' AND c.chunk_id <> ref.chunk_id
ORDER BY c.embedding <=> ref.embedding LIMIT 5;

-- What happened on recent requests (latency, retrieval hits, errors)
SELECT created_at, mode, cache_hit, total_ms::int, llm_ms::int,
       vector_hits, bm25_hits, rrf_chunks, empty_response, error
FROM rag_traces ORDER BY created_at DESC LIMIT 10;

-- Cached answers
SELECT class_num, subject, medium, left(query_text, 50) AS query, hit_count, prompt_version
FROM response_cache ORDER BY created_at DESC LIMIT 10;

-- Generated chapter summaries
SELECT class_num, subject, chapter, language, chunk_count FROM chapter_summaries;
```

**Reset helpers** (local only):
```sql
TRUNCATE response_cache;                                          -- stop serving cached answers after a prompt or data change
UPDATE textbooks SET status = 'failed' WHERE status = 'processing'; -- unstick a textbook so it can be deleted
```

### 14.4 Search the vectors with your own question

SQL can't turn text into an embedding, so this script asks Ollama for the question's embedding and then runs the same cosine search the app uses. It shows which pages a question would retrieve. Run it from `backend/` (PowerShell):
```powershell
@'
import asyncio, sys
import asyncpg, httpx

question, class_num, subject = sys.argv[1], int(sys.argv[2]), sys.argv[3]
emb = httpx.post("http://localhost:11434/api/embeddings",
                 json={"model": "snowflake-arctic-embed2", "prompt": question}, timeout=120).json()["embedding"]

async def main():
    conn = await asyncpg.connect("postgresql://rusty:localdev@localhost:5432/rusty_dev")
    rows = await conn.fetch(
        """SELECT page_num, round((embedding <=> $1::text::vector)::numeric, 4) AS distance,
                  left(replace(text_content, E'\n', ' '), 80) AS text
           FROM chunks WHERE class_num = $2 AND subject = $3
           ORDER BY embedding <=> $1::text::vector LIMIT 5""",
        str(emb), class_num, subject)
    for r in rows:
        print(f"p{r['page_num']}  {r['distance']}  {r['text']}")
    await conn.close()

asyncio.run(main())
'@ | .\venv\Scripts\python.exe - "What is a perfect square?" 8 mathematics
```
If Ollama is busy (for example generating chapter summaries after an upload), this waits until it is free. Ollama handles one request at a time by default.

### 14.5 Reading the results

- **`dims` is not 1024, or `embedded` < `chunks`.** The embedding model changed or ingestion broke partway. Delete the textbook and upload it again.
- **`chapter` is `(none)` for everything.** The PDF's chapter headings weren't detected. Questions still work, but chapter filters and chapter summaries won't. You can pass `chapter` when uploading to label all of the book's chunks.
- **`bm25_hits` is almost always 0.** This is a known flaw, not your setup. The keyword half of the hybrid search uses `plainto_tsquery('simple', …)`, which keeps filler words and requires *every* word in one chunk (`'what' & 'is' & 'a' & 'perfect' & 'square' …`). Natural questions rarely match, so retrieval is effectively vector-only. Compare:
  ```sql
  SELECT plainto_tsquery('simple',  'What is a perfect square?');   -- every word required
  SELECT plainto_tsquery('english', 'What is a perfect square?');   -- stop-words dropped, words stemmed
  ```
  The GIN index `ix_chunks_fts` is also built with `'english'`, so the `'simple'` query can't use it.
- **Few or no `vector_hits` once many books are loaded.** `ix_chunks_embedding` is an IVFFlat index with `lists = 100`, built on an empty table. When the planner uses it, the default `ivfflat.probes = 1` searches only 1 of 100 lists and can miss rows. With little data, Postgres ignores the index and searches exactly, so check the plan with `EXPLAIN` first. If the plan shows `ix_chunks_embedding`, try `SET ivfflat.probes = 10;` in the same session, or rebuild the index after loading data: `REINDEX INDEX ix_chunks_embedding;`.
- **No `rag_traces` row for a question asked in the web app.** Answers in the app are streamed (`/study/query/stream`), and that path's trace and cache writes appear never to be committed. Only the non-streaming `/study/query` reliably logs, so use that (e.g. via `curl`) when you need a trace.
