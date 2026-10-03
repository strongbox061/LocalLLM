#!/usr/bin/env bash
# Copies the live settings of Pi (with the encrypted Context7 sign-in), Plannotator, Open WebUI, SearXNG and this
# machine's own LocalLLM settings (~/.config/localllm, plus EXTRA_BACKUP_FILES) into backup/ (kept out of git: it holds
# secrets), with this repo itself as backup/repo.bundle, and records the versions running now in
# ~/.config/localllm/versions.env (README, "Versions").
# Safe while Open WebUI runs: its databases are copied as SQLite snapshots.
# archive-backup.sh and upload-backup.sh call it before packing backup/ into an encrypted archive.
set -euo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/lib/common.sh"

PI_FILES=(
    settings.json
    models.json
    sandbox.json
    auth.json
    models-store.json
    tool-view-mode.json
    mcp-onboarding.json
)

copy_file() {   # copy_file <source> <destination>
    if [[ -f "$1" ]]; then
        mkdir -p "$(dirname -- "$2")"
        cp "$1" "$2"
        echo "  saved   ${2#"$REPO_DIR"/}"
    else
        echo "  missing $1"
    fi
}

copy_dir() {    # copy_dir <source> <destination>, replacing the destination
    if [[ -d "$1" ]]; then
        rm -rf "$2"
        mkdir -p "$(dirname -- "$2")"
        cp -r "$1" "$2"
        echo "  saved   ${2#"$REPO_DIR"/}/"
    else
        echo "  missing $1"
    fi
}

# snapshot_db <source .db> <destination>: a consistent copy of a SQLite database, safe while the app is running.
# .backup includes what is still in the WAL file; the copy is switched to a single file (no -wal/-shm next to it).
snapshot_db() {
    local src="$1" dst="$2" tmp="$2.tmp"
    if [[ ! -f "$src" ]]; then
        echo "  missing $src"
        return 0
    fi
    mkdir -p "$(dirname -- "$dst")"
    rm -f "$tmp"
    sqlite3 "$src" ".backup '$tmp'"
    [[ "$(sqlite3 "$tmp" 'pragma integrity_check;')" == "ok" ]] || die "The snapshot of $src is corrupt: $tmp"
    sqlite3 "$tmp" 'pragma journal_mode=delete;' >/dev/null
    rm -f "$dst" "$dst-wal" "$dst-shm"
    mv "$tmp" "$dst"
    echo "  saved   ${dst#"$REPO_DIR"/}"
}

log "Pi ($PI_AGENT_DIR)"
for f in "${PI_FILES[@]}"; do
    copy_file "$PI_AGENT_DIR/$f" "$BACKUP_DIR/pi/$f"
done
# The encrypted Context7 sign-in (its key is in local.env, saved below).
copy_dir "$PI_AGENT_DIR/mcp-oauth-encrypted" "$BACKUP_DIR/pi/mcp-oauth-encrypted"

# write_versions <file>: the versions running now, so a fresh install from this backup gets the same ones
# (lib/common.sh reads them; the installers use them only for what they install new).
write_versions() {
    local out="$1" v img
    {
        echo "# Versions running at the last backup ($(date +%F)). Written by backup.sh; don't edit."
        v="$(npm ls -g --depth=0 --json 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin).get("dependencies",{}); print(d.get("@earendil-works/pi-coding-agent",{}).get("version",""))')"
        [[ -n "$v" ]] && echo "PI_VERSION=\"$v\""
        v="$(npm ls -g --depth=0 --json 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin).get("dependencies",{}); print(d.get("agent-browser",{}).get("version",""))')"
        [[ -n "$v" ]] && echo "AGENT_BROWSER_VERSION=\"$v\""
        v="$(python3 - "$PI_AGENT_DIR/npm" <<'PY'
import json, os, sys
root = sys.argv[1]
try:
    deps = json.load(open(os.path.join(root, "package.json"))).get("dependencies", {})
except OSError:
    deps = {}
out = []
for name in sorted(deps):
    try:
        out.append(name + "@" + json.load(open(os.path.join(root, "node_modules", name, "package.json")))["version"])
    except (OSError, KeyError):
        pass
print(" ".join(out))
PY
)"
        [[ -n "$v" ]] && echo "PI_PACKAGE_VERSIONS=\"$v\""
        v="$(uv tool list 2>/dev/null | sed -nE 's/^open-webui v([0-9][^ ]*).*/\1/p')"
        [[ -n "$v" ]] && echo "OPEN_WEBUI_VERSION=\"$v\""
        v="$(git -C "$LLM_HOME/llama.cpp" rev-parse HEAD 2>/dev/null)"
        [[ -n "$v" ]] && echo "LLAMA_CPP_COMMIT=\"$v\""
        for pair in SEARXNG_IMAGE:searxng-core VALKEY_IMAGE:searxng-valkey; do
            img="$(docker_cmd inspect --format '{{.Image}}' "${pair#*:}" 2>/dev/null)" || continue
            v="$(docker_cmd image inspect --format '{{index .RepoDigests 0}}' "$img" 2>/dev/null)" || continue
            [[ -n "$v" ]] && echo "${pair%%:*}=\"$v\""
        done
    } > "$out.tmp"
    # A value that couldn't be read now (e.g. Docker needs sudo in the background) keeps the one recorded before.
    if [[ -f "$out" ]]; then
        local kept="" line
        while IFS= read -r line; do
            grep -q "^${line%%=*}=" "$out.tmp" || kept+="$line"$'\n'
        done < <(grep -E '^[A-Z_]+=' "$out")
        printf '%s' "$kept" >> "$out.tmp"
    fi
    mv "$out.tmp" "$out"
}

