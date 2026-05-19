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
    Write-Host "Starting $($s.Title)..."
    Set-Location $s.Path
    if ($s.Script) {
        & $s.Script 2>&1 | Out-Null
    } elseif ($s.Exe) {
        Start-Process $s.Exe -WorkingDirectory $s.Path -WindowStyle Minimized
    }
    # brief wait so the process appears in Get-Process
    Start-Sleep -Seconds 3
    Write-Host "  $($(State $s))"
}

function Stop-One($s) {
    Write-Host "Stopping $($s.Title)..."
    Stop-Process -Name $s.Name -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    Write-Host "  $($(State $s))"
}

function Show-Status {
    $services = @(
        @{ Name='kokoro-fastapi';    Title='Kokoro TTS';       Port=8080; Path='.\kokoro-fastapi'; Script='start.bat' },
        @{ Name='ollama';            Title='Ollama';           Port=11434; Path='.'; Exe='ollama.exe' },
        @{ Name='open-webui';       Title='Open WebUI';       Port=3000; Path='.\open-webui'; Script='start.bat' }
    )

    if ($Json) {
        $results = $services | ForEach-Object {
            [ordered]@{
                name  = $_.Name
                title = $_.Title
                port  = $_.Port
                state = State $_
            }
        }
        return $results | ConvertTo-Json -Compress
    }

    Write-Host "Service Status:"
    foreach ($svc in $services) {
        Write-Host "  $($svc.Title,-15)[$($svc.Port)] $(State $svc)"
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
