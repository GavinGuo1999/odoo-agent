[CmdletBinding()]
param(
    [ValidateSet('deepseek', 'siliconflow')]
    [string]$Provider = 'deepseek',

    [string]$Model,

    [switch]$SkipVerify,

    [switch]$Clear
)

$ErrorActionPreference = 'Stop'

$providerSettings = @{
    deepseek = @{
        KeyName = 'DEEPSEEK_API_KEY'
        ModelName = 'DEEPSEEK_MODEL'
        BaseUrlName = 'DEEPSEEK_BASE_URL'
        BaseUrl = 'https://api.deepseek.com'
        DefaultModel = 'deepseek-v4-pro'
    }
    siliconflow = @{
        KeyName = 'SILICONFLOW_API_KEY'
        ModelName = 'SILICONFLOW_MODEL'
        BaseUrlName = 'SILICONFLOW_BASE_URL'
        BaseUrl = 'https://api.siliconflow.cn/v1'
        DefaultModel = 'deepseek-ai/DeepSeek-V3.1-Terminus'
    }
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

    [void][OdooAgentEnvironmentBroadcast]::SendNotifyMessage(
        [IntPtr]0xffff,
        0x001a,
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

function Remove-UserEnvironmentSettings {
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

$selected = $providerSettings[$Provider]

if ($Clear) {
    Remove-UserEnvironmentSettings -Names @(
        'LLM_PROVIDER',
        $selected.KeyName,
        $selected.ModelName,
        $selected.BaseUrlName
    )
    Write-Host "$Provider model settings were cleared."
    exit 0
}

if ([string]::IsNullOrWhiteSpace($Model)) {
    $Model = $selected.DefaultModel
}

$apiKeySecure = $null
$apiKey = $null
$settings = $null

try {
    Write-Host "Enter the $Provider API key. Input will be hidden."
    $apiKeySecure = Read-Host 'API Key' -AsSecureString
    $apiKey = [System.Net.NetworkCredential]::new('', $apiKeySecure).Password.Trim()

    if ($apiKey.Length -lt 10) {
        throw 'The API key is too short.'
    }

    $settings = @{
        LLM_PROVIDER = $Provider
        $selected.KeyName = $apiKey
        $selected.ModelName = $Model
        $selected.BaseUrlName = $selected.BaseUrl
    }
    Set-UserEnvironmentSettings -Settings $settings

    Write-Host ''
    Write-Host 'Model environment variables were configured.'
    Write-Host "Provider: $Provider"
    Write-Host "Model: $Model"

    if (-not $SkipVerify) {
        foreach ($name in @(
            'LANGFUSE_ENABLED',
            'LANGFUSE_PUBLIC_KEY',
            'LANGFUSE_SECRET_KEY',
            'LANGFUSE_BASE_URL',
            'LANGFUSE_TRACING_ENVIRONMENT'
        )) {
            $value = [Environment]::GetEnvironmentVariable($name, 'User')
            if (-not [string]::IsNullOrWhiteSpace($value)) {
                [Environment]::SetEnvironmentVariable($name, $value, 'Process')
            }
        }
        [Environment]::SetEnvironmentVariable(
            'LANGFUSE_TRACING_ENABLED',
            'true',
            'Process'
        )

        $projectDirectory = Split-Path -Parent $PSCommandPath
        $pythonPath = Join-Path $projectDirectory '.venv\Scripts\python.exe'
        $smokePath = Join-Path $projectDirectory 'backend\scripts\smoke_backend.py'

        Write-Host ''
        Write-Host 'Sending one short model request...'
        & $pythonPath $smokePath --with-model
        if ($LASTEXITCODE -ne 0) {
            throw "Model verification failed with exit code $LASTEXITCODE."
        }
    }
}
finally {
    $apiKey = $null
    $settings = $null
    if ($null -ne $apiKeySecure) {
        $apiKeySecure.Dispose()
    }
}
