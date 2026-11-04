# bootstrap.ps1 - the one script run by hand on the helper PC (README, "Connecting the helper PC").
#
# Don't run this file itself: ./localllm.sh setup-helper on the main PC writes ~/.cache/localllm/bootstrap.local.ps1, a copy with
# the main PC's public SSH key filled in. Copy that one to the helper PC and run it in PowerShell as Administrator:
#     powershell -ExecutionPolicy Bypass -File <path to>\bootstrap.local.ps1
#
# What it does (safe to run again; each step skips what is already done):
#   1. Checks that Tailscale is installed and signed in, and turns on its "Run unattended" mode, so the connection
#      stays up with nobody signed in to Windows.
#   2. Installs Windows' own OpenSSH server, starts it now and at every boot.
#   3. Key login only: the main PC's public key is the only way in (passwords are turned off), and PowerShell is the
#      shell for SSH commands.
#   4. The firewall lets SSH in only from Tailscale addresses and the local network.
#   5. Prints this PC's Windows user name, its Tailscale name and the SSH fingerprint, for setup-helper on the main PC.
# Everything else on this PC is installed from the main PC afterwards (./localllm.sh install-helper), over SSH.
# Plain ASCII on purpose: Windows PowerShell 5.1 reads a script without a byte order mark as the local code page.

$ErrorActionPreference = 'Stop'
$PublicKey = '@MAIN_PC_PUBLIC_KEY@'

function Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host ""; Write-Host "ERROR: $text" -ForegroundColor Red; exit 1 }
function Warn($text) { Write-Host "WARNING: $text" -ForegroundColor Yellow }
# Runs a program and returns its output; its error messages are dropped (with 'Stop', Windows PowerShell 5.1 would
# treat any line a program writes to stderr as a fatal error). $LASTEXITCODE is the program's.
function Quiet([scriptblock]$run) {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $run 2>$null } finally { $ErrorActionPreference = $saved }
}

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Fail "Run this in PowerShell as Administrator (Start, type powershell, right-click, Run as administrator)." }
if ($PublicKey -notmatch '^ssh-ed25519 [A-Za-z0-9+/=]+') {
    Fail "No public key in this file: run ./localllm.sh setup-helper on the main PC and use the bootstrap.local.ps1 it writes."
}

# --- 1. Tailscale ---
Step "Tailscale"
$tailscale = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
if (-not (Test-Path $tailscale)) {
    Fail "Tailscale is not installed. Install it from https://tailscale.com/download/windows, sign in with the same account as on the main PC, then run this again."
}
$status = $null
try { $status = (Quiet { & $tailscale status --json } | Out-String) | ConvertFrom-Json } catch { }
if (-not $status -or $status.BackendState -ne 'Running') {
    Fail "Tailscale is installed but not signed in. Click its icon in the taskbar, sign in (same account as on the main PC), then run this again."
}
$tailscaleName = ([string]$status.Self.DNSName).TrimEnd('.')
Write-Host "Signed in as $tailscaleName"
# Unattended: Tailscale keeps running when nobody is signed in to Windows (after a restart, with the screen locked).
Quiet { & $tailscale set --unattended=true } | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Run unattended: on"
} else {
    Warn "Could not turn on Run unattended from here: right-click the Tailscale icon, Settings, tick Run unattended."
}

# --- 2. OpenSSH server ---
Step "OpenSSH server"
$cap = Get-WindowsCapability -Online | Where-Object Name -like 'OpenSSH.Server*' | Select-Object -First 1
if (-not $cap) { Fail "This Windows has no OpenSSH server to install (Settings, System, Optional features)." }
if ($cap.State -ne 'Installed') {
    Write-Host "Installing $($cap.Name) (needs the internet, can take a few minutes)..."
    Add-WindowsCapability -Online -Name $cap.Name | Out-Null
}
Set-Service -Name sshd -StartupType Automatic
if ((Get-Service sshd).Status -ne 'Running') { Start-Service sshd }
Write-Host "sshd is installed, running, and starts at boot."

# --- 3. Key login only, PowerShell as the shell ---
Step "Key login"
$sshDir = Join-Path $env:ProgramData 'ssh'
# For an administrator, Windows' OpenSSH reads only this file (not the user's own .ssh\authorized_keys).
$keys = Join-Path $sshDir 'administrators_authorized_keys'
$existing = @()
if (Test-Path $keys) { $existing = @(Get-Content $keys) }
if ($existing -contains $PublicKey) {
    Write-Host "The main PC's key is already allowed."
} else {
    Add-Content -Path $keys -Value $PublicKey -Encoding ASCII
    Write-Host "Allowed the main PC's key."
}
# Only Administrators and SYSTEM may read or change it (sshd ignores the file otherwise). SIDs: any Windows language.
& icacls.exe $keys /inheritance:r /grant '*S-1-5-32-544:F' /grant '*S-1-5-18:F' | Out-Null

$config = Join-Path $sshDir 'sshd_config'
$lines = @(Get-Content $config)
$want = [ordered]@{ 'PubkeyAuthentication' = 'yes'; 'PasswordAuthentication' = 'no'; 'KbdInteractiveAuthentication' = 'no' }
$changed = $false
foreach ($name in $want.Keys) {
    $line = "$name $($want[$name])"
    $at = -1
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^\s*#?\s*$name\s") { $at = $i; break }
        if ($lines[$i] -match '^\s*Match\s') { break }   # settings for everyone go before the first Match block
    }
    if ($at -ge 0) {
        if ($lines[$at] -ne $line) { $lines[$at] = $line; $changed = $true }
    } else {
        $lines = @($line) + $lines
        $changed = $true
    }
}
if ($changed) {
    Set-Content -Path $config -Value $lines -Encoding ASCII
    Write-Host "sshd_config: passwords off, keys only."
} else {
    Write-Host "sshd_config: already keys only."
}
$shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
New-Item -Path 'HKLM:\SOFTWARE\OpenSSH' -Force | Out-Null
New-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell -Value $shell -PropertyType String -Force | Out-Null
Write-Host "Shell for SSH commands: Windows PowerShell"

# --- 4. Firewall: SSH only from Tailscale (100.64.0.0/10, fd7a:115c:a1e0::/48) and the local network ---
Step "Firewall"
$remote = @('100.64.0.0/10', 'fd7a:115c:a1e0::/48', 'LocalSubnet')
$rule = Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue
if ($rule) {
    Set-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -Enabled True -Profile Any -RemoteAddress $remote
} else {
    New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' -Direction Inbound `
        -Protocol TCP -LocalPort 22 -Action Allow -Profile Any -RemoteAddress $remote | Out-Null
}
Write-Host "SSH (port 22) allowed only from Tailscale and the local network."

Restart-Service sshd

# --- 5. What the main PC needs ---
Step "Done. For ./localllm.sh setup-helper on the main PC:"
$fingerprint = & (Join-Path $env:SystemRoot 'System32\OpenSSH\ssh-keygen.exe') -lf (Join-Path $sshDir 'ssh_host_ed25519_key.pub')
Write-Host "  Windows user name: $env:USERNAME"
Write-Host "  Tailscale name:    $tailscaleName"
Write-Host "  SSH fingerprint:   $fingerprint"
Write-Host ""
Write-Host "When ssh on the main PC asks whether to trust the helper, answer yes only if it shows this same fingerprint."
