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
    $d = [string]$cfg.data_dir
    if (-not $d) { Fail "config.json has no data_dir: run ./localllm.sh install-helper on the main PC." }
    $drive = Split-Path -Qualifier $d
    if (-not (Test-Path "$drive")) {
        Fail ("The data folder $d is on drive $drive, which isn't here (another disk, or its letter changed). " +
              "On the main PC: fix HELPER_DATA_DIR in ~/.config/localllm/helper.env (or remove the line to choose " +
              "again), then ./localllm.sh install-helper. Moving the data: README, 'Two-PC setup'.")
    }
    $p = [ordered]@{
        Data = $d
        Bin = Join-Path $d 'bin'
        Python = Join-Path $d 'python'
        Cache = Join-Path $d 'cache'
        Venv = Join-Path $d 'venv'
        Apps = Join-Path $d 'apps'
        Models = Join-Path $d 'models'
        WebUIData = Join-Path $d 'open-webui'
        GatewayData = Join-Path $d 'search-gateway'
        Logs = Join-Path $d 'logs'
        Services = Join-Path $d 'services'
    }
    return [pscustomobject]$p
}

# Environment for uv, Hugging Face and Playwright: everything on the data drive, also for the services, which run as
# SYSTEM (no user profile).
function Set-ToolEnv($paths) {
    $env:UV_PYTHON_INSTALL_DIR = $paths.Python
    $env:UV_CACHE_DIR = Join-Path $paths.Cache 'uv'
    $env:UV_LINK_MODE = 'hardlink'
    $env:HF_HOME = Join-Path $paths.Cache 'huggingface'
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $paths.Cache 'ms-playwright'
    # Python reads the repo's files as UTF-8 (without it, Windows' code page: cp1252 breaks on the tools' text).
    $env:PYTHONUTF8 = '1'
    $env:PATH = "$($paths.Bin);$env:PATH"
}

# Download-File <url> <file>: to a temporary name first, so a broken download never looks finished.
function Download-File($url, $file) {
    $tmp = "$file.part"
    Remove-Item $tmp -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path (Split-Path $file) | Out-Null
    Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
    Move-Item -Force $tmp $file
}

# Windows' pending-restart flags (an update, an installer or a renamed file waiting for the next start).
# (Not PendingFileRenameOperations: Windows keeps entries there that survive every restart, e.g. a DirectX
# compatibility file, so it would ask for restarts forever.)
function Test-PendingRestart {
    $keys = @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending',
              'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired')
    foreach ($k in $keys) { if (Test-Path $k) { return $true } }
    return $false
}

# Run-Installer <name> <exe> <arguments> [minutes]: runs a silent installer with a time limit. While it runs, its CPU
# time and the files under the given folders are watched: when nothing has moved for IdleMinutes, it is most likely
# waiting on a dialog on this PC's screen, and setup stops and says so (click it away, then run setup again: every step
# skips what is done). Returns the exit code; the caller checks the result itself (a step is done when its program
# answers, never on an exit code alone).
function Run-Installer($name, $exe, $arguments, $minutes = 30, $idleMinutes = 5) {
    Info "Installing $name (time limit $minutes minutes)..."
    $p = Start-Process -FilePath $exe -ArgumentList $arguments -PassThru -WindowStyle Hidden
    $start = Get-Date
    $lastCpu = -1
    $lastChange = Get-Date
    while (-not $p.HasExited) {
        Start-Sleep -Seconds 10
        $cpu = 0
        try {
            $ids = @($p.Id) + @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($p.Id)" | ForEach-Object ProcessId)
            foreach ($id in $ids) { $q = Get-Process -Id $id -ErrorAction SilentlyContinue; if ($q) { $cpu += $q.TotalProcessorTime.TotalSeconds } }
        } catch { }
        if ([math]::Abs($cpu - $lastCpu) -gt 0.5) { $lastCpu = $cpu; $lastChange = Get-Date }
        if (((Get-Date) - $lastChange).TotalMinutes -ge $idleMinutes) {
            Fail ("$name has been idle for $idleMinutes minutes: probably waiting on a dialog on the helper PC's screen. " +
                  "Click it away there, then run ./localllm.sh install-helper again.") 4
        }
        if (((Get-Date) - $start).TotalMinutes -ge $minutes) {
            Fail "$name didn't finish within $minutes minutes. Look at the helper PC's screen, then run install-helper again." 4
        }
    }
    return $p.ExitCode
}

# The repo's services on this PC, from config.json: name, Windows service id, port, health path, whether it runs.
function Get-HelperServices($cfg) {
    $list = @()
    foreach ($s in $cfg.services) { $list += $s }
    return $list
}

function Test-Url($url, $timeoutSec = 3) {
    try {
        $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec $timeoutSec
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 400)
    } catch { return $false }
}
