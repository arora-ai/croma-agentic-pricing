# Starts the Croma assistant UI at http://localhost:8000 (FastAPI serves the built React app).
# Use -Dev for hot reload: backend on :8000 and the Vite dev server on :5173.
param([switch]$Dev, [switch]$Build)
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$env:PYTHONIOENCODING = "utf-8"

if ($Build -or -not (Test-Path "$PSScriptRoot\frontend\dist")) {
    Push-Location "$PSScriptRoot\frontend"
    if (-not (Test-Path node_modules)) { npm install --no-audit --no-fund }
    npm run build
    Pop-Location
}
if ($Dev) {
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PSScriptRoot\frontend'; npm run dev"
    Write-Host "UI dev server: http://localhost:5173"
}
Write-Host "Assistant: http://localhost:8000"
& "$root\.venv\Scripts\python" -m uvicorn ui.backend.main:app --port 8000 $(if ($Dev) { "--reload" })
