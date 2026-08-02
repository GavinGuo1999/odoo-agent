[CmdletBinding()]
param(
    [int]$Port = 8090,
    [switch]$NoReload
)

$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSCommandPath
$pythonPath = Join-Path $projectDirectory '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python virtual environment was not found: $pythonPath"
}

$arguments = @(
    '-m', 'uvicorn',
    'app.main:app',
    '--app-dir', (Join-Path $projectDirectory 'backend'),
    '--host', '127.0.0.1',
    '--port', $Port
)

if (-not $NoReload) {
    $arguments += '--reload'
}

& $pythonPath @arguments

