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

# Get-SourceZip <github repo> <tag> <folder>: a tagged release's source, unpacked into apps\<folder> (kept files:
# models and outputs inside it stay when the version changes).
function Get-SourceZip($repo, $tag, $folder) {
    $dest = Join-Path $paths.Apps $folder
    $mark = Join-Path $dest '.localllm-version'
    if ((Test-Path $mark) -and ((Get-Content $mark -Raw).Trim() -eq $tag)) { return $dest }
    $zip = Join-Path $paths.Cache "$folder-$tag.zip"
    if (-not (Test-Path $zip)) { Info "Downloading $repo $tag..."; Download-File "https://github.com/$repo/archive/refs/tags/$tag.zip" $zip }
    $tmp = Join-Path $paths.Cache "$folder-unpack"
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    Expand-Archive -Path $zip -DestinationPath $tmp
    $inner = Get-ChildItem $tmp | Select-Object -First 1
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Native { robocopy.exe $inner.FullName $dest /E /NFL /NDL /NJH /NJS /NP } | Out-Null
    Remove-Item -Recurse -Force $tmp
    Set-Content -Path $mark -Value $tag -Encoding ASCII
    Info "$repo $tag in $dest"
    return $dest
}

# Get-Model <url> <file> [sha256]: a model file (resumes nothing; a broken download is fetched again on the next run).
# With a SHA-256, the file is checked once after the download (a mismatch is deleted and setup stops).
function Get-Model($url, $file, $sha256 = '') {
    if (Test-Path $file) { return }
    Info "Downloading $(Split-Path -Leaf $file)..."
    Download-File $url $file
    if ($sha256) {
        $got = (Get-FileHash $file -Algorithm SHA256).Hash.ToLower()
        if ($got -ne $sha256.ToLower()) {
            Remove-Item -Force $file
            Fail "$(Split-Path -Leaf $file) has SHA-256 $got, not the pinned $sha256 (windows/versions.json): deleted."
        }
        Info "SHA-256 checked."
    }
}

# --- base: folders, permissions, Windows settings ---
if (Want 'base') {
    Step "Folders and Windows settings"
    foreach ($d in @($LocalLLM, $SecretsDir, $StateDir, $paths.Data, $paths.Bin, $paths.Cache, $paths.Venv, $paths.Apps,
                     $paths.Models, $paths.WebUIData, $paths.GatewayData, $paths.Logs, $paths.Services)) {
        New-Item -ItemType Directory -Force -Path $d | Out-Null
    }
    # Open WebUI's database (your chats), its signing key and the API keys: Administrators and SYSTEM only.
    Set-AdminOnly $LocalLLM
    Set-AdminOnly $paths.Data
    Info "Data: $($paths.Data); settings: $LocalLLM (both Administrators and SYSTEM only)"
    # Python packages have deep paths: allow paths over 260 characters.
    Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name LongPathsEnabled -Value 1 -Type DWord
    # The repo's scripts may run (the shortcuts); scripts from the internet still need a signature.
    # (Windows reports an "overridden" error when this session runs with Bypass; the machine's setting is still made.)
    try { Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope LocalMachine -Force } catch { }
    if ((Get-ExecutionPolicy -Scope LocalMachine) -ne 'RemoteSigned') { Fail "Couldn't set the execution policy to RemoteSigned." }
    # Windows Defender: don't scan the models and environments on every read (much faster starts).
    try {
        $have = @((Get-MpPreference).ExclusionPath)
        if ($have -notcontains $paths.Data) { Add-MpPreference -ExclusionPath $paths.Data; Info "Defender: $($paths.Data) excluded from scans." }
    } catch { Warn "Couldn't set Defender's exclusion (another antivirus?): $($_.Exception.Message)" }
    # Power: the Balanced plan, no sleep on mains power, closing the lid does nothing (the helper works lid-closed).
    # The plan that was active before is kept for gaming mode (gaming.ps1 on switches to it).
    $planFile = Join-Path $StateDir 'power-plan-before.txt'
    $active = ((Native { powercfg.exe /getactivescheme }) -join ' ') -replace '.*([0-9a-f]{8}-[0-9a-f-]{27}).*', '$1'
    $balanced = '381b4222-f694-41f0-9685-ff5bb260df2e'
    if ($active -ne $balanced -and -not (Test-Path $planFile)) { Set-Content -Path $planFile -Value $active -Encoding ASCII }
    Native { powercfg.exe /setactive $balanced } | Out-Null
    Native { powercfg.exe /change standby-timeout-ac 0 } | Out-Null
    Native { powercfg.exe /change hibernate-timeout-ac 0 } | Out-Null
    Native { powercfg.exe /setacvalueindex $balanced SUB_BUTTONS LIDACTION 0 } | Out-Null
    Native { powercfg.exe /setactive $balanced } | Out-Null
    Info "Power: Balanced, no sleep on mains power, lid close does nothing."
    # Microsoft's Visual C++ runtime (2015-2022, x64): PyTorch and llama-server.exe need its DLLs, and a fresh Windows
    # may not have it (their error is only "a DLL can't be loaded"). Installed when the registry doesn't list it or
    # vcruntime140_1.dll (2019 and newer) is missing; Microsoft's link always gives the newest, which replaces older ones.
    $vcKey = 'HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\X64'
    function Test-VcRuntime {
        $r = Get-ItemProperty -Path $vcKey -ErrorAction SilentlyContinue
        return ($r -and $r.Installed -eq 1 -and (Test-Path (Join-Path $env:SystemRoot 'System32\vcruntime140_1.dll')))
    }
    if (-not (Test-VcRuntime)) {
        $vcExe = Join-Path $paths.Cache 'vc_redist.x64.exe'
        Remove-Item $vcExe -ErrorAction SilentlyContinue   # always the newest
        Download-File 'https://aka.ms/vs/17/release/vc_redist.x64.exe' $vcExe
        $code = Run-Installer 'Visual C++ runtime' $vcExe '/install /quiet /norestart' 15
        # 0 done, 1638 a newer one is already there, 3010 done but Windows wants a restart.
        if (-not (Test-VcRuntime)) { Fail "The Visual C++ runtime didn't install (exit $code)." }
        if ($code -eq 3010) { Info "Visual C++ runtime installed; Windows wants a restart."; Write-Host "RESTART NEEDED: the Visual C++ runtime asked for a restart."; exit 3 }
    }
    Info "Visual C++ runtime: $((Get-ItemProperty -Path $vcKey).Version)"
    if (Test-PendingRestart){ Write-Host "RESTART NEEDED: Windows has changes waiting for a restart."; exit 3 }
}

