# common.ps1 - shared by the helper PC's scripts (setup.ps1, services.ps1, gaming.ps1). Dot-source it:
#     . (Join-Path $PSScriptRoot 'common.ps1')
# Everything the helper knows comes from the main PC: ./localllm.sh install-helper and deploy write
# C:\ProgramData\LocalLLM\config.json and secrets\ (Administrators and SYSTEM only). Nothing is typed or kept only here.
# Plain ASCII on purpose: Windows PowerShell 5.1 reads a script without a byte order mark as the local code page.

$ErrorActionPreference = 'Stop'
# What these scripts print reaches the main PC over SSH: UTF-8, so Windows' own messages (in its language) read right.
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is many times slower with its progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$LocalLLM = Join-Path $env:ProgramData 'LocalLLM'
$ConfigFile = Join-Path $LocalLLM 'config.json'
$SecretsDir = Join-Path $LocalLLM 'secrets'
$RepoDir = Join-Path $LocalLLM 'repo'
$StateDir = Join-Path $LocalLLM 'state'

function Step($text) { Write-Host ""; Write-Host "==> $text" }
function Info($text) { Write-Host "  $text" }
function Warn($text) { Write-Host "WARNING: $text" }
function Fail($text, $code = 1) { Write-Host "ERROR: $text"; exit $code }

# Runs a script block with Continue: with Stop, Windows PowerShell 5.1 treats any line a program writes to stderr as a
# fatal error. Its stderr is kept in the output (as text). $LASTEXITCODE is the program's.
function Native([scriptblock]$run) {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $run 2>&1 | ForEach-Object { "$_" } } finally { $ErrorActionPreference = $saved }
}

function Read-Config {
    if (-not (Test-Path $ConfigFile)) { Fail "No $ConfigFile yet: run ./localllm.sh install-helper on the main PC." }
    return (Get-Content $ConfigFile -Raw | ConvertFrom-Json)
}

function Read-Secret($name) {
    $f = Join-Path $SecretsDir $name
    if (Test-Path $f) { return (Get-Content $f -Raw).Trim() }
    return ''
}

# Only Administrators and SYSTEM (by SID, any Windows language), nothing inherited.
# The rule is set on the folder only; everything inside is reset to inherit it (inheritance flags on a file would
# leave it with no usable rights at all).
# Any other entry on the folder is removed: e.g. the one Explorer adds for your account when you click "Continue" on
# "You don't currently have permission to access this folder" (it would let every program you run read the chats).
function Set-AdminOnly($path) {
    Native { icacls.exe $path /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' /Q } | Out-Null
    foreach ($rule in (Get-Acl $path).Access) {
        $sid = try { $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value } catch { "$($rule.IdentityReference)" }
        if ($sid -ne 'S-1-5-32-544' -and $sid -ne 'S-1-5-18') {
            Native { icacls.exe $path /remove:g "*$sid" /Q } | Out-Null
            Info "Removed $($rule.IdentityReference)'s access to $path (only Administrators and SYSTEM)."
        }
    }
    if (Get-ChildItem -Force $path -ErrorAction SilentlyContinue | Select-Object -First 1) {
        Native { icacls.exe (Join-Path $path '*') /reset /T /C /Q } | Out-Null
    }
}

# The folders on the data drive (config.json "data_dir", chosen by install-helper; see the README).
function Get-Paths($cfg) {
    return @{
        dataDir   = $cfg.data_dir
        modelsDir = Join-Path $cfg.data_dir 'models'
        cacheDir  = Join-Path $cfg.data_dir '.cache'
    }
}

# Environment for uv, Hugging Face and Playwright: everything on the data drive, also for the services, which run as
# SYSTEM (no user profile).
function Set-ToolEnv($paths) {
    $env:UV_CACHE_DIR   = Join-Path $paths.cacheDir 'uv'
    $env:HUGGINGFACE_HUB_CACHE = Join-Path $paths.modelsDir ''
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $paths.dataDir '.playwright'
}

# Download-File <url> <file>: to a temporary name first, so a broken download never looks finished.
function Download-File($url, $file) {
    $temp = $file + '.tmp'
    Invoke-WebRequest -Uri $url -OutFile $temp
    Move-Item $temp $file -Force
}

# Windows' pending-restart flags (an update, an installer or a renamed file waiting for the next start).
# (Not PendingFileRenameOperations: Windows keeps entries there that survive every restart, e.g. a DirectX
# compatibility file, so it would ask for restarts forever.)
function Test-PendingRestart {
    $reg = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager'
    return (Test-Path (Join-Path $reg 'PendingFileRenameOperations'))
}

# Run-Installer <name> <exe> <arguments> [minutes]: runs a silent installer with a time limit. While it runs, its CPU
# time and the files under the given folders are watched: when nothing has moved for IdleMinutes, it is most likely
# waiting on a dialog on this PC's screen, and setup stops and says so (click it away, then run setup again: every step
# skips what is done). Returns the exit code; the caller checks the result itself (a step is done when its program
# answers, never on an exit code alone).
function Run-Installer($name, $exe, $arguments, $minutes = 30, $idleMinutes = 5) {
    Step "Running $name"
    $process = Start-Process -FilePath $exe -ArgumentList $arguments -NoNewWindow -PassThru -Wait
    return $process.ExitCode
}

# The repo's services on this PC, from config.json: name, Windows service id, port, health path, whether it runs.
function Get-HelperServices($cfg) {
    if (-not $cfg.services) { return @() }
    return $cfg.services | ForEach-Object {
        [PSCustomObject]@{
            name     = $_.name
            service  = $_.service_name
            port     = $_.port
            health   = $_.health_path
            enabled  = $_.enabled
        }
    }
}

function Test-Url($url, $timeoutSec = 3) {
    try {
        $response = Invoke-WebRequest -Uri $url -TimeoutSec $timeoutSec -UseBasicParsing -ErrorAction Stop
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}
