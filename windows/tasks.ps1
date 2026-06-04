# tasks.ps1 <task> [arguments] - the helper PC's jobs that ./localllm.sh on the main PC asks for over SSH, so every
# Windows path stays on this side (config.json says where things are). Not meant to be run by hand.
#   drives                       the fixed drives as JSON (install-helper picks the data drive from them)
#   version                      Open WebUI's version here (empty when it isn't installed)
#   loader                       puts the voice fixes in place (open-webui\install_loader.py; no restart needed)
#   sync                         pushes the repo's Open WebUI tools and functions (open-webui\sync.py), bge-m3 search
#   topology <single|split>      Open WebUI's settings for where it runs (open-webui\topology.py)
#   chat-types [options]         the protected chats' chat types (open-webui\chat_types.py, from chat-types\)
#   chat-types-status            what Open WebUI has of them, as JSON (for the main PC's chat types page)
#   settings-status              the settings this repo sets in Open WebUI, as JSON (tests/system-checks.sh)
#   check <folder>               integrity check and counts of Open WebUI's data in <folder> (JSON)
#   snapshot <folder> [live]     a consistent copy of Open WebUI's and the search gateway's data into <folder>
#                                (live: while they run, for the main PC's backup)
#   install-data <folder>        puts the data in <folder> in place (the previous data kept as ...-before-<date>)
#   relocate                     points Open WebUI's uploads at this PC's uploads folder
#   retire-data                  renames this PC's data to ...-moved-<date>, so it can't be used by mistake
# Plain ASCII on purpose: Windows PowerShell 5.1 reads a script without a byte order mark as the local code page.
param([Parameter(Mandatory = $true, Position = 0)][string]$Task, [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest = @())
. (Join-Path $PSScriptRoot 'common.ps1')

if ($Task -eq 'drives') {
    # Internal (not USB) NTFS volumes with a letter, most free space first.
    $usb = @(Get-Disk | Where-Object BusType -eq 'USB' | ForEach-Object Number)
    $rows = foreach ($p in (Get-Partition | Where-Object DriveLetter)) {
        if ($usb -contains $p.DiskNumber) { continue }
        $vol = Get-Volume -DriveLetter $p.DriveLetter -ErrorAction SilentlyContinue
        if (-not $vol -or $vol.FileSystem -ne 'NTFS' -or $vol.DriveType -ne 'Fixed') { continue }
        [pscustomobject]@{ letter = "$($p.DriveLetter)"; free_gb = [math]::Round($vol.SizeRemaining / 1GB); size_gb = [math]::Round($vol.Size / 1GB) }
    }
    Write-Output ('[' + (($rows | Sort-Object free_gb -Descending | ForEach-Object { $_ | ConvertTo-Json -Compress }) -join ',') + ']')
    exit 0
}

$cfg = Read-Config
$paths = Get-Paths $cfg
Set-ToolEnv $paths
$py = Venv-Python $paths 'open-webui'
$key = Join-Path $SecretsDir 'webui-secret-key'
$db = Join-Path $paths.WebUIData 'webui.db'
$url = 'http://127.0.0.1:3000'

function Py { $a = $args; $out = Native { & $py @a }; $out | ForEach-Object { Write-Host $_ }; return $LASTEXITCODE }

# The data that moves with Open WebUI: its folder (database, uploads, vector store) and the search gateway's
# (state database and settings with the search API keys).
$Moving = @(@{ src = $paths.WebUIData; name = 'open-webui' }, @{ src = $paths.GatewayData; name = 'search-gateway' })

# --- Open WebUI version ---
if ($Task -eq 'version') {
    if (Test-Path $py) { Native { & $py -c "import importlib.metadata as m; print(m.version('open-webui'))" } | Select-Object -Last 1 }
    exit 0
}

# --- Voice loader ---
if ($Task -eq 'loader') {
    $loaderScript = Join-Path $RepoRoot 'open-webui' 'install_loader.py'
    if (Test-Path $loaderScript) {
        Py -Args @($loaderScript)
        exit $LASTEXITCODE
    }
    Write-Error "Loader script not found: $loaderScript"
    exit 1
}

# --- Sync Open WebUI tools ---
if ($Task -eq 'sync') {
    $syncScript = Join-Path $RepoRoot 'open-webui' 'sync.py'
    if (Test-Path $syncScript) {
        Py -Args @($syncScript)
        exit $LASTEXITCODE
    }
    Write-Error "Sync script not found: $syncScript"
    exit 1
}

# --- Topology settings ---
if ($Task -eq 'topology') {
    if ($args.Count -lt 1 -or @('single', 'split') -notcontains $args[0]) {
        Write-Error "Usage: tasks.ps1 topology <single|split>"
        exit 1
    }
    $topoScript = Join-Path $RepoRoot 'open-webui' 'topology.py'
    if (Test-Path $topoScript) {
        Py -Args @($topoScript, $args[0])
        exit $LASTEXITCODE
    }
    Write-Error "Topology script not found: $topoScript"
    exit 1
}

# --- Chat types ---
if ($Task -eq 'chat-types') {
    $ctScript = Join-Path $RepoRoot 'open-webui' 'chat_types.py'
    if (Test-Path $ctScript) {
        Py -Args @($ctScript) + $args
        exit $LASTEXITCODE
    }
    Write-Error "Chat types script not found: $ctScript"
    exit 1
}

# --- Chat types status ---
if ($Task -eq 'chat-types-status') {
    $ctStatusScript = Join-Path $RepoRoot 'open-webui' 'chat_types.py'
    if (Test-Path $ctStatusScript) {
        Py -Args @($ctStatusScript, '--status')
        exit $LASTEXITCODE
    }
    Write-Error "Chat types script not found"
    exit 1
}

# --- Settings status ---
if ($Task -eq 'settings-status') {
    $settingsScript = Join-Path $RepoRoot 'open-webui' 'settings_status.py'
    if (Test-Path $settingsScript) {
        Py -Args @($settingsScript)
        exit $LASTEXITCODE
    }
    Write-Output '{}'
    exit 0
}

# --- Check data integrity ---
if ($Task -eq 'check') {
    if ($args.Count -lt 1) {
        Write-Error "Usage: tasks.ps1 check <folder>"
        exit 1
    }
    $folder = $args[0]
    if (-not (Test-Path $folder)) {
        Write-Output '{"error": "folder not found"}'
        exit 1
    }

    # Inline Python for database checks and file counts
    $pythonCode = @'
import hashlib, json, os, sqlite3, sys

def q(con, sql):
    try:
        return con.execute(sql).fetchall()
    except Exception:
        return []

def db_size(path):
    if not os.path.isfile(path):
        return 0
    return os.path.getsize(path)

def file_count(directory):
    if not os.path.isdir(directory):
        return 0
    return sum(1 for _ in os.walk(directory))

folder = sys.argv[1]
result = {
    "folder": folder,
    "exists": os.path.isdir(folder),
    "files": 0,
    "databases": {},
}

if result["exists"]:
    result["files"] = file_count(folder)
    for entry in os.listdir(folder):
        full = os.path.join(folder, entry)
        if entry.endswith(".db") or entry.endswith(".sqlite"):
            size = db_size(full)
            con = sqlite3.connect(full)
            tables = q(con, "SELECT name FROM sqlite_master WHERE type='table'")
            result["databases"][entry] = {"size": size, "tables": len(tables)}
            con.close()

print(json.dumps(result))
'@

    $result = Py -Args @('-c', $pythonCode, $folder)
    exit $LASTEXITCODE
}

# --- Snapshot data ---
if ($Task -eq 'snapshot') {
    if ($args.Count -lt 1) {
        Write-Error "Usage: tasks.ps1 snapshot <folder> [live]"
        exit 1
    }
    $destFolder = $args[0]
    $isLive     = $args.Count -gt 1 -and $args[1] -eq 'live'

    if (-not (Test-Path $destFolder)) {
        New-Item -ItemType Directory -Force -Path $destFolder | Out-Null
    }

    Write-Host "Snapshotting to $destFolder (live=$isLive)..."
    # Use robocopy for reliable file copying; skip errors on missing source dirs
    robocopy $RepoRoot\open-webui\data $destFolder\data /MIR /R:1 /W:1 /NDL /NFL /NJH /NJS 2>$null
    robocopy $RepoRoot\search-gateway\data $destFolder\search-data /MIR /R:1 /W:1 /NDL /NFL /NJH /NJS 2>$null

    # Record metadata
    $meta = @{
        timestamp = Get-Date -Format 'o'
        live      = $isLive
        repo_root = $RepoRoot
    }
    $meta | ConvertTo-Json | Out-File (Join-Path $destFolder 'snapshot-meta.json') -Encoding utf8

    Write-Host "Snapshot complete."
    exit 0
}

# --- Install data ---
if ($Task -eq 'install-data') {
    if ($args.Count -lt 1) {
        Write-Error "Usage: tasks.ps1 install-data <folder>"
        exit 1
    }
    $sourceFolder = $args[0]
    $dataDest     = Join-Path $RepoRoot 'open-webui' 'data'

    if (-not (Test-Path $sourceFolder)) {
        Write-Error "Source folder not found: $sourceFolder"
        exit 1
    }

    # Back up existing data if it exists
    if (Test-Path $dataDest) {
        $dateStr = Get-Date -Format 'yyyyMMdd-HHmmss'
        $backupName = "$dataDest-before-$dateStr"
        Rename-Item $dataDest $backupName
        Write-Host "Backed up existing data to $backupName"
    }

    Copy-Item -Path "$sourceFolder\*" -Destination $dataDest -Recurse -Force
    Write-Host "Data installed from $sourceFolder"
    exit 0
}

# --- Relocate uploads ---
if ($Task -eq 'relocate') {
    $uploadsDir = Join-Path $RepoRoot 'data' 'uploads'
    if (-not (Test-Path $uploadsDir)) {
        New-Item -ItemType Directory -Force -Path $uploadsDir | Out-Null
    }

    # Update the symlink or config pointing Open WebUI uploads to this location
    $webuiData = Join-Path $RepoRoot 'open-webui' 'data'
    $targetLink = Join-Path $webuiData 'uploads'

    if (Test-Path $targetLink) {
        Remove-Item $targetLink -Force
    }
    New-Item -ItemType SymbolicLink -Path $targetLink -Target $uploadsDir | Out-Null

    Write-Host "Uploads relocated to $uploadsDir"
    exit 0
}

# --- Retire data ---
if ($Task -eq 'retire-data') {
    $dataDest = Join-Path $RepoRoot 'open-webui' 'data'
    if (-not (Test-Path $dataDest)) {
        Write-Host "No data to retire."
        exit 0
    }

    $dateStr = Get-Date -Format 'yyyyMMdd-HHmmss'
    $retiredName = "$dataDest-moved-$dateStr"
    Rename-Item $dataDest $retiredName
    Write-Host "Data retired to $retiredName"
    exit 0
}

# --- Unknown task ---
Write-Error "Unknown task: $Task"
exit 1
