#!/usr/bin/env bash
set -euo pipefail

REMOTE="ssilver@192.168.1.138"
REMOTE_SCRIPT_DIR="~/scripts"
FIFO="/tmp/apk-push-pipe"
SOCK="/tmp/apk-listener-ssh.sock"
ADB="$HOME/Library/Android/sdk/platform-tools/adb"
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)

# ── colors ──────────────────────────────────────────────
GREEN='\033[0;32m'  BLUE='\033[0;34m'   RED='\033[0;31m'
YELLOW='\033[1;33m' BOLD='\033[1m'      DIM='\033[2m'
CYAN='\033[0;36m'   NC='\033[0m'

ts() { echo -ne "${DIM}[$(date +%H:%M:%S)]${NC} "; }

human_size() {
    awk "BEGIN {
        b = $1
        if      (b < 1024)       printf \"%.0f B\",  b
        else if (b < 1048576)    printf \"%.1f KB\", b/1024
        else if (b < 1073741824) printf \"%.1f MB\", b/1048576
        else                     printf \"%.1f GB\", b/1073741824
    }"
}

INSTALL_LOCK="/tmp/apk-install.lock"
BG_PIDS=()

cleanup() {
    printf "\n"
    # wait for in-flight jobs
    for pid in "${BG_PIDS[@]+"${BG_PIDS[@]}"}"; do
        kill -0 "$pid" 2>/dev/null && {
            echo -e "${DIM}Waiting for job $pid to finish...${NC}"
            wait "$pid" 2>/dev/null || true
        }
    done
    echo -e "${DIM}Tearing down SSH tunnel...${NC}"
    ssh -S "$SOCK" -O exit "$REMOTE" 2>/dev/null || true
    rm -f "$INSTALL_LOCK"
    exit 0
}
trap cleanup INT TERM EXIT

# reap finished background jobs so BG_PIDS doesn't grow forever
reap_jobs() {
    local still_running=()
    for pid in "${BG_PIDS[@]+"${BG_PIDS[@]}"}"; do
        if kill -0 "$pid" 2>/dev/null; then
            still_running+=("$pid")
        else
            wait "$pid" 2>/dev/null || true
        fi
    done
    BG_PIDS=("${still_running[@]+"${still_running[@]}"}")
}

ensure_ssh() {
    if ssh -S "$SOCK" -O check "$REMOTE" 2>/dev/null; then
        return
    fi

    # clean up stale socket
    ssh -S "$SOCK" -O exit "$REMOTE" 2>/dev/null || true
    rm -f "$SOCK"

    local delay=1
    while true; do
        ts; echo -e "${BLUE}Connecting to ${REMOTE}...${NC}"
        if ssh -M -S "$SOCK" -fN \
            -o ConnectTimeout=5 \
            -o ServerAliveInterval=15 \
            -o ServerAliveCountMax=3 \
            "$REMOTE" 2>/dev/null; then
            ts; echo -e "${GREEN}✓ Tunnel up${NC}"
            return
        fi

        ts; echo -e "${YELLOW}Connection failed, retrying in ${delay}s...${NC}"
        sleep "$delay"
        delay=$(( delay < 30 ? delay * 2 : 30 ))
    done
}

# ── banner ──────────────────────────────────────────────
echo ""
echo -e "${BOLD}  APK Install Listener${NC}"
echo -e "${DIM}  ─────────────────────────────────────${NC}"
echo -e "  Remote   ${CYAN}${REMOTE}${NC}"
echo -e "  FIFO     ${CYAN}${FIFO}${NC}"
echo ""
echo -e "  ${DIM}On the build machine, run:${NC}"
echo -e "  ${YELLOW}./push-apk.sh path/to/app.apk${NC}"
echo -e "${DIM}  ─────────────────────────────────────${NC}"
echo ""

setup_remote() {
    ssh -S "$SOCK" "$REMOTE" "[ -p '$FIFO' ] || mkfifo '$FIFO'" 2>/dev/null
    ts; echo -e "${GREEN}✓ FIFO ready on remote${NC}"

    # sync push-apk.sh to build machine
    ssh -S "$SOCK" "$REMOTE" "mkdir -p $REMOTE_SCRIPT_DIR" 2>/dev/null
    if rsync -a -e "ssh -S '$SOCK'" \
         "$SCRIPT_DIR/push-apk.sh" "$REMOTE:$REMOTE_SCRIPT_DIR/push-apk.sh" 2>/dev/null; then
        ts; echo -e "${GREEN}✓ push-apk.sh synced to ${REMOTE_SCRIPT_DIR}${NC}"
    else
        ts; echo -e "${YELLOW}⚠ Could not sync push-apk.sh (non-fatal)${NC}"
    fi
}

# ── process one APK (runs in background) ───────────────
process_apk() {
    local apk_path="$1"
    local filename tag size
    filename=$(basename "$apk_path")
    tag="${filename%.apk}"

    # ── pull (parallel) ────────────────────────────────
    ts; echo -e "${BLUE}↓${NC} ${BOLD}[${tag}]${NC} Pulling..."

    if ! rsync -ah --progress -e "ssh -S '$SOCK'" \
         "$REMOTE:$apk_path" "/tmp/$filename" 2>/dev/null; then
        ts; echo -e "${RED}✗${NC} ${BOLD}[${tag}]${NC} Pull failed"
        return 1
    fi

    size=$(stat -f%z "/tmp/$filename" 2>/dev/null || echo 0)
    ts; echo -e "${GREEN}✓${NC} ${BOLD}[${tag}]${NC} Pulled $(human_size "$size")"

    # ── install (serialized via lock) ──────────────────
    ts; echo -e "${BLUE}⏳${NC} ${BOLD}[${tag}]${NC} Installing..."

    # spin until we grab the lock
    while ! shlock -p $$ -f "$INSTALL_LOCK" 2>/dev/null; do
        sleep 0.5
    done

    if "$ADB" install -r "/tmp/$filename" 2>&1 | while IFS= read -r line; do
        echo -e "           ${DIM}[${tag}] ${line}${NC}"
    done; then
        ts; echo -e "${GREEN}✓${NC} ${BOLD}[${tag}]${NC} Installed"
    else
        ts; echo -e "${RED}✗${NC} ${BOLD}[${tag}]${NC} Install failed"
    fi

    rm -f "$INSTALL_LOCK"
}

NEEDS_SETUP=true

# ── main loop ───────────────────────────────────────────
while true; do
    ensure_ssh

    if $NEEDS_SETUP; then
        setup_remote
        NEEDS_SETUP=false
        echo ""
    fi

    reap_jobs
    ts; echo "Waiting for APK..."

    # blocks until the remote push-apk.sh writes a path into the FIFO
    apk_path=$(ssh -S "$SOCK" "$REMOTE" "cat '$FIFO'" 2>/dev/null) || {
        ts; echo -e "${YELLOW}Connection lost, reconnecting...${NC}"
        ssh -S "$SOCK" -O exit "$REMOTE" 2>/dev/null || true
        rm -f "$SOCK"
        NEEDS_SETUP=true
        continue
    }

    apk_path=$(echo "$apk_path" | tr -d '\r\n')
    [[ -z "$apk_path" ]] && continue

    # fork into background — main loop is immediately free for the next push
    process_apk "$apk_path" &
    BG_PIDS+=($!)
done
