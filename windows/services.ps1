# services.ps1 - starts, stops and lists the helper PC's AI services (README, "Two-PC setup").
#     services.ps1 start|stop|restart|status [service|all] [-Json] [-Pause]
# The desktop shortcuts run it here (works with the main PC off: nothing here needs the main PC, Tailscale or SSH);
# ./localllm.sh start|stop|status on the main PC runs this same script over SSH, so there is one implementation.
# Start goes in config.json's order (Kokoro first, Open WebUI last); stop the other way round.
# status -Json prints [{name, title, port, state}] (state: running, starting, stopped, not installed) for the main PC.
# Plain ASCII on purpose: Windows PowerShell 5.1 reads a script without a byte order mark as the local code page.
param(
    [Parameter(Position = 0)][ValidateSet('start', 'stop', 'restart', 'status')][string]$Command = 'status',
    [Parameter(Position = 1)][string]$Service = 'all',
    [switch]$Json,
    [switch]$Pause
)
. (Join-Path $PSScriptRoot 'common.ps1')

$cfg = Read-Config
$all = @($cfg.services)
if ($Service -eq 'all') { $chosen = $all } else {
    $chosen = @($all | Where-Object name -eq $Service)
    if (-not $chosen) { Fail "No service '$Service' on this PC. Services: $(($all | ForEach-Object name) -join ', ')" }
}
# How long each may take to answer after a start (the first start of Open WebUI loads its models).
$StartSeconds = @{ 'open-webui' = 420; 'docling' = 240; 'kokoro' = 180; 'whisper' = 180; 'comfyui' = 180; 'small-model' = 120 }
$failed = @()
$appsList = @(([string]$cfg.apps) -split ',' | ForEach-Object { $_.Trim().ToLower() } | Where-Object { $_ })

function State($s) {
    $svc = Get-Service -Name $s.id -ErrorAction SilentlyContinue
    if (-not $svc) { return 'not installed' }
    if ($svc.Status -ne 'Running') { return 'stopped' }
    if (Test-Url "http://127.0.0.1:$($s.port)$($s.health)") { return 'running' }
    return 'starting'
}

# Off in gaming mode (gaming.ps1): they leave the GPU to the game.
$GamingOff = @('small-model', 'comfyui')

function Start-One($s) {
    if ($s.owui_only -and ($appsList -notcontains 'owui')) { Info "$($s.title): off (LOCALLLM_APPS=$($cfg.apps))."; return }
    if ((Test-Gaming) -and $GamingOff -contains $s.name) { Info "$($s.title): off in gaming mode (gaming.ps1 off)."; return }
    $state = State $s
    if ($state -eq 'not installed') { Warn "$($s.title) is not installed: run ./localllm.sh install-helper on the main PC."; $script:failed += $s.name; return }
    if ($state -eq 'running') { Info "$($s.title): already running."; return }
    if ($state -eq 'stopped') { Start-Service -Name $s.id }
    $limit = if ($StartSeconds[$s.name]) { $StartSeconds[$s.name] } else { 60 }
    $t0 = Get-Date
    while (((Get-Date) - $t0).TotalSeconds -lt $limit) {
        if ((State $s) -eq 'running') { Info "$($s.title): running (port $($s.port))."; return }
        if ((Get-Service -Name $s.id).Status -eq 'Stopped') { break }
        Start-Sleep -Seconds 2
    }
    Warn "$($s.title) did not become ready within $limit s. Its log: $((Get-Paths $cfg).Logs)\$($s.id).out.log / .err.log"
    $script:failed += $s.name
}

function Stop-One($s) {
    $svc = Get-Service -Name $s.id -ErrorAction SilentlyContinue
    if (-not $svc) { return }
    if ($svc.Status -ne 'Stopped') { Stop-Service -Name $s.id -Force; Info "$($s.title): stopped." } else { Info "$($s.title): already stopped." }
}

function Show-Status {
    $rows = foreach ($s in $chosen) {
        [pscustomobject]@{ name = $s.name; title = $s.title; port = $s.port; state = (State $s) }
    }
    if ($Json) {
        # Always a list, also for one service (ConvertTo-Json would print a single object).
        Write-Output ('[' + (($rows | ForEach-Object { $_ | ConvertTo-Json -Compress }) -join ',') + ']')
    } else {
        $rows | Format-Table -AutoSize title, port, state | Out-String -Width 120 | Write-Host
        if ($appsList -notcontains 'owui') { Write-Host "Open WebUI services are off (LOCALLLM_APPS=$($cfg.apps))." }
        if (Test-Gaming) { Write-Host "Gaming mode is on (gaming.ps1 off restores everything)." }
    }
}

switch ($Command) {
    'start' {
        if ($Service -eq 'all') {
            foreach ($s in @(
                @{ Name='kokoro-fastapi';    Title='Kokoro TTS';       Port=8080; Path='.\kokoro-fastapi'; Script='start.bat' },
                @{ Name='ollama';            Title='Ollama';           Port=11434; Path='.'; Exe='ollama.exe' },
                @{ Name='open-webui';       Title='Open WebUI';       Port=3000; Path='.\open-webui'; Script='start.bat' }
            )) { Start-One $s }
        } else {
            $svc = $services | Where-Object { $_.Name -eq $Service }
            if ($svc) { Start-One $svc }
        }
    }
    'stop' {
        $order = @(
            @{ Name='open-webui';       Title='Open WebUI';       Port=3000; Path='.\open-webui'; Script='start.bat' },
            @{ Name='ollama';            Title='Ollama';           Port=11434; Path='.'; Exe='ollama.exe' },
            @{ Name='kokoro-fastapi';    Title='Kokoro TTS';       Port=8080; Path='.\kokoro-fastapi'; Script='start.bat' }
        )
        if ($Service -eq 'all') {
            foreach ($s in $order) { Stop-One $s }
        } else {
            $svc = $order | Where-Object { $_.Name -eq $Service }
            if ($svc) { Stop-One $svc }
        }
    }
    'restart' {
        if ($Service -eq 'all') {
            foreach ($s in @(
                @{ Name='open-webui';       Title='Open WebUI';       Port=3000; Path='.\open-webui'; Script='start.bat' },
                @{ Name='ollama';            Title='Ollama';           Port=11434; Path='.'; Exe='ollama.exe' },
                @{ Name='kokoro-fastapi';    Title='Kokoro TTS';       Port=8080; Path='.\kokoro-fastapi'; Script='start.bat' }
            )) { Stop-One $s }
            foreach ($s in @(
                @{ Name='kokoro-fastapi';    Title='Kokoro TTS';       Port=8080; Path='.\kokoro-fastapi'; Script='start.bat' },
                @{ Name='ollama';            Title='Ollama';           Port=11434; Path='.'; Exe='ollama.exe' },
                @{ Name='open-webui';       Title='Open WebUI';       Port=3000; Path='.\open-webui'; Script='start.bat' }
            )) { Start-One $s }
        } else {
            $svc = $services | Where-Object { $_.Name -eq $Service }
            if ($svc) { Stop-One $svc; Start-One $svc }
        }
    }
    'status' { Show-Status }
}
