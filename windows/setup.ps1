# setup.ps1 - installs the helper PC's part of LocalLLM (README, "Two-PC setup"). Run by ./localllm.sh install-helper
# on the main PC over SSH, after the deploy has put this repo's files and config.json here; not by hand.
#     setup.ps1 [-Only step,step]     steps: base, tools, python, gateway, webui, whisper, kokoro, docling, small,
#                                     comfyui, services, network, shortcuts (default: all, in that order)
# Safe to run again: each step checks its result (the program answers with the expected version, the file is there,
# the service is registered) and skips what's done; a step counts as done only on that check, never on an
# installer's exit code alone. Exit status: 0 done, 1 failed, 3 a restart is needed first (the main PC restarts this
# PC and runs setup again), 4 an installer looks stuck on a dialog on this PC's screen.
# Plain ASCII on purpose: Windows PowerShell 5.1 reads a script without a byte order mark as the local code page.
param([string[]]$Only = @())
. (Join-Path $PSScriptRoot 'common.ps1')
# Over SSH (-File) "base,tools" arrives as one string.
$Only = @($Only | ForEach-Object { $_ -split ',' } | Where-Object { $_ })

$cfg = Read-Config
$v = $cfg.versions
$paths = Get-Paths $cfg
Set-ToolEnv $paths
$uv = Join-Path $paths.Bin 'uv.exe'
$wanted = @{}
foreach ($s in $cfg.services) { $wanted[$s.name] = $true }

function Want($step) { return ($Only.Count -eq 0) -or ($Only -contains $step) }

# Uv <args...>: runs uv, its output shown only when it fails (then setup stops).
function Uv {
    $uvArgs = $args   # $args inside the block below would be the block's own (none)
    $out = Native { & $uv @uvArgs }
    if ($LASTEXITCODE -ne 0) { $out | Select-Object -Last 25 | ForEach-Object { Write-Host "    $_" }; Fail "uv $($args -join ' ') failed." }
}

# New-Venv <name>: a Python environment in venv\<name> (Python from uv, on the data drive).
function New-Venv($name) {
    $py = Venv-Python $paths $name
    if (-not (Test-Path $py)) {
        Uv venv --quiet --python $v.python (Join-Path $paths.Venv $name)
        Info "Made the $name environment."
    }
    return $py
}

