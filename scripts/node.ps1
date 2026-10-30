# Start one GHOST python edge node. All arguments pass through to node.main.
#   scripts\node.ps1 --cam cam-01 --source 0
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { Write-Error "no venv at .venv\ - see README.md" }
Set-Location $Root
& $Py -m node.main @args
exit $LASTEXITCODE
