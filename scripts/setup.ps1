# One-time local setup for Rusty on Windows (PowerShell).
# Usage (from repo root):  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 [-PostgresPort 5433]
# Safe to re-run: every step skips work that is already done.
param(
    [int]$PostgresPort = 5432
)

# Native tools report failure via $LASTEXITCODE, which each step checks explicitly.
$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $RepoRoot "backend"
$Frontend = Join-Path $RepoRoot "frontend"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

# -- 1. Prerequisites ---------------------------------------------------------
Step "Checking prerequisites"
foreach ($cmd in "git", "docker", "node", "npm", "ollama") {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { Fail "'$cmd' not found on PATH. See doc/LOCAL_LAUNCH.md." }
}
docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail "Docker is installed but not running. Start Docker Desktop and re-run." }
$nodeMajor = [int]((node --version).TrimStart("v").Split(".")[0])
if ($nodeMajor -lt 20) { Fail "Node 20+ required (found $(node --version))." }

# Pick a Python 3.11/3.12 interpreter (pinned deps have no 3.13 wheels).
$PythonCmd = $null
foreach ($v in "3.11", "3.12") {
    py "-$v" --version *> $null
    if ($LASTEXITCODE -eq 0) { $PythonCmd = @("py", "-$v"); break }
}
$UseUv = $false
if (-not $PythonCmd) {
    Write-Host "Python 3.11/3.12 not found - using uv to provision Python 3.11 into the venv."
    py -m uv --version *> $null
    if ($LASTEXITCODE -ne 0) {
        py -m pip install --user -q uv
        if ($LASTEXITCODE -ne 0) { Fail "Could not install uv. Install Python 3.11 from python.org and re-run." }
    }
    $UseUv = $true
}
Write-Host "Prerequisites OK"

# -- 2. Ollama models ---------------------------------------------------------
Step "Pulling Ollama models (skipped if already present)"
$models = ollama list | Out-String
foreach ($m in "qwen2.5:3b", "snowflake-arctic-embed2") {
    if ($models -notmatch [regex]::Escape($m)) { ollama pull $m } else { Write-Host "$m already present" }
}

# -- 3. Docker services -------------------------------------------------------
Step "Starting PostgreSQL + Firebase emulators (Postgres on port $PostgresPort)"
$env:POSTGRES_PORT = "$PostgresPort"
Push-Location $RepoRoot
docker compose up -d
if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "docker compose failed. If port $PostgresPort is in use, re-run with -PostgresPort 5433." }
Pop-Location

# -- 4. Backend venv + dependencies -------------------------------------------
Step "Creating backend virtualenv and installing dependencies"
$VenvPython = Join-Path $Backend "venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    if ($UseUv) { py -m uv venv --python 3.11 (Join-Path $Backend "venv") }
    else { & $PythonCmd[0] $PythonCmd[1] -m venv (Join-Path $Backend "venv") }
}
if ($UseUv) {
    py -m uv pip install --python $VenvPython -r (Join-Path $Backend "requirements.txt") -r (Join-Path $Backend "requirements-dev.txt")
} else {
    & $VenvPython -m pip install -q --upgrade pip
    & $VenvPython -m pip install -r (Join-Path $Backend "requirements.txt") -r (Join-Path $Backend "requirements-dev.txt")
}
if ($LASTEXITCODE -ne 0) { Fail "pip install failed." }

# backend/rag is a git symlink, which Windows checks out as a plain file.
# A .pth file puts the repo root on sys.path so `import rag` resolves everywhere
# (uvicorn, alembic, pytest) without needing PYTHONPATH.
$SitePackages = & $VenvPython -c "import sysconfig; print(sysconfig.get_paths()['purelib'])"
Set-Content -Path (Join-Path $SitePackages "rusty_repo_root.pth") -Value $RepoRoot -Encoding ascii

# -- 5. backend/.env ----------------------------------------------------------
Step "Configuring backend/.env"
$EnvFile = Join-Path $Backend ".env"
if (-not (Test-Path $EnvFile)) {
    $secret = & $VenvPython -c "import secrets; print(secrets.token_hex(32))"
    $lines = (Get-Content (Join-Path $Backend ".env.template")) `
        -replace "^SECRET_KEY=.*", "SECRET_KEY=$secret" `
        -replace "localhost:5432/", "localhost:$PostgresPort/"
    # Write without a BOM so pydantic-settings reads the first key correctly
    [System.IO.File]::WriteAllLines($EnvFile, $lines, (New-Object System.Text.UTF8Encoding $false))
    Write-Host "Created backend/.env"
} else {
    Write-Host "backend/.env already exists - left unchanged (check DATABASE_URL port is $PostgresPort)"
}

# -- 6. Database migrations ---------------------------------------------------
Step "Waiting for Postgres and running migrations"
for ($i = 0; $i -lt 30; $i++) {
    docker compose -f (Join-Path $RepoRoot "docker-compose.yml") exec -T postgres pg_isready -U rusty -d rusty_dev *> $null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 2
}
Push-Location $Backend
& $VenvPython -m alembic upgrade head
$migrateExit = $LASTEXITCODE
Pop-Location
if ($migrateExit -ne 0) { Fail "alembic upgrade failed." }

# -- 7. Frontend dependencies -------------------------------------------------
Step "Installing frontend dependencies"
Push-Location $Frontend
npm install
$npmExit = $LASTEXITCODE
Pop-Location
if ($npmExit -ne 0) { Fail "npm install failed." }

Write-Host "`nSetup complete. Start the app as described in doc/LOCAL_LAUNCH.md (section 'Start the app')." -ForegroundColor Green
