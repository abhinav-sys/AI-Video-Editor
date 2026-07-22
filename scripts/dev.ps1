# Start Bulkcut backend + frontend for local development (Windows PowerShell)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $Root) { $Root = (Resolve-Path "$PSScriptRoot\..").Path }

$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$VenvPython = Join-Path $Backend ".venv\Scripts\python.exe"
$VenvUvicorn = Join-Path $Backend ".venv\Scripts\uvicorn.exe"

Write-Host "Bulkcut local dev" -ForegroundColor Cyan
Write-Host "Root: $Root"

if (-not (Test-Path $VenvUvicorn)) {
  Write-Host "Backend venv missing. Creating and installing..." -ForegroundColor Yellow
  Push-Location $Backend
  python -m venv .venv
  & .\.venv\Scripts\python -m pip install -r requirements.txt
  & .\.venv\Scripts\alembic upgrade head
  Pop-Location
}

if (-not (Test-Path (Join-Path $Frontend "node_modules"))) {
  Write-Host "Frontend node_modules missing. Running npm install --strict-ssl=false ..." -ForegroundColor Yellow
  Push-Location $Frontend
  npm install --no-audit --no-fund --strict-ssl=false
  Pop-Location
}

Push-Location $Backend
& .\.venv\Scripts\alembic upgrade head | Out-Null
Pop-Location

Write-Host "Starting backend on http://localhost:8000 ..." -ForegroundColor Green
Start-Process -FilePath $VenvUvicorn -ArgumentList "app.main:app","--reload","--host","0.0.0.0","--port","8000" -WorkingDirectory $Backend

Start-Sleep -Seconds 2

Write-Host "Starting frontend on http://localhost:3000 ..." -ForegroundColor Green
Start-Process -FilePath "npm" -ArgumentList "run","dev" -WorkingDirectory $Frontend

Write-Host ""
Write-Host "Open http://localhost:3000" -ForegroundColor Cyan
Write-Host "API docs http://localhost:8000/docs"
Write-Host "Health  http://localhost:8000/health"
Write-Host ""
Write-Host "Manual check: drop 1-2 MP4s, click an example prompt, Start batch, then Download ZIP."
