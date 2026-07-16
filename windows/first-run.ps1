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
$tailscale = "$env:ProgramFiles\Tailscale\Tailscale.exe"

if (-not (Test-Path $tailscale)) {
    Fail("Tailscale is not installed. Install it from https://tailscale.com/download/windows first.")
}

$status = & $tailscale status 2>$null | ConvertFrom-Json
if (-not $status -or $status.BackendState -ne 'Running') {
    Fail("Tailscale is not running. Sign in with: & '$tailscale' login")
}

# Unattended: Tailscale keeps running when nobody is signed in to Windows (after a restart, with the screen locked).
if ((Quiet { & $tailscale up --authkey=$env:TSAK })) -eq 0 {
    # auth key was provided and worked; nothing more to do
}

# --- 2. OpenSSH server ---
$cap = Get-AppCapability -Online -Name "OpenSSH.Server~~~~0.0.1.0" -ErrorAction SilentlyContinue
if ($cap.State -ne 'Installed') {
    Add-AppCapability -Online -Name "OpenSSH.Server~~~~0.0.1.0"
    if ($LASTEXITCODE -ne 0) {
        Fail("Failed to install OpenSSH server.")
    }
}

Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
if (-not (Get-Service sshd).Status -eq 'Running') {
    Fail("Failed to start sshd service.")
}

# --- 3. Key login only, PowerShell as the shell ---
# For an administrator, Windows' OpenSSH reads only this file (not the user's own .ssh\authorized_keys).
$authKeys = "$env:ProgramFiles\OpenSSH\etc\sshd_config"
$configContent = Get-Content $authKeys -ErrorAction SilentlyContinue

# Set PowerShell as the default shell for SSH commands
if ($configContent -notmatch '^Shell\s') {
    (Get-Content $authKeys) -replace '^#?Shell\b', 'Shell C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' | Set-Content $authKeys
}

# Add the public key if not already present
$existing = Get-Content "$env:ProgramFiles\OpenSSH\etc\authorized_keys" -ErrorAction SilentlyContinue
if ($existing -contains $PublicKey) {
    Write-Host "Public key already in authorized_keys."
} else {
    Add-Content "$env:ProgramFiles\OpenSSH\etc\authorized_keys" $PublicKey
}

# Only Administrators and SYSTEM may read or change it (sshd ignores the file otherwise). SIDs: any Windows language.
$want = @{
    'BUILTIN\Administrators' = 'FullControl'
    'SYSTEM'                 = 'FullControl'
}

$changed = $false
$acl = Get-Acl $authKeys
foreach ($name in $want.Keys) {
    $rule = $acl.Access | Where-Object { $_.IdentityReference -eq $name -and $_.FileSystemRights -eq $want[$name] }
    if (-not $rule) {
        $identity = New-Object System.Security.Principal.NTAccount($name)
        $ruleType = [System.Security.AccessControl.AccessControlType]::Allow
        $rights = [System.Security.FileSystemRights]::FullControl
        $inherit = [System.Security.AccessControl.InheritanceFlags]::None
        $propagation = [System.Security.AccessControl.PropagationFlags]::None
        $ace = New-Object System.Security.AccessControl.FileSystemAccessRule($identity, $rights, $ruleType, $inherit, $propagation)
        $acl.AddAccessRule($ace)
        $changed = $true
    }
}

if ($changed) {
    Set-Acl $authKeys $acl
}

# Disable password authentication in sshd_config
$sshdConfig = "$env:ProgramFiles\OpenSSH\etc\sshd_config"
$configLines = Get-Content $sshdConfig
$modified = $false
for ($i = 0; $i -lt $configLines.Count; $i++) {
    if ($configLines[$i] -match '^#?\s*PasswordAuthentication\s') {
        $configLines[$i] = 'PasswordAuthentication no'
        $modified = $true
    }
}
if (-not $modified) {
    $configLines += 'PasswordAuthentication no'
}
$configLines | Set-Content $sshdConfig

Restart-Service sshd

# --- 4. Firewall: SSH only from Tailscale (100.64.0.0/10, fd7a:115c:a1e0::/48) and the local network ---
$rule = Get-NetFirewallRule -DisplayName 'OpenSSH Server (sshd)' -ErrorAction SilentlyContinue
if (-not $rule) {
    New-NetFirewallRule -DisplayName 'OpenSSH Server (sshd)' -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow
}

# --- 5. What the main PC needs ---
$windowsUser = $env:USERNAME
$tailscaleName = & $tailscale status 2>$null | ConvertFrom-Json | Select-Object -ExpandProperty Self | Select-Object -ExpandProperty DNSName
$sshHostKey = Get-Content "$env:ProgramFiles\OpenSSH\etc\ssh_host_ed25519_key.pub" -ErrorAction SilentlyContinue

Step("Setup complete.")
Write-Host "Windows user: $windowsUser"
Write-Host "Tailscale name: $tailscaleName"
Write-Host "SSH host key: $sshHostKey"
