[CmdletBinding()]
param(
    [int]$Port = 8090
)

$ErrorActionPreference = 'Stop'
$projectDirectory = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSCommandPath))
$backendDirectory = Join-Path $projectDirectory 'backend'
$startScript = Join-Path $projectDirectory 'start-backend.ps1'
$escapedBackendDirectory = [regex]::Escape($backendDirectory)
$escapedStartScript = [regex]::Escape($startScript)
$portPattern = "--port\s+$Port(?:\s|$)"
$appDirectoryPattern = '--app-dir\s+"?' + $escapedBackendDirectory + '"?(?:\s|$)'
$startScriptPattern = '(?i)-File\s+"?' + $escapedStartScript + '"?(?:\s|$)'

$allProcesses = @(Get-CimInstance Win32_Process)
$backendProcesses = @(
    $allProcesses | Where-Object {
        $_.Name -eq 'python.exe' -and
        $_.CommandLine -match '-m\s+uvicorn\s+app\.main:app' -and
        $_.CommandLine -match $appDirectoryPattern -and
        $_.CommandLine -match $portPattern
    }
)
$launcherProcesses = @(
    $allProcesses | Where-Object {
        $_.Name -eq 'powershell.exe' -and
        $_.CommandLine -match '(?i)-NoExit' -and
        $_.CommandLine -match $startScriptPattern
    }
)

$listeners = @(
    Get-NetTCPConnection `
        -LocalAddress '127.0.0.1' `
        -LocalPort $Port `
        -State Listen `
        -ErrorAction SilentlyContinue
)
$backendProcessIds = @($backendProcesses | ForEach-Object { [int]$_.ProcessId })
$unrelatedListeners = @(
    $listeners | Where-Object { [int]$_.OwningProcess -notin $backendProcessIds }
)

if ($unrelatedListeners.Count -gt 0) {
    $owners = $unrelatedListeners | ForEach-Object {
        $owner = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
        if ($owner) { "$($owner.ProcessName) (PID $($owner.Id))" } else { "PID $($_.OwningProcess)" }
    }
    Write-Host "Port $Port is owned by another program: $($owners -join ', ')" -ForegroundColor Yellow
    Write-Host 'No process was stopped.'
    exit 2
}

if ($backendProcesses.Count -eq 0 -and $launcherProcesses.Count -eq 0) {
    Write-Host "Odoo Agent is not running on port $Port." -ForegroundColor Yellow
    exit 0
}

# Stop project-owned Python children before closing launcher windows.
$backendProcesses |
    Sort-Object ParentProcessId -Descending |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$launcherProcesses |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Start-Sleep -Milliseconds 300
$remainingListener = Get-NetTCPConnection `
    -LocalAddress '127.0.0.1' `
    -LocalPort $Port `
    -State Listen `
    -ErrorAction SilentlyContinue

if ($remainingListener) {
    Write-Host "Odoo Agent processes stopped, but port $Port is still in use." -ForegroundColor Yellow
    exit 1
}

Write-Host "Odoo Agent stopped. Port $Port is available." -ForegroundColor Green
