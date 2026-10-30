# GHOST dev stack: broker + brain + dashboard. Ctrl-C stops all three.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
  Write-Error "no venv at .venv\ - run: python -m venv .venv ; .venv\Scripts\python -m pip install -r requirements.txt"
}

# --- broker: docker first, native mosquitto as fallback (see docs/DECISIONS.md D4)
$Jobs = @()
docker info *> $null
if ($LASTEXITCODE -eq 0) {
  Write-Host "[dev] starting mosquitto via docker compose"
  docker compose up -d mosquitto
} elseif (Get-Command mosquitto -ErrorAction SilentlyContinue) {
  Write-Host "[dev] no docker daemon - starting native mosquitto"
  $Jobs += Start-Process -PassThru mosquitto -ArgumentList "-c", (Join-Path $Root "mosquitto\mosquitto.conf")
} else {
  Write-Error "no docker and no mosquitto on PATH - install one of them"
}

$BrainPort = if ($env:BRAIN_PORT) { $env:BRAIN_PORT } else { "8000" }
Write-Host "[dev] brain     -> http://localhost:$BrainPort  (docs at /docs, web node at /node)"
$Jobs += Start-Process -PassThru $Py -ArgumentList "-m", "uvicorn", "brain.app:app", "--host", "0.0.0.0", "--port", $BrainPort

if (Test-Path (Join-Path $Root "dashboard\node_modules")) {
  Write-Host "[dev] dashboard -> http://localhost:3000"
  $Jobs += Start-Process -PassThru -WorkingDirectory (Join-Path $Root "dashboard") "npm" -ArgumentList "run", "dev"
} else {
  Write-Host "[dev] dashboard skipped - run: cd dashboard ; npm install"
}

try {
  Write-Host "[dev] running. Ctrl-C to stop."
  Wait-Process -Id ($Jobs | ForEach-Object { $_.Id })
} finally {
  Write-Host "[dev] stopping"
  foreach ($j in $Jobs) { if (-not $j.HasExited) { Stop-Process -Id $j.Id -Force -ErrorAction SilentlyContinue } }
}
