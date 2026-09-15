[CmdletBinding()]
param(
    [switch]$Clear,

    [switch]$VerifyOnly
)

# 设置/清除演示门禁的访问口令。
#
# 明文口令**不落盘**：脚本只把 PBKDF2 派生值写进用户环境变量，供
# start-backend.ps1 注入后端进程。留空即关闭门禁（本机开发保持零摩擦）。
#
# 服务器上的做法不同：值放在 odoo-stack/.env 里，由 compose 传进容器。

$ErrorActionPreference = 'Stop'

$variableName = 'AGENT_UI_PASSWORD_HASH'
$projectDirectory = Split-Path -Parent $PSCommandPath
$pythonPath = Join-Path $projectDirectory '.venv\Scripts\python.exe'

function Show-Current {
    $current = [Environment]::GetEnvironmentVariable($variableName, 'User')
    if ([string]::IsNullOrWhiteSpace($current)) {
        Write-Host '当前状态：未设置口令，门禁关闭。'
    }
    else {
        Write-Host "当前状态：门禁已开启（$($current.Substring(0, [Math]::Min(28, $current.Length)))…）"
    }
}

if ($VerifyOnly) {
    Show-Current
    return
}

if ($Clear) {
    [Environment]::SetEnvironmentVariable($variableName, $null, 'User')
    Write-Host '口令已清除，门禁关闭。重启后端后生效。'
    return
}

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "找不到虚拟环境的 Python：$pythonPath"
}

$first = Read-Host -AsSecureString -Prompt '设置访问口令'
$again = Read-Host -AsSecureString -Prompt '再输一次'
$plainFirst = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($first))
$plainAgain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($again))

try {
    if ($plainFirst -ne $plainAgain) { throw '两次输入不一致。' }
    if ([string]::IsNullOrWhiteSpace($plainFirst)) { throw '口令不能为空。' }

    # 通过 stdin 传给 Python，避免明文出现在命令行参数里（那会被其他进程看到）。
    $hash = $plainFirst | & $pythonPath -c @'
import sys
sys.path.insert(0, "backend")
from app.security.session_gate import hash_password
print(hash_password(sys.stdin.readline().rstrip("\n")))
'@
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($hash)) {
        throw '生成口令哈希失败。'
    }

    [Environment]::SetEnvironmentVariable($variableName, $hash.Trim(), 'User')
    Write-Host '口令已设置。重启后端后生效：'
    Write-Host '  .\stop-backend.ps1; .\start-backend.ps1'
}
finally {
    # 明文只在内存里存在这一小会儿。
    $plainFirst = $null
    $plainAgain = $null
    [GC]::Collect()
}