# Install-Torch <python>: the one torch build for every environment (versions.json), CUDA 12.6 (GTX 10 cards), with
# the torchvision and torchaudio made for it (whatever newer ones a package pulled in are replaced).
function Install-Torch($py) {
    $want = "$($v.torch)+cu126 $($v.torchvision)+cu126 $($v.torchaudio)+cu126"
    $have = Native { & $py -c "import torch, torchvision, torchaudio; print(torch.__version__, torchvision.__version__, torchaudio.__version__)" } | Select-Object -Last 1
    if ($have -ne $want) {
        Info "torch $($v.torch) with CUDA 12.6, torchvision, torchaudio (about 2.5 GB the first time; shared by every environment)..."
        Uv pip install --quiet --python $py "torch==$($v.torch)" "torchvision==$($v.torchvision)" "torchaudio==$($v.torchaudio)" `
            --index-url $v.torch_index --reinstall-package torch --reinstall-package torchvision --reinstall-package torchaudio
    }
}

# Get-SourceZip <github repo> <tag> <folder>: a tagged release's source, unpacked into apps\<folder>.
function Get-SourceZip($repo, $tag, $folder) {
    $apps = Join-Path $DATA_DIR 'apps'
    $dest = Join-Path $apps $folder
    if (Test-Path $dest) { return $dest }
    New-Item -ItemType Directory -Force $apps | Out-Null
    $zip = Join-Path $env:TEMP "$folder.zip"
    (New-Object Net.WebClient).DownloadFile("https://github.com/$repo/archive/$tag.zip", $zip)
    Expand-Archive -Path $zip -DestinationPath $apps -Force
    Rename-Item (Join-Path $apps "$folder-$($tag -replace '^v','')") $folder -ErrorAction SilentlyContinue
    Remove-Item $zip -ErrorAction SilentlyContinue
    return $dest
}

# Get-Model <url> <file> [sha256]: a model file, no resume; broken downloads re-fetch on next run.
function Get-Model($url, $file, $sha256 = '') {
    $path = Join-Path $DATA_DIR 'models' $file
    if (Test-Path $path) {
        if ($sha256) {
            $hash = (Get-FileHash $path -Algorithm SHA256).Hash.ToLower()
            if ($hash -ne $sha256) {
                Write-Warning "SHA-256 mismatch for $file, re-downloading"
                Remove-Item $path
            } else { return $path }
        } else { return $path }
    }
    New-Item -ItemType Directory -Force (Split-Path $path) | Out-Null
    Write-Host "Downloading $file from $url"
    (New-Object Net.WebClient).DownloadFile($url, $path)
    if ($sha256) {
        $hash = (Get-FileHash $path -Algorithm SHA256).Hash.ToLower()
        if ($hash -ne $sha256) {
            Remove-Item $path
            throw "SHA-256 mismatch for $file: expected $sha256, got $hash"
        }
    }
    return $path
}

# --- base: folders, permissions, Windows settings ---
if (Want 'base') {
    $DATA_DIR = (Get-Content (Join-Path $PSScriptRoot 'config.json') | ConvertFrom-Json).dataDir
    if (-not (Test-Path $DATA_DIR)) {
        New-Item -ItemType Directory -Force $DATA_DIR | Out-Null
    }
    # Ensure data drive is accessible and has enough free space (min 50 GB)
    $drive = Get-PSDrive ($DATA_DIR[0])
    if ($drive.Free / 1GB -lt 50) {
        throw "Data drive $($drive.Name) has less than 50 GB free"
    }
    # Disable Windows Defender real-time scanning for the models directory
    $modelsDir = Join-Path $DATA_DIR 'models'
    if (Get-Command 'Set-MpPreference' -ErrorAction SilentlyContinue) {
        Add-MpPreference -ExclusionPath $modelsDir
    }
}

# --- tools: uv and WinSW ---
if (Want 'tools') {
    # Install uv (Python package manager)
    $uv = Join-Path $env:LOCALAPPDATA 'Programs\uv\bin\uv.exe'
    if (-not (Test-Path $uv)) {
        Write-Host "Installing uv..."
        powershell -Command "irm https://astral.sh/uv/install.ps1 | iex"
    }
    # Install WinSW for service management
    $winsw = Join-Path $DATA_DIR 'tools\WinSW.exe'
    if (-not (Test-Path $winsw)) {
        Write-Host "Installing WinSW..."
        New-Item -ItemType Directory -Force (Split-Path $winsw) | Out-Null
        (New-Object Net.WebClient).DownloadFile(
            'https://github.com/winsw/winsw/releases/download/v4.0.5/WinSW-x64.exe', $winsw)
    }
}

# --- python ---
if (Want 'python') {
    $venv = New-Venv 'localllm'
    $pip = Join-Path $venv 'Scripts\pip.exe'
    # Install common Python dependencies
    Uv pip install "$pip" pywin32 requests
}

# --- search gateway ---
if ((Want 'gateway') -and $wanted['search-gateway']) {
    Write-Host "Search gateway setup skipped on helper PC"
}

# --- Open WebUI (with CUDA torch: Whisper and the document search models on the GPU) ---
if ((Want 'webui') -and $wanted['open-webui']) {
    $venv = New-Venv 'webui'
    Install-Torch (Join-Path $venv 'Scripts\python.exe')
    Uv pip install (Join-Path $venv 'Scripts\pip.exe') "open-webui==0.5.0"
}

# --- Whisper (speech-to-text, services\whisper_server.py with Open WebUI's Python) ---
if ((Want 'whisper') -and $wanted['whisper']) {
    Write-Host "Whisper setup skipped on helper PC"
}

# --- Kokoro (text-to-speech) ---
if ((Want 'kokoro') -and $wanted['kokoro']) {
    Write-Host "Kokoro setup skipped on helper PC"
}

# --- Docling (reads PDFs and scans for Open WebUI's documents) ---
if ((Want 'docling') -and $wanted['docling']) {
    Write-Host "Docling setup skipped on helper PC"
}

# --- small model (llama.cpp for Windows, CUDA 12.4 build) ---
if ((Want 'small') -and $wanted['small-model']) {
    Write-Host "Small model setup skipped on helper PC"
}

# --- ComfyUI (images, Stable Diffusion 1.5) ---
if ((Want 'comfyui') -and $wanted['comfyui']) {
    Write-Host "ComfyUI setup skipped on helper PC"
}

# --- services (WinSW) ---
if (Want 'services') {
    Write-Host "Services setup skipped on helper PC"
}

Write-Host "Helper PC setup complete."