# --- tools: uv and WinSW ---
if (Want 'tools') {
    Step "uv and WinSW"
    $have = if (Test-Path $uv) { ((Native { & $uv --version }) -join ' ') } else { '' }
    if ($have -notmatch "^uv $([regex]::Escape($v.uv))\b") {
        $zip = Join-Path $paths.Cache "uv-$($v.uv).zip"
        Download-File "https://github.com/astral-sh/uv/releases/download/$($v.uv)/uv-x86_64-pc-windows-msvc.zip" $zip
        Expand-Archive -Path $zip -DestinationPath $paths.Bin -Force
    }
    Info ((Native { & $uv --version }) -join ' ')
    $winsw = Join-Path $paths.Bin "WinSW-$($v.winsw).exe"
    if (-not (Test-Path $winsw)) { Download-File "https://github.com/winsw/winsw/releases/download/$($v.winsw)/WinSW-x64.exe" $winsw }
    Info "WinSW $($v.winsw)"
}

# --- python ---
if (Want 'python') {
    Step "Python $($v.python)"
    Uv python install --quiet $v.python
    Info ((Native { & $uv python find $v.python }) -join ' ')
}

# --- search gateway ---
if ((Want 'gateway') -and $wanted['search-gateway']) {
    Step "Search gateway"
    $py = New-Venv 'search-gateway'
    Uv pip install --quiet --python $py -r (Join-Path $RepoDir 'services\search-gateway-requirements.txt')
    $out = Native { & $py -m playwright install chromium }
    if ($LASTEXITCODE -ne 0) { $out | Select-Object -Last 10 | ForEach-Object { Write-Host "    $_" }; Fail "Playwright's Chromium didn't install." }
    $ok = Native { & $py -c "import aiohttp, pydantic, playwright; print('ok')" } | Select-Object -Last 1
    if ($ok -ne 'ok') { Fail "The search gateway's packages don't import." }
    Info "Ready ($py)."
}

# --- Open WebUI (with CUDA torch: Whisper and the document search models on the GPU) ---
if ((Want 'webui') -and $wanted['open-webui']) {
    Step "Open WebUI $($cfg.open_webui_version)"
    $py = New-Venv 'open-webui'
    $have = Native { & $py -c "import importlib.metadata as m; print(m.version('open-webui'))" } | Select-Object -Last 1
    if ($have -ne $cfg.open_webui_version) {
        Info "Installing Open WebUI $($cfg.open_webui_version) (about 3 GB, several minutes)..."
        Uv pip install --quiet --python $py "open-webui==$($cfg.open_webui_version)" playwright
    }
    # The voice router (services/voice_router.py) runs with this Python too.
    $ld = Native { & $py -c "import langdetect; print('ok')" } | Select-Object -Last 1
    if ($ld -ne 'ok') { Uv pip install --quiet --python $py langdetect }
    Install-Torch $py
    $out = Native { & $py -m playwright install chromium }
    if ($LASTEXITCODE -ne 0) { Fail "Playwright's Chromium didn't install for Open WebUI." }
    $check = Native { & $py -c "import importlib.metadata as m, torch; print(m.version('open-webui'), torch.cuda.is_available())" } | Select-Object -Last 1
    if ($check -ne "$($cfg.open_webui_version) True") { Fail "Open WebUI's environment isn't right (got '$check'; want version and CUDA True)." }
    Info "Open WebUI $($cfg.open_webui_version), CUDA available."
}

