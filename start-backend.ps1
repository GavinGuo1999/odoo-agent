[CmdletBinding()]
param(
    [int]$Port = 8090,
    [switch]$NoReload
)

$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSCommandPath
$pythonPath = Join-Path $projectDirectory '.venv\Scripts\python.exe'

# A terminal opened before the settings were saved still has an old process
# environment. Refresh only Odoo Agent-managed user variables before startup.
$managedVariablePattern = '^(AGENT_STATE_|ANSWER_LLM_|DEEPSEEK_|GENERAL_LLM_|LANGFUSE_|LLM_PROVIDER$|ODOO_|SEMANTIC_PROVIDER$|SILICONFLOW_|SQL_LLM_|WREN_)'
$savedEnvironment = Get-ItemProperty -Path 'HKCU:\Environment' -ErrorAction SilentlyContinue
if ($savedEnvironment) {
    foreach ($property in $savedEnvironment.PSObject.Properties) {
        if ($property.Name -match $managedVariablePattern -and $null -ne $property.Value) {
            [Environment]::SetEnvironmentVariable(
                $property.Name,
                [string]$property.Value,
                'Process'
            )
        }
    }
}

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python virtual environment was not found: $pythonPath"
}

$arguments = @(
    '-m', 'uvicorn',
    'app.main:app',
    '--app-dir', (Join-Path $projectDirectory 'backend'),
    '--host', '127.0.0.1',
    '--port', $Port,
    '--loop', 'app.windows_loop:selector_loop_factory'
)

if (-not $NoReload) {
    $arguments += '--reload'
}

& $pythonPath @arguments
