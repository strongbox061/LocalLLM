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

switch ($Task) {
    'version' {
        if (Test-Path $py) { Native { & $py -c "import importlib.metadata as m; print(m.version('open-webui'))" } | Select-Object -Last 1 }
        exit 0
    }
    'loader' { exit (Py '-I' (Join-Path $RepoDir 'open-webui\install_loader.py')) }
    'sync' {
        $env:LOCALLLM_EMBEDDING = 'bge-m3'
        exit (Py (Join-Path $RepoDir 'open-webui\sync.py') $url $db $key (Join-Path $LocalLLM 'model.json'))
    }
    'topology' {
        $mode = $Rest[0]
        $names = @($cfg.services | ForEach-Object name)
        $v = $cfg.versions
        $settings = @{ llama_url = $cfg.llama_url; llama_key = (Read-Secret 'llama-api-key'); whisper_model = $v.whisper_model
            reranking_model = $v.reranking_model
            whisper_url = $(if ($names -contains 'whisper') { 'http://127.0.0.1:8882' } else { '' })
            docling_url = $(if ($names -contains 'docling') { 'http://127.0.0.1:5001' } else { '' })
            small_model = $(if ($names -contains 'small-model') { @{ url = 'http://127.0.0.1:8083'; alias = $v.small_model.alias } } else { $null })
            comfyui = $(if ($names -contains 'comfyui') { @{ url = 'http://127.0.0.1:8188'; checkpoint = $v.comfyui_checkpoint.file; embeddings = @($v.comfyui_embeddings | ForEach-Object { [IO.Path]::GetFileNameWithoutExtension($_.file) }) } } else { $null }) }
        $f = Join-Path $SecretsDir 'topology.json'
        Set-Content -Path $f -Value ($settings | ConvertTo-Json -Depth 5) -Encoding ASCII
        try { $code = Py (Join-Path $RepoDir 'open-webui\topology.py') $mode $url $db $key $f } finally { Remove-Item -Force $f }
        exit $code
    }
    'chat-types' {
        $args2 = @((Join-Path $RepoDir 'open-webui\chat_types.py'), $url, $db, $key, (Join-Path $LocalLLM 'model.json'), (Join-Path $LocalLLM 'chat-types')) + $Rest
        exit (Py @args2)
    }
    'chat-types-status' {
        # Read-only: the presets, Knowledge bases and accounts (services\chat_types_web.py db_status, the same code
        # the page uses in single mode).
        $code = 'import json, sys; sys.path.insert(0, sys.argv[1]); import chat_types_web as w; print(json.dumps(w.db_status(sys.argv[2])))'
        Native { & $py -I -c $code (Join-Path $RepoDir 'services') $db }
        exit $LASTEXITCODE
    }
    'settings-status' {
        # Read-only: embedding model, hybrid search, the chat type presets' tools (open-webui\settings_status.py).
        Native { & $py -I (Join-Path $RepoDir 'open-webui\settings_status.py') $db }
        exit $LASTEXITCODE
    }
    'relocate' {
        # Open WebUI's uploads listed with this PC's folder (install-data does it; on its own for a fix by hand).
        exit (Py (Join-Path $RepoDir 'open-webui\relocate_uploads.py') $db (Join-Path $paths.WebUIData 'uploads'))
    }
    'check' {
        # Read-only: integrity and what's in it, to compare with the other PC's numbers.
        $dir = $Rest[0]
        $code = @'
import hashlib, json, os, sqlite3, sys
d = sys.argv[1]
out = {}
def q(con, sql):
    try: return con.execute(sql).fetchone()[0]
    except sqlite3.Error: return None
for name, rel in (("webui", "open-webui/webui.db"), ("chroma", "open-webui/vector_db/chroma.sqlite3"), ("gateway", "search-gateway/search-gateway.db")):
    p = os.path.join(d, *rel.split("/"))
    if not os.path.exists(p): out[name] = None; continue
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    out[name] = {"integrity": q(con, "pragma integrity_check")}
    if name == "webui":
        for t in ("chat", "user", "file", "knowledge", "model"): out[name][t] = q(con, f"select count(*) from {t}")
        out[name]["newest_chat"] = q(con, "select max(updated_at) from chat")
    con.close()
up = os.path.join(d, "open-webui", "uploads")
names = sorted(os.path.relpath(os.path.join(r, f), up).replace(os.sep, "/") for r, _, fs in os.walk(up) for f in fs) if os.path.isdir(up) else []
out["uploads"] = len(names)
out["upload_names"] = hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest()[:16]
print(json.dumps(out, sort_keys=True))
'@
        $tmp = Join-Path $StateDir 'check.py'
        Set-Content -Path $tmp -Value $code -Encoding ASCII
        try { Native { & $py -I $tmp $dir } } finally { Remove-Item -Force $tmp }
        exit 0
    }
    'snapshot' {
        # Open WebUI and the gateway must be stopped (./localllm.sh switch stops them first): plain copies are then
        # consistent; the databases still go through SQLite's backup, so a -wal file never splits them.
        # "live" (the main PC's backup.sh): while they run; the databases are still consistent (SQLite's backup),
        # the vector index folders may be caught mid-write, as on the main PC.
        $dir = $Rest[0]
        if ($Rest -notcontains 'live') {
            foreach ($n in 'localllm-open-webui', 'localllm-search-gateway') {
                $s = Get-Service $n -ErrorAction SilentlyContinue
                if ($s -and $s.Status -ne 'Stopped') { Fail "$n is running: stop it first (./localllm.sh stop helper)." }
            }
        }
        Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue
        foreach ($m in $Moving) {
            $dst = Join-Path $dir $m.name
            New-Item -ItemType Directory -Force -Path $dst | Out-Null
            if (Test-Path $m.src) { Native { robocopy.exe $m.src $dst /E /NFL /NDL /NJH /NJS /NP /XF *-wal *-shm *.db *.sqlite3 /XD cache } | Out-Null }
            $code = 'import sqlite3, sys, pathlib' + "`n" + 'for src in pathlib.Path(sys.argv[1]).rglob("*"):' + "`n" +
                    '    if src.suffix in (".db", ".sqlite3") and src.is_file():' + "`n" +
                    '        dst = pathlib.Path(sys.argv[2]) / src.relative_to(sys.argv[1]); dst.parent.mkdir(parents=True, exist_ok=True)' + "`n" +
                    '        a = sqlite3.connect(str(src)); b = sqlite3.connect(str(dst)); a.backup(b); b.execute("pragma journal_mode=delete"); b.close(); a.close()'
            $tmp = Join-Path $StateDir 'snap.py'
            Set-Content -Path $tmp -Value $code -Encoding ASCII
            try { if (Test-Path $m.src) { Native { & $py -I $tmp $m.src $dst } | Out-Null } } finally { Remove-Item -Force $tmp }
        }
        Info "Snapshot in $dir"
        exit 0
    }
    'install-data' {
        # <folder> holds open-webui\ and search-gateway\ (from the main PC); each replaces this PC's, whose old copy
        # is kept next to it as <name>-before-<date>. Open WebUI and the gateway must be stopped.
        $dir = $Rest[0]
        foreach ($n in 'localllm-open-webui', 'localllm-search-gateway') {
            $s = Get-Service $n -ErrorAction SilentlyContinue
            if ($s -and $s.Status -ne 'Stopped') { Fail "$n is running: stop it first." }
        }
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        foreach ($m in $Moving) {
            $new = Join-Path $dir $m.name
            if (-not (Test-Path $new)) { Fail "No $new in the copy." }
            if ((Test-Path $m.src) -and (Get-ChildItem $m.src -Force | Select-Object -First 1)) {
                Rename-Item -Path $m.src -NewName "$(Split-Path -Leaf $m.src)-before-$stamp"
                Info "Kept the previous $($m.name) data as $(Split-Path -Leaf $m.src)-before-$stamp"
            } elseif (Test-Path $m.src) { Remove-Item -Force -Recurse $m.src }
            Move-Item -Path $new -Destination $m.src
        }
        Remove-Item -Force -Recurse $dir  # empty now: both folders moved out of it
        Set-AdminOnly $paths.Data
        # Open WebUI's uploads are listed with their full path on the other PC: point them here.
        $code = Py (Join-Path $RepoDir 'open-webui\relocate_uploads.py') $db (Join-Path $paths.WebUIData 'uploads')
        if ($code -ne 0) { Fail "The uploads' paths couldn't be set (see above)." }
        Info "Open WebUI's and the search gateway's data are in place."
        exit 0
    }
    'retire-data' {
        $stamp = Get-Date -Format 'yyyyMMdd'
        foreach ($m in $Moving) {
            if (Test-Path $m.src) {
                $to = "$(Split-Path -Leaf $m.src)-moved-$stamp"
                if (Test-Path (Join-Path (Split-Path $m.src) $to)) { $to = "$to-$(Get-Date -Format HHmmss)" }
                Rename-Item -Path $m.src -NewName $to
                Info "This PC's $($m.name) data is now $to (moved to the main PC)."
            }
        }
        exit 0
    }
}
Fail "Unknown task: $Task"
