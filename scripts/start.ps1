# One command setup and start for Windows PowerShell.
# All the work is done by scripts\run.py; this file only finds a Python to run it with.
# Arguments are passed through, for example:  scripts\start.ps1 --check

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $root "scripts\run.py"

foreach ($candidate in @("python", "py")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
        & $candidate $runner @args
        exit $LASTEXITCODE
    }
}

Write-Host "Python was not found. Install Python 3.11 or newer from https://www.python.org/downloads/ and run this again."
exit 1
