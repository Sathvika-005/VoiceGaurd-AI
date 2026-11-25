# VoiceGuard AI - Windows PowerShell Local Dev Launcher
# Starts FastAPI (Uvicorn with hot reload) and a Cloudflare Quick Tunnel in a second window.

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$VenvActivate = Join-Path $RepoRoot ".venv\Scripts\Activate.ps1"
if (Test-Path $VenvActivate) {
    Write-Host "Activating virtual environment..." -ForegroundColor Green
    & $VenvActivate
} else {
    Write-Host "No .venv found at $VenvActivate. Using global python on PATH." -ForegroundColor Yellow
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "WARNING: ffmpeg not found on PATH. Install with: winget install Gyan.FFmpeg" -ForegroundColor Yellow
}

if (Get-Command cloudflared -ErrorAction SilentlyContinue) {
    Write-Host "Opening second window for Cloudflare Quick Tunnel (http://localhost:8000)..." -ForegroundColor Cyan
    Start-Process powershell -ArgumentList @(
        "-NoExit",
        "-Command",
        "Write-Host 'VoiceGuard AI Quick Tunnel -> http://localhost:8000' -ForegroundColor Cyan; cloudflared tunnel --url http://localhost:8000"
    )
} else {
    Write-Host "WARNING: cloudflared not found. Install with: winget install Cloudflare.cloudflared" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor DarkCyan
Write-Host " REMINDERS FOR WHATSAPP WEBHOOK DEMO:" -ForegroundColor Cyan
Write-Host " 1. Cloudflare Quick Tunnel URL changes on EVERY restart." -ForegroundColor White
Write-Host "    Copy the new https://*.trycloudflare.com URL from the" -ForegroundColor White
Write-Host "    tunnel window and set Callback URL = <tunnel>/webhook" -ForegroundColor White
Write-Host "    in Meta Developer Console -> WhatsApp -> Configuration." -ForegroundColor White
Write-Host " 2. Verify Token in Meta MUST match WHATSAPP_VERIFY_TOKEN in .env." -ForegroundColor White
Write-Host " 3. Temporary Meta access tokens expire roughly every 24 hours." -ForegroundColor White
Write-Host "============================================================" -ForegroundColor DarkCyan
Write-Host ""

python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
