[CmdletBinding()]
param(
    [ValidateSet('eu', 'us', 'jp')]
    [string]$Region = 'eu',

    [switch]$SkipVerify,

    [switch]$VerifyOnly,

    [switch]$Clear
)

$ErrorActionPreference = 'Stop'

$variableNames = @(
    'LANGFUSE_ENABLED',
    'LANGFUSE_PUBLIC_KEY',
    'LANGFUSE_SECRET_KEY',
    'LANGFUSE_BASE_URL',
    'LANGFUSE_TRACING_ENVIRONMENT',
    'LANGFUSE_TRACING_ENABLED'
)

$baseUrls = @{
    eu = 'https://cloud.langfuse.com'
    us = 'https://us.cloud.langfuse.com'
    jp = 'https://jp.cloud.langfuse.com'
}

function Send-EnvironmentChangedNotification {
    if (-not ('OdooAgentEnvironmentBroadcast' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class OdooAgentEnvironmentBroadcast
{
    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern bool SendNotifyMessage(
        IntPtr hWnd,
        uint message,
        UIntPtr wParam,
        string lParam
    );
}
'@ | Out-Null
    }

    $hwndBroadcast = [IntPtr]0xffff
    $wmSettingChange = 0x001a
    [void][OdooAgentEnvironmentBroadcast]::SendNotifyMessage(
        $hwndBroadcast,
        $wmSettingChange,
        [UIntPtr]::Zero,
        'Environment'
    )
}

function Set-UserEnvironmentSettings {
    param([hashtable]$Settings)

    $registryKey = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Environment')
    try {
        foreach ($entry in $Settings.GetEnumerator()) {
            $registryKey.SetValue(
                $entry.Key,
                $entry.Value,
                [Microsoft.Win32.RegistryValueKind]::String
            )
            [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, 'Process')
        }
    }
    finally {
        $registryKey.Dispose()
    }

    Send-EnvironmentChangedNotification
}

function Clear-UserEnvironmentSettings {
    param([string[]]$Names)

    $registryKey = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Environment')
    try {
        foreach ($name in $Names) {
            $registryKey.DeleteValue($name, $false)
            [Environment]::SetEnvironmentVariable($name, $null, 'Process')
        }
    }
    finally {
        $registryKey.Dispose()
    }

    Send-EnvironmentChangedNotification
}

function Get-SavedEnvironmentSettings {
    param([string[]]$Names)

    $result = @{}
    foreach ($name in $Names) {
        $value = [Environment]::GetEnvironmentVariable($name, 'User')
        if ([string]::IsNullOrWhiteSpace($value)) {
            if ($name -eq 'LANGFUSE_TRACING_ENABLED') {
                $value = 'true'
            }
            else {
                throw "Missing saved environment variable: $name"
            }
        }

        $result[$name] = $value
        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }

    return $result
}

function Invoke-LangfuseVerification {
    $projectDirectory = Split-Path -Parent $PSCommandPath
    $pythonPath = Join-Path $projectDirectory '.venv\Scripts\python.exe'
    $verifyPath = Join-Path $projectDirectory 'backend\scripts\verify_langfuse.py'

    if (-not (Test-Path -LiteralPath $pythonPath)) {
        throw "Python virtual environment was not found: $pythonPath"
    }

    if (-not (Test-Path -LiteralPath $verifyPath)) {
        throw "Langfuse verification script was not found: $verifyPath"
    }

    Write-Host ''
    Write-Host 'Sending a setup verification trace...'
    & $pythonPath $verifyPath
    if ($LASTEXITCODE -ne 0) {
        throw "Langfuse verification failed with exit code $LASTEXITCODE."
    }
}

if ($Clear -and ($VerifyOnly -or $SkipVerify)) {
    throw '-Clear cannot be combined with -VerifyOnly or -SkipVerify.'
}

if ($VerifyOnly -and $SkipVerify) {
    throw '-VerifyOnly cannot be combined with -SkipVerify.'
}

if ($Clear) {
    Clear-UserEnvironmentSettings -Names $variableNames
    Write-Host 'Langfuse user environment variables were cleared.'
    exit 0
}

$publicKeySecure = $null
$secretKeySecure = $null
$publicKey = $null
$secretKey = $null
$settings = $null

try {
    if ($VerifyOnly) {
        $settings = Get-SavedEnvironmentSettings -Names $variableNames
        Write-Host 'Loaded the saved Langfuse configuration.'
    }
    else {
        Write-Host 'Enter a newly generated Langfuse key pair. Input will be hidden.'
        $publicKeySecure = Read-Host 'Public Key' -AsSecureString
        $secretKeySecure = Read-Host 'Secret Key' -AsSecureString

        $publicKey = [System.Net.NetworkCredential]::new('', $publicKeySecure).Password.Trim()
        $secretKey = [System.Net.NetworkCredential]::new('', $secretKeySecure).Password.Trim()

        if (-not $publicKey.StartsWith('pk-lf-')) {
            throw 'Invalid Public Key. It must start with pk-lf-.'
        }

        if (-not $secretKey.StartsWith('sk-lf-')) {
            throw 'Invalid Secret Key. It must start with sk-lf-.'
        }

        $settings = @{
            LANGFUSE_ENABLED = 'true'
            LANGFUSE_PUBLIC_KEY = $publicKey
            LANGFUSE_SECRET_KEY = $secretKey
            LANGFUSE_BASE_URL = $baseUrls[$Region]
            LANGFUSE_TRACING_ENVIRONMENT = 'development'
            LANGFUSE_TRACING_ENABLED = 'true'
        }

        Set-UserEnvironmentSettings -Settings $settings

        Write-Host ''
        Write-Host 'Langfuse user environment variables were configured.'
        Write-Host "Region: $Region"
        Write-Host "Base URL: $($baseUrls[$Region])"
    }

    if (-not $SkipVerify) {
        Invoke-LangfuseVerification
    }

    Write-Host ''
    Write-Host 'This terminal is ready. Restart other already-running applications before they use Langfuse.'
}
finally {
    $publicKey = $null
    $secretKey = $null
    $settings = $null

    if ($null -ne $publicKeySecure) {
        $publicKeySecure.Dispose()
    }

    if ($null -ne $secretKeySecure) {
        $secretKeySecure.Dispose()
    }
}
