#!/usr/bin/env bash
# The "~/..." strings below are labels for people to read, not paths.
# shellcheck disable=SC2088
# Restorability check (S, no model): is everything this setup runs brought back by the repo's scripts, the settings
# backup or a README step? Read-only on the live machine.
#   1. Inventory: lists what exists now (Pi's folder, ~/.local/bin, user services, Nix profile, Docker containers,
#      system files) and reports any entry no script, backup or README step covers.
#   2. Restore: runs restore.sh with a temporary HOME and checks that every file in backup/ arrives, with the modes
#      restore.sh sets, and that the repo itself clones from backup/repo.bundle.
# Exit status 1 when anything is uncovered or missing. Run it after every section of work.
# Programs of your own that aren't part of this setup (in ~/.local/bin, the Nix profile) go one name per line in
# ~/.config/localllm/restorability-ignore, which stays on this machine.
set -uo pipefail

source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/common.sh"

FAIL=0
ok()   { printf '  ok    %s\n' "$*"; }
bad()  { printf '  FAIL  %s\n' "$*"; FAIL=1; }
note() { printf '  note  %s\n' "$*"; }

# check_dir <label> <listing command output> <covered names...> -- <ignored names...>
# Every name in the listing must be covered (brought back) or ignored (caches, other projects).
check_list() {
    local label="$1" listing="$2"; shift 2
    local -A known=()
    local name kind=covered
    for name in "$@"; do
        if [[ "$name" == "--" ]]; then kind=ignored; continue; fi
        known[$name]=$kind
    done
    local uncovered=()
    while IFS= read -r name; do
        [[ -n "$name" ]] || continue
        [[ -n "${known[$name]:-}" ]] || uncovered+=("$name")
    done <<< "$listing"
    if [[ ${#uncovered[@]} -eq 0 ]]; then ok "$label"; else bad "$label: not covered: ${uncovered[*]}"; fi
}

IGNORE_FILE="$RESTORABILITY_IGNORE_FILE"
LOCAL_IGNORED=()
[[ -f "$IGNORE_FILE" ]] && mapfile -t LOCAL_IGNORED < <(grep -v -E '^\s*(#|$)' "$IGNORE_FILE")

echo "Private files"
# Open WebUI's signing key lets whoever reads it log in as any user; its database holds your chats; backup/ has copies
# of both. backup.sh, restore.sh and services/open-webui.sh keep them yours only (lib/common.sh make_private).
open=()
for f in "$BACKUP_DIR:700" "$WEBUI_DATA_DIR:700" "$WEBUI_KEY_FILE:600" "$WEBUI_DATA_DIR/webui.db:600"; do
    path="${f%:*}" want="${f##*:}"
    [[ -e "$path" ]] || continue
    mode="$(stat -c %a "$path")"
    [[ "$mode" == "$want" ]] || open+=("${path/#"$HOME"/\~} ($mode, should be $want)")
done
if [[ ${#open[@]} -eq 0 ]]; then ok "Open WebUI's signing key, its database and backup/ are yours only"
else bad "others can read: ${open[*]} (run ./backup.sh or restart Open WebUI)"; fi

echo
echo "Inventory"

check_list "~/.pi/agent" "$(find "$PI_AGENT_DIR" -mindepth 1 -maxdepth 1 ! -name '*.bak*' -printf '%f\n' 2>/dev/null)" \
    AGENTS.md extensions skills agents prompts plannotator.json mcp-adapter.json `# install/pi-config.sh, install/pi-mcp.sh` \
    settings.json models.json sandbox.json auth.json models-store.json tool-view-mode.json mcp-onboarding.json \
    mcp-oauth-encrypted `# backup` \
    npm `# install/pi-packages.sh` \
    -- \
    bin context7-ids.json mcp-cache.json mcp-last-seen-version.json pi-ui-extension sessions `# rebuilt or history`

check_list "~/.local/bin" "$(ls "$HOME/.local/bin" 2>/dev/null)" \
    pi agent-browser plannotator open-webui hf tiny-agents ocrmypdf `# ocrmypdf: install/ocr.sh` \
    -- \
    "${LOCAL_IGNORED[@]}"

# ~/.config/localllm: local.env, restorability-ignore and chat-types/ are in the backup; model.json is generated from
# the model profile (install/llama.sh, the llama-server service); the passphrase file (wherever local.env puts it) comes
# back by hand (README, reinstall step 11); hardware.json is a per-PC cache from ./localllm.sh monitor --detect-hw.
passphrase_name=""
[[ -n "$BACKUP_PASSPHRASE_FILE" && "$(dirname -- "$BACKUP_PASSPHRASE_FILE")" == "$(dirname -- "$LOCAL_ENV_FILE")" ]] \
    && passphrase_name="$(basename -- "$BACKUP_PASSPHRASE_FILE")"
browser_installer_name=""
[[ -n "$BROWSER_INSTALLER" && "$(dirname -- "$BROWSER_INSTALLER")" == "$(dirname -- "$LOCAL_ENV_FILE")" ]] \
    && browser_installer_name="$(basename -- "$BROWSER_INSTALLER")"
check_list "~/.config/localllm" "$(ls -A "$(dirname -- "$LOCAL_ENV_FILE")" 2>/dev/null)" \
    local.env restorability-ignore versions.env chat-types model.json search-gateway.json llama-api-key helper.env github-token \
    endpoints.json endpoints.env `# generated from local.env (lib/endpoints.py)` \
    ${passphrase_name:+"$passphrase_name"} ${browser_installer_name:+"$browser_installer_name"} \
    -- hardware.json
# The search gateway's state database (backup.sh) and its Python environment (install/search-gateway.sh).
# (Dated copies the two-PC switch keeps on purpose, *.moved-<date> and *.before-<date>, are not part of the setup.)
check_list "~/.local/share/localllm" "$(find "$(dirname -- "$SEARCH_GATEWAY_DB")" -mindepth 1 -maxdepth 1 -printf '%f\n' 2>/dev/null | grep -Ev '\.(moved|before)-[0-9-]+$')" \
    search-gateway.db search-gateway-venv \
    -- search-gateway.db-wal search-gateway.db-shm

check_list "user services" "$(find "$HOME/.config/systemd/user" -mindepth 1 -maxdepth 1 \( -name '*.service' -o -name '*.timer' \) -printf '%f\n' 2>/dev/null)" \
    open-webui.service search-gateway.service voice-router.service \
    localllm-backup.service localllm-backup.timer localllm-helper-keepalive.service localllm-helper-keepalive.timer \
    -- \
    pipewire-session-manager.service

if command -v nix >/dev/null; then
    check_list "Nix profile" "$(nix profile list 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g' | sed -nE 's/^Name: +//p')" \
        -- \
        "${LOCAL_IGNORED[@]}"
fi

# Containers started from this repo or ~/LocalLLM; others belong to other projects.
containers="$(docker_cmd ps -a --format '{{.Names}} {{.Label "com.docker.compose.project.working_dir"}}' 2>/dev/null \
    | awk -v r="$REPO_DIR" -v l="$LLM_HOME" 'index($2, r) == 1 || index($2, l) == 1 { print $1 }')"
check_list "Docker containers" "$containers" kokoro searxng-core searxng-valkey

system_files=(
    "$HOME/.config/environment.d/60-localllm-path.conf"
    /etc/systemd/system/localllm-llm.service
    /etc/systemd/zram-generator.conf
    /etc/sysctl.d/99-localllm-zram.conf
)
if [[ -n "${BROWSER_POLICY_DIR:-}" ]]; then
    system_files+=(
        "${BROWSER_POLICY_DIR%/}/recommended/localllm-memory.json"
        "${BROWSER_POLICY_DIR%/}/managed/localllm-autoplay.json"
    )
fi
for f in "${system_files[@]}"; do
    if [[ -f "$f" ]]; then ok "$f"; else bad "$f is missing: run the installer that writes it (install.sh)"; fi
done

for f in pi/extensions/*.ts pi/extensions/*/*.ts pi/agents/*.md pi/prompts/*.md pi/AGENTS.md pi/plannotator.json \
         pi/mcp-adapter.json pi/skills/*/SKILL.md; do
    target="$PI_AGENT_DIR/${f#pi/}"
    cmp -s "$REPO_DIR/$f" "$target" || bad "$target differs from the repo: run install/pi-config.sh or install/pi-mcp.sh"
done
ok "Pi's installed files match the repo (if no FAIL above)"

echo
echo "Restore into a temporary HOME"
if [[ ! -d "$BACKUP_DIR" ]] || [[ -z "$(ls -A "$BACKUP_DIR" 2>/dev/null)" ]]; then
    note "no backup/ here, skipped (run ./backup.sh first to test it)"
else
    TMP_HOME="$(mktemp -d)"
    trap 'rm -rf "$TMP_HOME"' EXIT
    if HOME="$TMP_HOME" "$REPO_DIR/restore.sh" >"$TMP_HOME/restore.log" 2>&1; then
        ok "restore.sh ran"
    else
        bad "restore.sh failed: $(tail -3 "$TMP_HOME/restore.log")"
    fi
    # backup path -> restored path and mode (as restore.sh writes them)
    while read -r src dst mode; do
        [[ -e "$BACKUP_DIR/$src" ]] || continue
        if [[ ! -e "$TMP_HOME/$dst" ]]; then
            bad "$src was not restored to ~/$dst"
        elif [[ -n "$mode" && "$(stat -c %a "$TMP_HOME/$dst")" != "$mode" ]]; then
            bad "~/$dst has mode $(stat -c %a "$TMP_HOME/$dst"), expected $mode"
        else
            ok "$src -> ~/$dst"
        fi
    done <<'EOF'
pi/settings.json .pi/agent/settings.json
pi/models.json .pi/agent/models.json 600
pi/sandbox.json .pi/agent/sandbox.json
pi/auth.json .pi/agent/auth.json 600
pi/models-store.json .pi/agent/models-store.json 600
pi/tool-view-mode.json .pi/agent/tool-view-mode.json 600
pi/mcp-onboarding.json .pi/agent/mcp-onboarding.json
pi/mcp-oauth-encrypted .pi/agent/mcp-oauth-encrypted
open-webui/webui.db .open-webui/webui.db
search-gateway/search-gateway.db .local/share/localllm/search-gateway.db 600
open-webui/uploads .open-webui/uploads
open-webui/vector_db .open-webui/vector_db
searxng/docker-compose.yml LocalLLM/searxng/docker-compose.yml
searxng/.env LocalLLM/searxng/.env
searxng/settings.yml LocalLLM/searxng/core-config/settings.yml 644
plannotator/config.json .plannotator/config.json 600
localllm/local.env .config/localllm/local.env 600
localllm/restorability-ignore .config/localllm/restorability-ignore
localllm/chat-types .config/localllm/chat-types
localllm/versions.env .config/localllm/versions.env
localllm/search-gateway.json .config/localllm/search-gateway.json 600
localllm/llama-api-key .config/localllm/llama-api-key 600
localllm/github-token .config/localllm/github-token 600
localllm/helper.env .config/localllm/helper.env 600
localllm/ssh/localllm-helper .ssh/localllm-helper 600
localllm/ssh/localllm-helper.pub .ssh/localllm-helper.pub 644
localllm/ssh/known_hosts .ssh/known_hosts 600
EOF
    # Extra files: each one comes back at its place in the temporary HOME.
    if [[ -d "$BACKUP_DIR/extra" ]]; then
        while IFS= read -r -d '' f; do
            rel="${f#"$BACKUP_DIR/extra/"}"
            if cmp -s "$f" "$TMP_HOME/$rel"; then ok "extra/$rel -> ~/$rel"; else bad "extra/$rel was not restored to ~/$rel"; fi
        done < <(find "$BACKUP_DIR/extra" -type f -print0)
    fi
    # Anything in backup/ that restore.sh doesn't know about would be saved but never put back.
    unknown="$(cd "$BACKUP_DIR" && find . -mindepth 2 -maxdepth 2 ! -path './extra/*' | sed 's|^\./||' | grep -v -x -E \
        'plannotator/config\.json|localllm/(local\.env|restorability-ignore|versions\.env|chat-types|search-gateway\.json|llama-api-key|github-token|helper\.env|ssh)|search-gateway/search-gateway\.db|pi/((settings|models|sandbox|auth|models-store|tool-view-mode|mcp-onboarding)\.json|mcp-oauth-encrypted)|open-webui/(webui\.db|uploads|vector_db|\.webui_secret_key)|searxng/(docker-compose\.yml|\.env|settings\.yml)')"
    if [[ -z "$unknown" ]]; then ok "backup/ holds nothing restore.sh ignores"; else note "in backup/ but never restored: ${unknown//$'\n'/ }"; fi
    # The repo itself: a fresh install clones it from the bundle in the archive (README, "Opening a backup").
    BUNDLE="$BACKUP_DIR/repo.bundle"
    if [[ ! -f "$BUNDLE" ]]; then
        bad "no backup/repo.bundle (the repo itself, for a fresh install): run ./backup.sh"
    elif ! git clone -q --branch master "$BUNDLE" "$TMP_HOME/repo-from-bundle" 2>/dev/null; then
        bad "backup/repo.bundle doesn't clone with master checked out: run ./backup.sh (on a repo with a master branch)"
    else
        # The README's steps: every branch from the bundle, then origin removed.
        CLONE="$TMP_HOME/repo-from-bundle"
        git -C "$CLONE" fetch -q origin '+refs/heads/*:refs/heads/*' --update-head-ok 2>/dev/null
        git -C "$CLONE" remote remove origin
        ok "backup/repo.bundle clones (master at $(git -C "$CLONE" rev-parse --short HEAD))"
        want_branches="$(git bundle list-heads "$BUNDLE" | awk '$2 ~ /^refs\/heads\// { sub("refs/heads/", "", $2); print $2 }' | sort)"
        got_branches="$(git -C "$CLONE" for-each-ref --format='%(refname:strip=2)' refs/heads | sort)"
        if [[ "$want_branches" == "$got_branches" ]]; then
            ok "every branch in the bundle comes back ($(printf '%s\n' "$got_branches" | wc -l))"
        else
            bad "branches missing after the README's clone steps: $(comm -23 <(printf '%s\n' "$want_branches") <(printf '%s\n' "$got_branches") | tr '\n' ' ')"
        fi
        [[ "$(git -C "$CLONE" rev-parse master)" == "$(git -C "$REPO_DIR" rev-parse master)" ]] \
            || note "backup/repo.bundle's master is older than this repo's: the next ./backup.sh (or the timer) brings it up to date"
        [[ "$(git -C "$REPO_DIR" rev-list --count master..HEAD)" -eq 0 ]] \
            || note "this repo's HEAD ($(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD)) has commits master doesn't: a fresh install runs master without them"
    fi
    pending="$(git -C "$REPO_DIR" status --porcelain | wc -l)"
    [[ "$pending" -eq 0 ]] || note "$pending uncommitted or untracked file(s) in the repo (git status): not in the bundle until committed"
fi

echo
if [[ $FAIL -eq 0 ]]; then echo "Restorability: all covered."; else echo "Restorability: gaps found (FAIL above)."; fi
exit $FAIL
