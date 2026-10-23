param(
    [ValidateRange(1, 65535)][int]$Port = 5000
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    $taskSystemPython = Get-Command python -ErrorAction SilentlyContinue
    if (-not $taskSystemPython) { throw 'Python 3.10+ is required. Install Python and run this script again.' }
    & $taskSystemPython.Source -c "import sys; assert sys.version_info >= (3,10), 'Python 3.10+ is required'"
    if ($LASTEXITCODE -ne 0) { throw 'Python version check failed.' }
    & $taskSystemPython.Source -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the virtual environment.' }
}
& $taskPython -c "import importlib.util, sys; sys.exit(0 if all(importlib.util.find_spec(m) for m in ('flask', 'waitress')) else 1)"
if ($LASTEXITCODE -ne 0) {
    & $taskPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check network access and try again.' }
}
Write-Host "Open http://127.0.0.1:$Port in your browser."
& $taskPython run.py --port $Port
exit $LASTEXITCODE
