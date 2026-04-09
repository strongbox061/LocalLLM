# run-service.ps1 <name> - what each LocalLLM Windows service runs (WinSW starts it; see setup.ps1). Sets the service's
# environment from config.json and secrets\ (read now, so no secret is in the service definition) and runs its
# program in the foreground; WinSW stops the whole process tree.
param([Parameter(Mandatory = $true)][string]$Name)
. (Join-Path $PSScriptRoot 'common.ps1')

$cfg = Read-Config
$paths = Get-Paths $cfg

# Load secrets from the secrets sub-directory for this service name
$secretsDir = Join-Path $paths.Root 'secrets' $Name
if (Test-Path $secretsDir) {
    Get-ChildItem $secretsDir -File | ForEach-Object {
        $key = $_.BaseName.ToUpper()
        $val = [System.IO.File]::ReadAllText($_.FullName).Trim()
        [Environment]::SetEnvironmentVariable($key, $val)
    }
}

# Resolve the program to run (default: ollama serve)
$binDir = $paths.Bin ?: $paths.Root
$exe = Join-Path $binDir 'ollama'
if (-not (Test-Path $exe)) { $exe = Join-Path $binDir 'ollama.exe' }

# Pass through any extra args meant for the service process
$remainingArgs = @($args) | Where-Object { $_ -ne $Name }

Write-Host "Starting LocalLLM service '$Name' from $($paths.Root)"
& $exe @remainingArgs
exit $LASTEXITCODE
