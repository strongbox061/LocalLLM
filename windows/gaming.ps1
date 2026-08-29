# gaming.ps1 on|off [-Pause] - frees the GPU's memory for a game, and gives it back afterwards (README, "Two-PC setup").
# Works on its own on the helper PC (desktop shortcuts "Gaming mode on/off"; no main PC, Tailscale or SSH needed);
# ./localllm.sh gaming on|off on the main PC runs this same script over SSH.
#   on:  stops the small model and ComfyUI; restarts Open WebUI (the document search models), Whisper, Kokoro and
#        Docling on the CPU, so document chats and voice keep working, slower; switches to the power plan that was
#        active before setup.
#   off: the GPU again for all of them, the Balanced plan.
# Open WebUI and the search gateway keep running throughout (a short restart for Open WebUI).
# Plain ASCII on purpose: Windows PowerShell 5.1 reads a script without a byte order mark as the local code page.
param(
    [Parameter(Mandatory = $true, Position = 0)][ValidateSet('on', 'off', 'status')][string]$Mode,
    [switch]$Pause
)
. (Join-Path $PSScriptRoot 'common.ps1')

$cfg = Read-Config
$services = Join-Path $PSScriptRoot 'services.ps1'
$names = @($cfg.services | ForEach-Object name)
$gpuUsers = @('open-webui', 'whisper', 'kokoro', 'docling') | Where-Object { $names -contains $_ }
$off = @('small-model', 'comfyui') | Where-Object { $names -contains $_ }

function Run-Services($cmd, $name) { & $services $cmd $name | Out-Host }
function Power($guid) { Native { powercfg.exe /setactive $guid } | Out-Null }

switch ($Mode) {
    'status' { if (Test-Gaming) { 'Gaming mode: on' } else { 'Gaming mode: off' } }
    'on' {
        if (Test-Gaming) { Info "Gaming mode is already on." } else {
            New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
            Set-Content -Path $GamingFlag -Value (Get-Date -Format s) -Encoding ASCII
            foreach ($n in $off) { Run-Services 'stop' $n }
            # Restarted: run-service.ps1 reads the flag and starts them on the CPU.
            foreach ($n in $gpuUsers) { if ((Get-Service "localllm-$n" -ErrorAction SilentlyContinue).Status -eq 'Running') { Run-Services 'restart' $n } }
            $before = Join-Path $StateDir 'power-plan-before.txt'
            if (Test-Path $before) { Power ((Get-Content $before -Raw).Trim()); Info "Power plan: the one from before setup." }
            $used = (Native { nvidia-smi.exe --query-gpu=memory.used,memory.total --format=csv,noheader }) -join ' '
            Info "Gaming mode on. GPU memory in use now: $used"
        }
    }
    'off' {
        if (-not (Test-Gaming)) { Info "Gaming mode is already off." } else {
            Remove-Item -Force $GamingFlag
            foreach ($n in $gpuUsers) { if ((Get-Service "localllm-$n" -ErrorAction SilentlyContinue).Status -eq 'Running') { Run-Services 'restart' $n } }
            foreach ($n in $off) { Run-Services 'start' $n }
            Power '381b4222-f694-41f0-9685-ff5bb260df2e'
            Info "Gaming mode off: everything on the GPU again."
        }
    }
}
if ($Pause) { Write-Host ""; Read-Host "Press Enter to close" | Out-Null }
