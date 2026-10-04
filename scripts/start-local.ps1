# Daily start for Rusty on Windows (PowerShell), after scripts\setup.ps1 has run once.
# Usage (from repo root):  powershell -ExecutionPolicy Bypass -File scripts\start-local.ps1 [-WithFirebase]
# Starts Postgres (Docker), Ollama, then the backend and frontend in their own windows.
param(
    [switch]$WithFirebase   # also start the Firebase emulators (not needed: dev logins bypass Firebase)
)

$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $RepoRoot "backend"
$Frontend = Join-Path $RepoRoot "frontend"
$VenvPython = Join-Path $Backend "venv\Scripts\python.exe"
$EnvFile = Join-Path $Backend ".env"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

# curl.exe (bundled with Windows 10+) rather than Invoke-WebRequest, which can stall in PowerShell 5.1.
function Test-Url($url) {
    curl.exe -s -o NUL -m 3 $url
    return ($LASTEXITCODE -eq 0)
}

if (-not (Test-Path $VenvPython)) { Fail "backend\venv not found. Run scripts\setup.ps1 first." }
if (-not (Test-Path $EnvFile)) { Fail "backend\.env not found. Run scripts\setup.ps1 first." }

# -- 1. Docker + Postgres -----------------------------------------------------
Step "Starting Docker services"
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    $dockerDesktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path $dockerDesktop)) { Fail "Docker is not running. Start Docker Desktop and re-run." }
    Write-Host "Docker is not running - starting Docker Desktop..."
    Start-Process $dockerDesktop
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 3
        docker info *> $null
        if ($LASTEXITCODE -eq 0) { break }
    }
    if ($LASTEXITCODE -ne 0) { Fail "Docker did not start within 3 minutes." }
}

# The DATABASE_URL in backend/.env decides the host port; keep docker compose in step with it.
$DatabaseUrl = (Get-Content $EnvFile | Where-Object { $_ -match "^DATABASE_URL=" } | Select-Object -First 1) -replace "^DATABASE_URL=", ""
if (-not $DatabaseUrl) { Fail "DATABASE_URL missing from backend\.env." }
if ($DatabaseUrl -match "@[^:/]+:(\d+)/") { $env:POSTGRES_PORT = $Matches[1] }

$services = @("postgres")
if ($WithFirebase) { $services += "firebase" }
Push-Location $RepoRoot
docker compose up -d @services
$composeExit = $LASTEXITCODE
Pop-Location
if ($composeExit -ne 0) { Fail "docker compose failed. Check that port $($env:POSTGRES_PORT) (and 8080/9099/4000 with -WithFirebase) is free." }

# -- 2. Ollama ----------------------------------------------------------------
Step "Checking Ollama"
if (-not (Test-Url "http://localhost:11434/api/version")) {
    $ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
    if (-not $ollama) { Fail "Ollama is not running and 'ollama' is not on PATH. Start Ollama from the Start menu and re-run." }
    # Hidden, with logs in a file: a console window can freeze the server (clicking in it pauses
    # output in QuickEdit mode, and Ollama blocks on its next log line).
    $OllamaLog = Join-Path $env:LOCALAPPDATA "Ollama\rusty-ollama-serve.log"
    New-Item -ItemType Directory -Force (Split-Path $OllamaLog) | Out-Null
    Write-Host "Starting 'ollama serve' in the background (log: $OllamaLog)..."
    Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden `
        -RedirectStandardError $OllamaLog -RedirectStandardOutput "$OllamaLog.out"
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Url "http://localhost:11434/api/version") { break }
    }
    if (-not (Test-Url "http://localhost:11434/api/version")) { Fail "Ollama did not start on port 11434." }
}
Write-Host "Ollama OK"

# -- 3. Backend + frontend ----------------------------------------------------
if (Test-Url "http://localhost:8000/health") {
    Write-Host "`nA backend is already running on port 8000 - not starting another backend/frontend." -ForegroundColor Yellow
    Write-Host "Close the 'Rusty backend' and 'Rusty frontend' windows first if you want a fresh start."
    exit 0
}
Step "Starting backend (port 8000) and frontend (port 5173) in new windows"
# Child windows inherit these. DATABASE_URL is set explicitly because a DATABASE_URL
# environment variable (e.g. from another project) overrides backend/.env.
# PYTHONUTF8 makes Python read backend/.env as UTF-8 instead of the Windows code page.
$env:DATABASE_URL = $DatabaseUrl
$env:PYTHONUTF8 = "1"

Start-Process powershell -WorkingDirectory $Backend -ArgumentList @(
    "-NoExit", "-Command",
    "`$host.UI.RawUI.WindowTitle = 'Rusty backend'; & '$VenvPython' -m uvicorn app.main:app --port 8000 --no-server-header --reload"
)
Start-Process powershell -WorkingDirectory $Frontend -ArgumentList @(
    "-NoExit", "-Command",
    "`$host.UI.RawUI.WindowTitle = 'Rusty frontend'; npm run dev"
)

for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Url "http://localhost:8000/health") { break }
}
if (Test-Url "http://localhost:8000/health") { Write-Host "Backend OK" }
else { Write-Host "Backend not responding yet - check the 'Rusty backend' window." -ForegroundColor Yellow }

Write-Host "`nRusty is starting. Open the Local URL shown in the 'Rusty frontend' window (http://localhost:5173, or 5174+ if 5173 is taken)." -ForegroundColor Green
Write-Host "Don't select text in those windows: Windows pauses a console while text is selected (press Esc to resume)."
Write-Host "Stop: close the two windows, then 'docker compose stop'."
