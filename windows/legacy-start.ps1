# legacy-start.ps1 — Early PowerShell service starter for LocalLLM
# Starts the local LLM inference server (Qwen3.6-35B-A3B with vision)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$configFile = Join-Path $scriptDir "..\llm-config.json"

function Write-Log {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$timestamp] $Message"
}

Write-Log "Starting LocalLLM service..."

if (-not (Test-Path $configFile)) {
    Write-Log "ERROR: Configuration file not found: $configFile"
    exit 1
}

$config = Get-Content $configFile -Raw | ConvertFrom-Json
$modelPath = $config.model.path
$visionModel = $config.model.visionPath
$port = $config.server.port

Write-Log "Model: $modelPath"
if ($visionModel) { Write-Log "Vision model: $visionModel" }
Write-Log "Port: $port"

if (-not (Test-Path $modelPath)) {
    Write-Log "ERROR: Model file not found: $modelPath"
    exit 1
}

if ($visionModel -and -not (Test-Path $visionModel)) {
    Write-Log "WARNING: Vision model not found at $visionModel — running text-only"
    $visionModel = $null
}

$llamaServer = Join-Path $scriptDir "llama-server.exe"
if (-not (Test-Path $llamaServer)) {
    Write-Log "ERROR: llama-server.exe not found. Ensure it is in the windows/ directory."
    exit 1
}

Write-Log "Launching inference server..."
$llamaArgs = @(
    "-m", $modelPath,
    "-c", $config.model.contextLength,
    "-t", $config.server.threads,
    "--host", "127.0.0.1"
)

if ($visionModel) {
    $llamaArgs += "--mmproj", $visionModel, "--vision-chunk-size", "512"
}

$llamaArgs += "-p", $port

$process = Start-Process -FilePath $llamaServer `
    -ArgumentList $llamaArgs `
    -NoNewWindow -PassThru

Write-Log "Server started (PID: $($process.Id)) on port $port"
Write-Log "Connect at http://127.0.0.1:$port"

try {
    $process.WaitForExit()
} catch {
    Write-Log "Process ended unexpectedly."
}

Write-Log "LocalLLM service stopped."
