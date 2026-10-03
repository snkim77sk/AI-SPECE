$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

function Get-G2BServiceKey {
    param([Parameter(Mandatory=$true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "G2B_DPAPI_KEY_FILE_NOT_FOUND"
    }

    try {
        $protected = [System.IO.File]::ReadAllBytes($Path)
        if ($protected.Length -gt 0) {
            $plain = [System.Security.Cryptography.ProtectedData]::Unprotect(
                $protected,
                $null,
                [System.Security.Cryptography.DataProtectionScope]::CurrentUser
            )
            $value = [System.Text.Encoding]::UTF8.GetString($plain).Trim()
            if ($value.Length -gt 0) { return $value }
        }
    } catch {
    }

    $text = ([System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)).Trim()
    if (-not $text) { throw "G2B_DPAPI_KEY_FILE_EMPTY" }

    try {
        $protected = [Convert]::FromBase64String($text)
        $plain = [System.Security.Cryptography.ProtectedData]::Unprotect(
            $protected,
            $null,
            [System.Security.Cryptography.DataProtectionScope]::CurrentUser
        )
        $value = [System.Text.Encoding]::UTF8.GetString($plain).Trim()
        if ($value.Length -gt 0) { return $value }
    } catch {
    }

    try {
        $secure = ConvertTo-SecureString -String $text
        $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try {
            $value = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr).Trim()
        } finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
        }
        if ($value.Length -gt 0) { return $value }
    } catch {
        throw "G2B_DPAPI_KEY_DECRYPT_FAILED"
    }

    throw "G2B_DPAPI_KEY_DECRYPT_FAILED"
}

$ProgramRoot = Split-Path -Parent $PSScriptRoot
$G2BRoot = Split-Path -Parent $ProgramRoot
$DbPath = Join-Path $G2BRoot "data\g2b-local.sqlite3"
$SnapshotPath = Join-Path $G2BRoot "snapshot\result-snapshot.json.gz"
$KeyPath = Join-Path $G2BRoot "g2b-service-key.dpapi"
$LogDir = Join-Path $G2BRoot "logs"

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $DbPath) | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $SnapshotPath) | Out-Null
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$VersionFile = Join-Path $ProgramRoot "VERSION.txt"
if (-not (Test-Path -LiteralPath $VersionFile -PathType Leaf)) {
    throw "G2B_PROGRAM_VERSION_FILE_NOT_FOUND"
}
$Version = ([System.IO.File]::ReadAllText($VersionFile)).Trim()
if ($Version -notmatch "4\.1\.\d+$") {
    throw "G2B_PROGRAM_VERSION_4_1_X_REQUIRED"
}

$Python = Get-Command "py.exe" -ErrorAction SilentlyContinue
if (-not $Python) { throw "PYTHON_LAUNCHER_NOT_FOUND" }

$ServiceKey = Get-G2BServiceKey -Path $KeyPath
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogPath = Join-Path $LogDir ("full-collection-" + $Stamp + ".log")

Write-Host ""
Write-Host "============================================================"
Write-Host " AI-SPECE G2B 4.1 LOCAL COMPATIBILITY COLLECTION"
Write-Host " Completed dates are skipped. Collection continues through Korea D-1."
Write-Host " DB: $DbPath"
Write-Host " LOG: $LogPath"
Write-Host "============================================================"
Write-Host ""

$env:G2B_SERVICE_KEY = $ServiceKey
$env:G2B_RUNTIME_ROLE = "LOCAL_COLLECTOR"
$env:G2B_AUTO_SYNC = "0"

try {
    Push-Location $ProgramRoot
    try {
        & py.exe -3.11 "scripts\local_collector.py" `
            --db $DbPath `
            --start-date "2026-09-01" `
            --max-days 31 `
            --progress `
            --output $SnapshotPath 2>&1 |
            Tee-Object -FilePath $LogPath
        $ExitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
} finally {
    $env:G2B_SERVICE_KEY = $null
    $ServiceKey = $null
}

if ($ExitCode -ne 0) {
    Write-Host ""
    Write-Host "[G2B] STOPPED. Local compatibility SQLite remains unchanged."
    Write-Host "[G2B] Run the same launcher again to resume from the stored checkpoint."
    Write-Host "[G2B] LOG: $LogPath"
    exit $ExitCode
}

Write-Host ""
Write-Host "[G2B] COMPLETE."
Write-Host "[G2B] Local compatibility SQLite kept. Result snapshot refreshed."
Write-Host "[G2B] LOG: $LogPath"
exit 0
