# GHOST test suite: unit tests, then the end-to-end smoke run.
# Smoke needs the broker and brain already running (scripts\dev.ps1).
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { Write-Error "no venv at .venv\ - see README.md" }
Set-Location $Root

$Fail = 0
Write-Host "=== pytest ==="
& $Py -m pytest brain/tests node/tests -q
if ($LASTEXITCODE -ne 0) { $Fail = 1 }

Write-Host ""
Write-Host "=== smoke (needs broker + brain) ==="
$BrokerHost = if ($env:BROKER_HOST) { $env:BROKER_HOST } else { "127.0.0.1" }
$BrokerPort = if ($env:BROKER_PORT) { [int]$env:BROKER_PORT } else { 1883 }
if ((Test-NetConnection -ComputerName $BrokerHost -Port $BrokerPort -InformationLevel Quiet -WarningAction SilentlyContinue)) {
  & $Py tools/smoke.py
  if ($LASTEXITCODE -ne 0) { $Fail = 1 }
} else {
  Write-Host "broker not reachable - skipping smoke. Start it with scripts\dev.ps1"
  $Fail = 1
}

Write-Host ""
if ($Fail -eq 0) { Write-Host "ALL GREEN" } else { Write-Host "FAILURES - see above" }
exit $Fail
