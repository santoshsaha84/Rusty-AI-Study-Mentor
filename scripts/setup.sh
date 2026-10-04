#!/usr/bin/env bash
# One-time local setup for Rusty on macOS / Linux.
# Usage (from repo root):  POSTGRES_PORT=5432 ./scripts/setup.sh
# Safe to re-run: every step skips work that is already done.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$REPO_ROOT/backend"
FRONTEND="$REPO_ROOT/frontend"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
export POSTGRES_PORT

step() { printf '\n==> %s\n' "$1"; }
fail() { printf 'ERROR: %s\n' "$1" >&2; exit 1; }

# -- 1. Prerequisites ---------------------------------------------------------
step "Checking prerequisites"
for cmd in git docker node npm ollama; do
  command -v "$cmd" >/dev/null || fail "'$cmd' not found on PATH. See doc/LOCAL_LAUNCH.md."
done
docker info >/dev/null 2>&1 || fail "Docker is installed but not running. Start it and re-run."
node_major="$(node --version | sed 's/^v//; s/\..*//')"
[ "$node_major" -ge 20 ] || fail "Node 20+ required (found $(node --version))."

# Pick a Python 3.11/3.12 interpreter (pinned deps have no 3.13 wheels).
PYTHON=""
for candidate in python3.11 python3.12; do
  if command -v "$candidate" >/dev/null; then PYTHON="$candidate"; break; fi
done
USE_UV=0
if [ -z "$PYTHON" ]; then
  echo "Python 3.11/3.12 not found - using uv to provision Python 3.11 into the venv."
  command -v uv >/dev/null || fail "Install Python 3.11 or uv (https://docs.astral.sh/uv/) and re-run."
  USE_UV=1
fi
echo "Prerequisites OK"

# -- 2. Ollama models ---------------------------------------------------------
step "Pulling Ollama models (skipped if already present)"
for model in qwen2.5:3b snowflake-arctic-embed2; do
  if ollama list | grep -q "^$model"; then echo "$model already present"; else ollama pull "$model"; fi
done

# -- 3. Docker services -------------------------------------------------------
step "Starting PostgreSQL + Firebase emulators (Postgres on port $POSTGRES_PORT)"
(cd "$REPO_ROOT" && docker compose up -d) \
  || fail "docker compose failed. If port $POSTGRES_PORT is in use, re-run with POSTGRES_PORT=5433."

# -- 4. Backend venv + dependencies -------------------------------------------
step "Creating backend virtualenv and installing dependencies"
VENV_PY="$BACKEND/venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  if [ "$USE_UV" = 1 ]; then uv venv --python 3.11 "$BACKEND/venv"; else "$PYTHON" -m venv "$BACKEND/venv"; fi
fi
if [ "$USE_UV" = 1 ]; then
  uv pip install --python "$VENV_PY" -r "$BACKEND/requirements.txt" -r "$BACKEND/requirements-dev.txt"
else
  "$VENV_PY" -m pip install -q --upgrade pip
  "$VENV_PY" -m pip install -r "$BACKEND/requirements.txt" -r "$BACKEND/requirements-dev.txt"
fi

# Put the repo root on sys.path so `import rag` works even if backend/rag
# (a git symlink) was not checked out as a symlink.
SITE_PACKAGES="$("$VENV_PY" -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")"
echo "$REPO_ROOT" > "$SITE_PACKAGES/rusty_repo_root.pth"

# -- 5. backend/.env ----------------------------------------------------------
step "Configuring backend/.env"
if [ ! -f "$BACKEND/.env" ]; then
  secret="$("$VENV_PY" -c "import secrets; print(secrets.token_hex(32))")"
  sed -e "s#^SECRET_KEY=.*#SECRET_KEY=$secret#" \
      -e "s#localhost:5432/#localhost:$POSTGRES_PORT/#" \
      "$BACKEND/.env.template" > "$BACKEND/.env"
  echo "Created backend/.env"
else
  echo "backend/.env already exists - left unchanged (check DATABASE_URL port is $POSTGRES_PORT)"
fi

# -- 6. Database migrations ---------------------------------------------------
step "Waiting for Postgres and running migrations"
for _ in $(seq 1 30); do
  (cd "$REPO_ROOT" && docker compose exec -T postgres pg_isready -U rusty -d rusty_dev >/dev/null 2>&1) && break
  sleep 2
done
(cd "$BACKEND" && "$VENV_PY" -m alembic upgrade head) || fail "alembic upgrade failed."

# -- 7. Frontend dependencies -------------------------------------------------
step "Installing frontend dependencies"
(cd "$FRONTEND" && npm install) || fail "npm install failed."

printf '\nSetup complete. Start the app as described in doc/LOCAL_LAUNCH.md (section "Start the app").\n'