log "This kit's own settings (~/.config/localllm; the backup passphrase stays out: it opens the archive)"
mkdir -p "$(dirname -- "$VERSIONS_FILE")"
write_versions "$VERSIONS_FILE"
copy_file "$VERSIONS_FILE" "$BACKUP_DIR/localllm/versions.env"
copy_file "$LOCAL_ENV_FILE" "$BACKUP_DIR/localllm/local.env"
copy_file "$RESTORABILITY_IGNORE_FILE" "$BACKUP_DIR/localllm/restorability-ignore"
# The search gateway's settings: the search services' API keys, their order and limits.
copy_file "$SEARCH_GATEWAY_SETTINGS" "$BACKUP_DIR/localllm/search-gateway.json"
# The GitHub token for Pi's issue searches, when there is one.
if [[ -f "$GITHUB_TOKEN_FILE" ]]; then copy_file "$GITHUB_TOKEN_FILE" "$BACKUP_DIR/localllm/github-token"; else rm -f "$BACKUP_DIR/localllm/github-token"; fi
# The two-PC setup (README, "Two-PC setup"), when it's set up: llama-server's API key, the helper PC's own values,
# and the SSH key to the helper with the helper's fingerprint (its lines in ~/.ssh/known_hosts).
for f in "$LLAMA_API_KEY_FILE:llama-api-key" "$HELPER_ENV_FILE:helper.env" \
         "$HELPER_SSH_KEY:ssh/$(basename -- "$HELPER_SSH_KEY")" "$HELPER_SSH_KEY.pub:ssh/$(basename -- "$HELPER_SSH_KEY").pub"; do
    if [[ -f "${f%%:*}" ]]; then copy_file "${f%%:*}" "$BACKUP_DIR/localllm/${f#*:}"; else rm -f "$BACKUP_DIR/localllm/${f#*:}"; fi
done
rm -f "$BACKUP_DIR/localllm/ssh/known_hosts"
if [[ -n "${HELPER_HOST:-}" && -f "$HOME/.ssh/known_hosts" ]]; then
    hosts="$(ssh-keygen -F "$HELPER_HOST" -f "$HOME/.ssh/known_hosts" 2>/dev/null | grep -v '^#' || true)"
    if [[ -n "$hosts" ]]; then
        mkdir -p "$BACKUP_DIR/localllm/ssh"
        printf '%s\n' "$hosts" > "$BACKUP_DIR/localllm/ssh/known_hosts"
        echo "  saved   backup/localllm/ssh/known_hosts (the helper PC's fingerprint)"
    fi
fi
rmdir "$BACKUP_DIR/localllm/ssh" 2>/dev/null || true
# The protected chats' chat types (install/chat-types.sh), when there are any.
if [[ -d "$CHAT_TYPES_DIR" ]]; then
    copy_dir "$CHAT_TYPES_DIR" "$BACKUP_DIR/localllm/chat-types"
