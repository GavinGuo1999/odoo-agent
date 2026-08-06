[CmdletBinding()]
param(
    [string]$PostgresBin = 'C:\Program Files\PostgreSQL\18\bin',
    [string]$HostName = '127.0.0.1',
    [int]$Port = 55432,
    [string]$PostgresAdminUser = 'odoo19',
    [string]$Database = 'odoo_agent_state',
    [string]$User = 'odoo_agent_state'
)

$ErrorActionPreference = 'Stop'

if ($Database -eq 'odoo19_dev') {
    throw 'Agent state must not use the Odoo business database.'
}
if ($Database -notmatch '^[a-zA-Z][a-zA-Z0-9_]{0,62}$') {
    throw 'State database name contains unsupported characters.'
}
if ($User -notmatch '^[a-zA-Z][a-zA-Z0-9_]{0,62}$') {
    throw 'State database user contains unsupported characters.'
}

$psql = Join-Path $PostgresBin 'psql.exe'
$createdb = Join-Path $PostgresBin 'createdb.exe'
if (-not (Test-Path -LiteralPath $psql) -or -not (Test-Path -LiteralPath $createdb)) {
    throw "PostgreSQL tools were not found in $PostgresBin"
}

$bytes = New-Object byte[] 32
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $rng.GetBytes($bytes)
} finally {
    $rng.Dispose()
}
$statePassword = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')

$roleExists = & $psql -h $HostName -p $Port -U $PostgresAdminUser -d postgres -tAc `
    "SELECT 1 FROM pg_roles WHERE rolname='$User'"
if ($LASTEXITCODE -ne 0) {
    throw 'Unable to inspect PostgreSQL roles. Start local PostgreSQL and verify the admin user.'
}

if ($roleExists -eq '1') {
    $roleSql = "ALTER ROLE $User WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '$statePassword';"
} else {
    $roleSql = "CREATE ROLE $User WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '$statePassword';"
}
$roleSql | & $psql -h $HostName -p $Port -U $PostgresAdminUser -d postgres -v ON_ERROR_STOP=1 *> $null
if ($LASTEXITCODE -ne 0) {
    throw 'Unable to configure the dedicated state role.'
}

$databaseExists = & $psql -h $HostName -p $Port -U $PostgresAdminUser -d postgres -tAc `
    "SELECT 1 FROM pg_database WHERE datname='$Database'"
if ($LASTEXITCODE -ne 0) {
    throw 'Unable to inspect PostgreSQL databases.'
}

if ($databaseExists -ne '1') {
    & $createdb -h $HostName -p $Port -U $PostgresAdminUser -O $User -T template0 -E UTF8 $Database
    if ($LASTEXITCODE -ne 0) {
        throw 'Unable to create the independent state database.'
    }
} else {
    "ALTER DATABASE $Database OWNER TO $User;" |
        & $psql -h $HostName -p $Port -U $PostgresAdminUser -d postgres -v ON_ERROR_STOP=1 *> $null
    if ($LASTEXITCODE -ne 0) {
        throw 'Unable to verify the state database owner.'
    }
}

$updates = @{
    AGENT_STATE_ENABLED = 'true'
    AGENT_STATE_DB_HOST = $HostName
    AGENT_STATE_DB_PORT = [string]$Port
    AGENT_STATE_DB_NAME = $Database
    AGENT_STATE_DB_USER = $User
    AGENT_STATE_DB_PASSWORD = $statePassword
}
foreach ($entry in $updates.GetEnumerator()) {
    [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, 'User')
    [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, 'Process')
}

Write-Output 'Independent Agent state database is ready.'
Write-Output "Database: $Database"
Write-Output "User: $User"
Write-Output 'The generated password was saved only in Windows user environment variables.'
Write-Output 'Restart Odoo Agent before using PostgreSQL persistence.'
