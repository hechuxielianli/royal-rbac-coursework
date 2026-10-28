$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw '请先创建.venv并安装requirements-lock.txt。' }
Push-Location (Join-Path $taskRoot 'sut')
try {
    & $taskPython -X utf8 -m pytest tests -q --json-report --json-report-file=../evidence/original-suite-after.json
    if ($LASTEXITCODE -ne 0) { throw '原有测试失败。' }
} finally { Pop-Location }
Push-Location $taskRoot
try {
    & $taskPython -X utf8 -m pytest tests/test_function_spec.py tests/test_equivalence.py tests/test_random.py -q --json-report --json-report-file=evidence/new-tests-after.json
    if ($LASTEXITCODE -ne 0) { throw '新增功能、规格、等价类或性质测试失败。' }
    & $taskPython -X utf8 -m pytest tests/test_regressions.py -q --json-report --json-report-file=evidence/regression-after.json
    if ($LASTEXITCODE -ne 0) { throw '缺陷确认失败。' }
    & $taskPython -X utf8 -m pytest tests/test_session_security.py -q --json-report --json-report-file=evidence/session-security.json
    if ($LASTEXITCODE -ne 0) { throw '会话扩展测试失败。' }
} finally { Pop-Location }
