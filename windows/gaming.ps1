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
        Write-Host "=== Gaming mode ON ===" -ForegroundColor Green
        Write-Host "Stopping GPU services..."

        foreach ($svc in $gpuServices) {
            Write-Host "  Stopping $($svc.Name)..."
            Run-Services stop $svc.Name
        }

        # Save active plan and switch to High Performance
        Write-Host "Switching power plan to High Performance..."
        $hpGuid = (powercfg.exe /list | Select-String 'High performance').Line.Split(':')[0].Trim()
        if ($hpGuid) { Power $hpGuid }

        # Restart CPU services that need a nudge
        foreach ($svc in $cpuServices) {
            if (-not $svc.Stop) {
                Write-Host "  Restarting $($svc.Name) on CPU..."
                Run-Services stop $svc.Name
                Start-Sleep -Seconds 2
                Run-Services start $svc.Name
            }
        }

        if ($Pause) {
            Write-Host "`nGPU freed. Press Enter to return to normal mode..." -ForegroundColor Yellow
            Read-Host
        }
    }

    'off' {
        Write-Host "=== Gaming mode OFF ===" -ForegroundColor Green
        Write-Host "Restoring GPU services..."

        # Restart Open WebUI (short restart)
        Run-Services stop open-webui
        Start-Sleep -Seconds 1
        Run-Services start open-webui

        foreach ($svc in $gpuServices) {
            Write-Host "  Starting $($svc.Name)..."
            Run-Services start $svc.Name
        }

        # Restore original power plan
        Write-Host "Restoring power plan..."
        Power $activePlan

        Write-Host "All services restored." -ForegroundColor Green
    }

    default {
        Write-Host "Usage: gaming.ps1 on|off [-Pause]" -ForegroundColor Red
        Write-Host "  on   - free GPU for gaming" -ForegroundColor Yellow
        Write-Host "  off  - restore GPU to LLM services" -ForegroundColor Yellow
        Write-Host "  -Pause - wait for Enter before restoring" -ForegroundColor Yellow
        exit 1
    }
}
