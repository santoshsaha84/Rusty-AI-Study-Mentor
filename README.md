# Rusty — AI Study Mentor

An AI-powered study mentor built for [KHEL Foundation](https://diksha.foundation) students studying the Bihar Board curriculum. Rusty uses a RAG (Retrieval-Augmented Generation) pipeline to answer questions strictly from uploaded textbook PDFs, with built-in child safeguarding at every layer.

## Architecture

```
┌──────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   React +    │────▶│  FastAPI Backend  │────▶│  PostgreSQL +   │
│  TypeScript  │ /api│  (uvicorn)       │     │  pgvector       │
│  Vite + PWA  │◀────│  Port 8000       │     │  Port 5432      │
│  Port 5173   │ SSE │                  │     └─────────────────┘
└──────────────┘     │  Middleware:      │     ┌─────────────────┐
                     │  • Safeguarding   │────▶│  Ollama (dev)   │
                     │  • Security hdrs  │     │  Port 11434     │
                     │  • Rate limiting  │     │  or Vertex AI   │
                     │  • Logging        │     │  (production)   │
                     └──────────────────┘     └─────────────────┘
                            │
                     ┌──────┴───────┐
                     │  Firebase    │
                     │  Auth        │
                     │  Port 9099   │
                     │  (emulator)  │
                     └──────────────┘
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 18, TypeScript, Tailwind CSS, Vite, PWA |
| Backend | FastAPI, SQLAlchemy 2.0 (async), Pydantic v2 |
| Database | PostgreSQL 16 + pgvector (vector similarity search) |
| LLM (dev) | Ollama — qwen2.5:3b (chat) + snowflake-arctic-embed2 (embeddings) |
| LLM (prod) | Google Vertex AI — Gemini 2.5 Flash Lite |
| Auth | Firebase Authentication (emulator for dev) |
| Containerization | Docker Compose |

## Prerequisites

- **Python 3.11+** (tested on 3.13)
- **Node.js 18+** (tested on 24.x)
- **Docker Desktop** (for PostgreSQL + Firebase emulators)
- **Ollama** (for local LLM inference) — [Install Ollama](https://ollama.ai)

## Setup — Step by Step

> **Quick start:** `scripts/setup.ps1` (Windows) or `scripts/setup.sh` (macOS/Linux) automates the steps below. See [doc/LOCAL_LAUNCH.md](doc/LOCAL_LAUNCH.md) for the full launch guide, login credentials and troubleshooting.

### 1. Clone the repo

```bash
git clone git@github.com:JampaniTejasai/Rusty-AI-Study-Mentor.git
cd Rusty-AI-Study-Mentor
```

### 2. Install Ollama models

```bash
# Chat model (~2GB)
ollama pull qwen2.5:3b

# Embedding model (~1.1GB)
ollama pull snowflake-arctic-embed2
```

Verify Ollama is running:
```bash
curl http://localhost:11434/api/tags
```

### 3. Start Docker services (PostgreSQL + Firebase)

```bash
docker compose up -d
```

This starts:
- **PostgreSQL 16** with pgvector on port 5432
- **Firebase Auth emulator** on port 9099
- **Firebase Emulator UI** on port 4000

Wait for PostgreSQL to be healthy:
```bash
docker exec ffg-postgres-1 pg_isready -U rusty -d rusty_dev
```

### 4. Set up the backend

```bash
cd backend

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.template .env
# Edit .env if needed — defaults work for local dev

# Run database migrations
alembic upgrade head

# Start the backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-server-header --reload
```

Verify: `curl http://localhost:8000/health` should return `{"status":"ok"}`

### 5. Set up the frontend

```bash
cd frontend

# Install dependencies
npm install

# Start dev server
npm run dev
```

The app is now running at **http://localhost:5173**

### 6. Upload a textbook (required for RAG to work)

The study mode needs textbook content to answer from. Upload a Bihar Board PDF via the admin API:

```bash
# Login as admin
curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"student_id":"KHEL-2026-ADM1","pin":"0000"}'
# Returns: {"firebase_token":"dev-admin-token", ...}

# Upload a PDF
curl -X POST http://localhost:8000/admin/upload-pdf \
  -H "Authorization: Bearer dev-admin-token" \
  -F "file=@/path/to/textbook.pdf" \
  -F "class_num=8" \
  -F "subject=mathematics"
```

The ingestion pipeline runs in the background: PDF → text extraction → chunking → embedding → pgvector storage. Check status:
```bash
curl -s http://localhost:8000/admin/textbooks \
  -H "Authorization: Bearer dev-admin-token"
```

Wait until `status` is `"ready"` before testing study queries.

Valid subjects: `mathematics`, `science`, `hindi`, `social_science`, `english`
Valid classes: `5` through `10`

## Dev Login Credentials

These only work in development mode (`APP_ENV=development`):

| Role | Student ID | PIN | Notes |
|------|-----------|-----|-------|
| Student | KHEL-2026-001 | 1234 | Class 8 |
| Teacher | KHEL-2026-T01 | 9999 | Centre KHEL-PATNA-01 |
| Admin | KHEL-2026-ADM1 | 0000 | Full access |

## Project Structure

```
├── backend/
│   ├── app/
│   │   ├── api/              # Route handlers
│   │   │   ├── admin.py      # PDF upload, textbook mgmt, RAG observatory
│   │   │   ├── auth.py       # Student ID + PIN login
│   │   │   ├── quiz.py       # Teacher quiz generation + publishing
│   │   │   ├── study.py      # RAG study queries (main feature)
│   │   │   ├── teacher.py    # Scores dashboard, weak topics
│   │   │   └── test.py       # AI test generation, answers, results
│   │   ├── core/             # Config, database, auth deps, rate limiter
│   │   ├── middleware/       # Safeguarding, security headers, logging
│   │   ├── models/           # SQLAlchemy models
│   │   ├── repositories/     # Data access layer
│   │   ├── schemas/          # Pydantic request/response schemas
│   │   └── services/         # Business logic (retrieval, circuit breaker)
│   ├── migrations/           # Alembic database migrations
│   ├── tests/                # pytest test suite
│   └── eval/                 # RAG evaluation framework
├── rag/
│   ├── ingestion/            # PDF → chunks → embeddings pipeline
│   │   ├── pdf_extractor.py  # PDF text extraction
│   │   ├── chunker.py        # Text chunking with overlap
│   │   ├── embedder.py       # Ollama/Vertex embedding
│   │   ├── metadata_tagger.py # Math type detection
│   │   └── summary_generator.py # Chapter summary generation
│   └── retrieval/            # Query → answer pipeline
│       ├── llm_client.py     # Ollama/Vertex AI abstraction
│       ├── pre_filter.py     # Metadata-based chunk filtering
│       ├── rrf.py            # Reciprocal Rank Fusion (vector + BM25)
│       └── prompt_builder.py # System prompts for study/test/quiz modes
├── safeguarding/
│   └── phrases_v1.json       # Blocklist for child safety scanning
├── frontend/
│   └── src/
│       ├── components/       # React components by role
│       │   ├── admin/        # Admin panel, PDF upload, RAG dashboard
│       │   ├── auth/         # Login screen
│       │   ├── student/      # Progress, tests, recent activity
│       │   ├── study/        # Study mode (main RAG chat interface)
│       │   ├── teacher/      # Create test, quiz pack, student dashboard
│       │   └── shared/       # MathRenderer, loaders, offline banner
│       ├── api/              # API client + SSE streaming
│       └── stores/           # Auth context, chapter state
├── docker-compose.yml        # PostgreSQL + Firebase emulators
└── .env.template             # Environment variable template
```

## RAG Pipeline

The retrieval pipeline works as follows:

1. **Input scan** — Every student query is scanned against the safeguarding blocklist before processing
2. **Embedding** — Query is embedded using snowflake-arctic-embed2 (dev) or Vertex text-embedding (prod)
3. **Vector search** — Cosine similarity search in pgvector, pre-filtered by class + subject + language
4. **BM25 search** — Full-text PostgreSQL search on the same chunk set
5. **RRF fusion** — Reciprocal Rank Fusion merges vector and BM25 results
6. **LLM generation** — Top chunks + query sent to qwen2.5:3b (dev) or Gemini (prod) with a structured prompt
7. **Output scan** — AI response is scanned against safeguarding blocklist before returning to student
8. **Response** — Structured JSON with key_points, notes, misconceptions, source references

Chunks are strictly filtered by `class_num` and `subject` — the LLM never sees content from other classes or subjects.

## API Endpoints

### Auth
- `POST /auth/login` — Student ID + PIN authentication
- `POST /auth/reset-pin` — Reset student PIN (teacher/admin)
- `GET /auth/ping` — Token validation

### Study (Student)
- `POST /study/query` — Ask a question (returns structured answer)
- `POST /study/query/stream` — Same, but SSE streaming
- `GET /study/chapters` — List available subjects/chapters
- `GET /study/summary` — Pre-computed chapter summary

### Test (Student)
- `POST /test/generate` — Generate AI practice test
- `POST /test/answer` — Submit an answer
- `GET /test/result/{test_id}` — Get test results
- `GET /test/my-tests` — List student's tests
- `GET /test/resume/{test_id}` — Resume in-progress test

### Quiz (Teacher)
- `POST /quiz/generate` — Generate quiz pack from textbook
- `POST /quiz/publish` — Publish quiz to students
- `GET /quiz/assigned` — Get assigned quizzes for a class

### Teacher
- `GET /teacher/scores` — Student scores dashboard
- `GET /teacher/weak-topics` — Weak topic analysis

### Admin
- `POST /admin/upload-pdf` — Upload textbook PDF for ingestion
- `GET /admin/textbooks` — List uploaded textbooks
- `DELETE /admin/textbooks/{id}` — Delete textbook + chunks
- `GET /admin/rag/summary` — RAG pipeline metrics
- `GET /admin/rag/recent` — Recent RAG traces
- `GET /admin/rag/timeline` — Performance timeline

### System
- `GET /health` — Health check
- `GET /ready` — Readiness check (DB + Ollama)

## Running Tests

```bash
cd backend
source venv/bin/activate

# Run all tests
PYTHONPATH=. python -m pytest tests/ -v

# Run specific test
PYTHONPATH=. python -m pytest tests/test_safeguarding.py -v
```

Frontend tests:
```bash
cd frontend
npm test
```

## Security

This app is designed for child users (Bihar Board students). Security measures include:

- **Closed access** — Only registered students with Student ID + PIN can log in. No email, phone, or self-registration.
- **Safeguarding** — Every student input AND every AI response is scanned against a blocklist before reaching the student. Cannot be bypassed.
- **Textbook-only answers** — RAG pre-filters chunks by class/subject via SQL WHERE clause. The LLM only sees relevant textbook content.
- **No conversation history stored** — Chat messages are never persisted server-side (child privacy).
- **PII protection** — Student IDs are SHA-256 hashed in logs. No names, ages, or contact details stored.
- **Security headers** — CSP, HSTS, X-Frame-Options, X-Content-Type-Options on all responses.
- **Rate limiting** — Per-endpoint rate limits (login: 5/min, study: 30/min).
- **Input validation** — Pydantic max_length on all fields. Parameterized SQL queries only.
- **CORS** — Only named allowed origins, no wildcard in production.

## Environment Variables

See `backend/.env.template` for all variables. Key ones:

| Variable | Description | Default |
|----------|------------|---------|
| `LLM_PROVIDER` | `ollama` (dev) or `vertex` (prod) | `ollama` |
| `OLLAMA_MODEL` | Ollama chat model | `qwen2.5:3b` |
| `OLLAMA_EMBED_MODEL` | Ollama embedding model | `snowflake-arctic-embed2` |
| `DATABASE_URL` | PostgreSQL connection string | `postgresql+asyncpg://rusty:localdev@localhost:5432/rusty_dev` |
| `FIREBASE_AUTH_EMULATOR_HOST` | Firebase emulator (empty for prod) | `localhost:9099` |
| `APP_ENV` | `development`, `staging`, or `production` | `development` |

## Production Deployment

For production on Google Cloud Run:

1. Set `LLM_PROVIDER=vertex` and configure Vertex AI project
2. Remove `FIREBASE_AUTH_EMULATOR_HOST` (use real Firebase)
3. Use Cloud SQL for PostgreSQL with pgvector
4. Store secrets in GCP Secret Manager
5. Build with `docker build -f backend/Dockerfile -t rusty-backend .`
6. Container runs as non-root user

## License

Private — KHEL Foundation / Diksha Foundation