else
    rm -rf "$BACKUP_DIR/localllm/chat-types"
fi
# Your own extra files/folders (EXTRA_BACKUP_FILES in local.env) and unpacked browser extensions
# (AGENT_BROWSER_EXTENSIONS), kept under backup/extra/ by their path in your home folder.
rm -rf "$BACKUP_DIR/extra"
for f in "${EXTRA_BACKUP_FILES[@]}"; do
    f="${f/#\~/$HOME}"
    f="${f%/}"
    case "$f" in
        "$HOME"/*)
            if [[ -d "$f" ]]; then
                copy_dir "$f" "$BACKUP_DIR/extra/${f#"$HOME"/}"
            else
                copy_file "$f" "$BACKUP_DIR/extra/${f#"$HOME"/}"
            fi
            ;;
        *) warn "EXTRA_BACKUP_FILES: $f is outside your home folder, skipped." ;;
    esac
done
for ext in "${AGENT_BROWSER_EXTENSIONS[@]}"; do
    ext="${ext/#\~/$HOME}"
    ext="${ext%/}"
    [[ -n "$ext" ]] || continue
    case "$ext" in
        "$HOME"/*) copy_dir "$ext" "$BACKUP_DIR/extra/${ext#"$HOME"/}" ;;
        *) warn "AGENT_BROWSER_EXTENSIONS: $ext is outside your home folder, skipped." ;;
    esac
done

log "Plannotator (its settings; the Inbox's threads stay out)"
copy_file "$PLANNOTATOR_CONFIG" "$BACKUP_DIR/plannotator/config.json"

if [[ "${WHERE_SEARCH_GATEWAY-main}" == helper || "${WHERE_OPEN_WEBUI-main}" == helper ]]; then
    # Split mode: Open WebUI and the search gateway, with their data, are on the helper PC. A live snapshot there
    # (consistent databases), fetched over SSH into the same places in backup/ as in single mode.
    log "Open WebUI and the search gateway (on the helper PC, $HELPER_HOST)"
    source "$REPO_DIR/lib/helper.sh"
    load_helper_env >/dev/null
    snap="$HELPER_DATA_DIR\\backup-snapshot"
    fetched="$(mktemp -d)"
    chmod 700 "$fetched"
    if helper_reachable && helper_task snapshot "$snap" live >/dev/null \
        && helper_ssh "tar --format=pax -cf - -C '$snap' open-webui search-gateway" | tar -xf - -C "$fetched"; then
        helper_ssh "Remove-Item -Recurse -Force '$snap'" >/dev/null 2>&1 || true
        for f in webui.db uploads vector_db; do
            rm -rf "${BACKUP_DIR:?}/open-webui/$f"
            [[ -e "$fetched/open-webui/$f" ]] && cp -r "$fetched/open-webui/$f" "$BACKUP_DIR/open-webui/$f" && echo "  saved   backup/open-webui/$f"
        done
        [[ -f "$fetched/search-gateway/search-gateway.db" ]] \
            && cp "$fetched/search-gateway/search-gateway.db" "$BACKUP_DIR/search-gateway/search-gateway.db" \
            && echo "  saved   backup/search-gateway/search-gateway.db"
        [[ -f "$fetched/search-gateway/search-gateway.json" ]] \
            && cp "$fetched/search-gateway/search-gateway.json" "$BACKUP_DIR/localllm/search-gateway.json" \
            && echo "  saved   backup/localllm/search-gateway.json (the helper's search settings)"
    else
        warn "The helper PC can't be reached: Open WebUI's data isn't in this backup (backup/ keeps the copy from the last backup that reached it)."
    fi
    rm -rf "$fetched"
else
    log "Search gateway ($SEARCH_GATEWAY_DB)"
    # Its usage counts (what each free allowance has left), result cache and search log (no search words unless its
    # log_search_words setting is on).
    snapshot_db "$SEARCH_GATEWAY_DB" "$BACKUP_DIR/search-gateway/search-gateway.db"

    log "Open WebUI ($WEBUI_DATA_DIR)"
    snapshot_db "$WEBUI_DATA_DIR/webui.db" "$BACKUP_DIR/open-webui/webui.db"
    # Kept here before the search gateway had its own database (and before its rename): now in search-gateway/.
    rm -f -- "$BACKUP_DIR"/open-webui/{search_gateway,web_research}_{usage.json,log.jsonl,log.jsonl.1}
    copy_dir "$WEBUI_DATA_DIR/uploads" "$BACKUP_DIR/open-webui/uploads"

    # Vector store: chroma.sqlite3 as a snapshot; Chroma's index folders (one per collection, once there are any)
    # as plain files. Those are written by Chroma directly, so a copy taken while Open WebUI runs can catch them
    # mid-write.
    VDB_SRC="$WEBUI_DATA_DIR/vector_db"
    VDB_DST="$BACKUP_DIR/open-webui/vector_db"
    if [[ -d "$VDB_SRC" ]]; then
        rm -rf "$VDB_DST"
        snapshot_db "$VDB_SRC/chroma.sqlite3" "$VDB_DST/chroma.sqlite3"
        indexes=0
        for item in "$VDB_SRC"/*; do
            [[ -e "$item" ]] || continue
            case "$(basename -- "$item")" in chroma.sqlite3 | chroma.sqlite3-wal | chroma.sqlite3-shm) continue ;; esac
            cp -r "$item" "$VDB_DST/"
            [[ -d "$item" ]] && indexes=$((indexes + 1))
        done
        [[ $indexes -eq 0 ]] || echo "  saved   ${VDB_DST#"$REPO_DIR"/}/ ($indexes index folders)"
        if [[ $indexes -gt 0 ]] && webui_running; then
            warn "Open WebUI is running: the vector index folders may be copied mid-write. Run ./stop.sh first for a clean copy."
        fi
    else
        echo "  missing $VDB_SRC"
    fi
    [[ -f "$BACKUP_DIR/open-webui/.webui_secret_key" ]] || warn "No backup/open-webui/.webui_secret_key yet (services/open-webui.sh creates it)."
fi

log "SearXNG ($SEARXNG_DIR)"
copy_file "$SEARXNG_DIR/core-config/settings.yml" "$BACKUP_DIR/searxng/settings.yml"
copy_file "$SEARXNG_DIR/.env" "$BACKUP_DIR/searxng/.env"
copy_file "$SEARXNG_DIR/docker-compose.yml" "$BACKUP_DIR/searxng/docker-compose.yml"

log "This repo (every branch and commit, as a git bundle: a fresh install clones it from the archive)"
# Commits only: uncommitted changes and untracked files that aren't in EXTRA_BACKUP_FILES don't come back.
git -C "$REPO_DIR" bundle create "$BACKUP_DIR/repo.bundle.part" --all 2>/dev/null \
    || die "git bundle failed for $REPO_DIR."
mv "$BACKUP_DIR/repo.bundle.part" "$BACKUP_DIR/repo.bundle"
echo "  saved   backup/repo.bundle ($(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD) at $(git -C "$REPO_DIR" rev-parse --short HEAD))"
if [[ "$REPO_DIR" == "$HOME"/* ]]; then
    printf '%s\n' "${REPO_DIR#"$HOME"/}" > "$BACKUP_DIR/repo-path"
    echo "  saved   backup/repo-path"
fi
pending="$(git -C "$REPO_DIR" status --porcelain | wc -l)"
[[ "$pending" -eq 0 ]] \
    || warn "$pending uncommitted or untracked file(s) in the repo (git status) aren't in backup/repo.bundle: commit them, or list them in EXTRA_BACKUP_FILES."
# A fresh install checks out master from the bundle (README, "Opening a backup"): work on another branch is in the
# bundle, but not in what gets installed.
branch="$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD)"
if ! git -C "$REPO_DIR" rev-parse --verify --quiet refs/heads/master >/dev/null; then
    warn "The repo has no master branch: a fresh install clones master from backup/repo.bundle (README, \"Opening a backup\")."
elif [[ "$branch" != master ]]; then
    ahead="$(git -C "$REPO_DIR" rev-list --count master..HEAD)"
    [[ "$ahead" -eq 0 ]] \
        || warn "You are on $branch, $ahead commit(s) ahead of master: a fresh install from this backup runs master, without them. Merge them into master when they are ready."
fi

make_private  # backup/ only yours (it holds Open WebUI's signing key and your chats)

echo
echo "backup/ is not in git. To keep it off this machine: ./upload-backup.sh (encrypted archive to your rclone remote),"
echo "or ./archive-backup.sh to only make the encrypted archive."