# --- Whisper (speech-to-text, services\whisper_server.py with Open WebUI's Python) ---
if ((Want 'whisper') -and $wanted['whisper']) {
    Step "Whisper $($v.whisper_model)"
    $py = Venv-Python $paths 'open-webui'
    if (-not (Test-Path $py)) { Fail "Whisper runs with Open WebUI's Python: run setup with the webui step first." }
    $dir = Join-Path $paths.Models 'whisper'
    # Open WebUI's own copy (from before this service), when it has the model: copied, not downloaded again.
    $old = Join-Path $paths.WebUIData 'cache\whisper\models'
    $repo = "models--*--faster-whisper-$($v.whisper_model)"
    if (-not (Get-ChildItem $dir -Filter $repo -ErrorAction SilentlyContinue) -and (Get-ChildItem $old -Filter $repo -ErrorAction SilentlyContinue)) {
        Info "Copying the model from Open WebUI's cache..."
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        Copy-Item -Recurse -Force (Join-Path $old '*') $dir
    }
    # Downloads what's missing (nothing when it's all there).
    # (No double quotes in the code: Windows PowerShell 5.1 drops them on the way to a program's arguments.)
    $code = "import sys, faster_whisper as f; p = f.download_model(sys.argv[1], cache_dir=sys.argv[2]); print('ok', p)"
    $out = Native { & $py -c $code $v.whisper_model $dir }
    if (-not ($out | Where-Object { $_ -like 'ok *' })) { $out | Select-Object -Last 10 | ForEach-Object { Write-Host "    $_" }; Fail "Whisper's model didn't download." }
    Info "Whisper ready (model in $dir)."
}

# --- Kokoro (text-to-speech) ---
if ((Want 'kokoro') -and $wanted['kokoro']) {
    Step "Kokoro $($v.kokoro_fastapi)"
    $root = Get-SourceZip 'remsky/Kokoro-FastAPI' $v.kokoro_fastapi 'kokoro-fastapi'
    $py = New-Venv 'kokoro'
    $have = Native { & $py -c "import importlib.metadata as m; print(m.version('kokoro-fastapi'))" } | Select-Object -Last 1
    if ($have -ne $v.kokoro_fastapi.TrimStart('v')) {
        Info "Installing Kokoro's packages (several minutes)..."
        Push-Location $root
        try { Uv pip install --quiet --python $py -e ".[gpu]" } finally { Pop-Location }
    }
    Install-Torch $py
    $model = Join-Path $root 'api\src\models\v1_0'
    if (-not (Get-ChildItem $model -Filter *.pth -ErrorAction SilentlyContinue)) {
        Info "Downloading Kokoro's model..."
        Push-Location $root
        try { $out = Native { & $py docker\scripts\download_model.py --output api\src\models\v1_0 } } finally { Pop-Location }
        if (-not (Get-ChildItem $model -Filter *.pth -ErrorAction SilentlyContinue)) { $out | Select-Object -Last 10 | ForEach-Object { Write-Host "    $_" }; Fail "Kokoro's model didn't download." }
    }
    Info "Kokoro ready (model in $model)."
}

# --- Docling (reads PDFs and scans for Open WebUI's documents) ---
if ((Want 'docling') -and $wanted['docling']) {
    Write-Host "Docling setup skipped on helper PC"
}

# --- small model (llama.cpp for Windows, CUDA 12.4 build) ---
if ((Want 'small') -and $wanted['small-model']) {
    Step "Small model (llama.cpp)"
    $cpp = Join-Path $paths.Bin 'llama-server.exe'
    if (Test-Path $cpp) {
        $have = (Native { & $cpp --version }) -join ' '
        if ($have) { Info "Small model: llama.cpp ready."; continue }
    }
    $zip = Join-Path $paths.Cache "llama-cuda124.zip"
    Download-File 'https://github.com/ggerganov/llama.cpp/releases/latest/download/llama-bin-win-cuda-x64.zip' $zip
    Expand-Archive -Path $zip -DestinationPath $paths.Cache\llama-tmp -Force
    Copy-Item (Join-Path $paths.Cache\llama-tmp 'llama-server.exe') $cpp -Force
    Remove-Item -Recurse -Force (Join-Path $paths.Cache 'llama-tmp')
    Info "Small model: llama.cpp ready."
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
