# run-service.ps1 <name> - what each LocalLLM Windows service runs (WinSW starts it; see setup.ps1). Sets the service's
# environment from config.json and secrets\ (read now, so no secret is in the service definition) and runs its
# program in the foreground; WinSW stops the whole process tree.
param([Parameter(Mandatory = $true)][string]$Name)
. (Join-Path $PSScriptRoot 'common.ps1')

$cfg = Read-Config
$paths = Get-Paths $cfg
Set-ToolEnv $paths
$spec = Get-ServiceSpec $Name $cfg $paths
foreach ($k in $spec.env.Keys) { Set-Item -Path "env:$k" -Value ([string]$spec.env[$k]) }
if (-not (Test-Path $spec.exe)) { Fail "$Name is not installed ($($spec.exe) is missing): run ./localllm.sh install-helper on the main PC." }
Set-Location $spec.dir
# The program's log lines on stderr are just output here (WinSW keeps both in the service's log).
$ErrorActionPreference = 'Continue'
if ($Name -eq 'open-webui') {
    # Voice fixes for every browser, served as Open WebUI's /static/loader.js (open-webui\install_loader.py). Voice
    # mode still works without them, so a failure here doesn't stop Open WebUI.
    & (Venv-Python $paths 'open-webui') -I (Join-Path $RepoDir 'open-webui\install_loader.py')
    if ($LASTEXITCODE -ne 0) { Write-Host "WARNING: Open WebUI's voice fixes (loader.js) weren't installed." }
}
Write-Host "$(Get-Date -Format s) starting $Name$(if (Test-Gaming) { ' (gaming mode)' })"
& $spec.exe @($spec.args)
exit $LASTEXITCODE
