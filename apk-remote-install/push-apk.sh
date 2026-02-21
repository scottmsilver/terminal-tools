#!/usr/bin/env bash
set -euo pipefail

FIFO="/tmp/apk-push-pipe"
SELF_URL="https://raw.githubusercontent.com/scottmsilver/agent-tools/main/apk-remote-install/push-apk.sh"
SCRIPT_PATH=$(realpath "$0" 2>/dev/null || readlink -f "$0" 2>/dev/null || echo "$0")

# ── colors ──────────────────────────────────────────────
GREEN='\033[0;32m'  RED='\033[0;31m'  BLUE='\033[0;34m'
BOLD='\033[1m'      DIM='\033[2m'     CYAN='\033[0;36m'
NC='\033[0m'

human_size() {
    awk "BEGIN {
        b = $1
        if      (b < 1024)       printf \"%.0f B\",  b
        else if (b < 1048576)    printf \"%.1f KB\", b/1024
        else if (b < 1073741824) printf \"%.1f MB\", b/1048576
        else                     printf \"%.1f GB\", b/1073741824
    }"
}

# ── self-update ─────────────────────────────────────────
self_update() {
    local tmp
    tmp=$(mktemp)
    if curl -sf --connect-timeout 3 "$SELF_URL" -o "$tmp"; then
        if ! cmp -s "$tmp" "$SCRIPT_PATH"; then
            mv "$tmp" "$SCRIPT_PATH"
            chmod +x "$SCRIPT_PATH"
            echo -e "  ${GREEN}✓${NC} Updated to latest version, re-running..."
            echo ""
            exec "$SCRIPT_PATH" "--skip-update" "$@"
        fi
        rm -f "$tmp"
    fi
}

if [[ "${1:-}" != "--skip-update" ]]; then
    self_update "$@"
else
    shift  # drop the --skip-update flag
fi

# ── usage ───────────────────────────────────────────────
if [[ $# -lt 1 ]]; then
    echo -e "${BOLD}Usage:${NC} $0 <apk-path>"
    echo ""
    echo "  Push an APK to the Mac for install."
    echo "  Make sure apk-listener.sh is running on the Mac first."
    echo ""
    echo "  Example:"
    echo "    $0 ./app/build/outputs/apk/debug/app-debug.apk"
    exit 1
fi

# resolve to absolute path portably
APK_PATH=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")

if [[ ! -f "$APK_PATH" ]]; then
    echo -e "${RED}Error:${NC} File not found: $1"
    exit 1
fi

if [[ ! -p "$FIFO" ]]; then
    echo -e "${RED}Error:${NC} FIFO not found at ${FIFO}"
    echo "  Is apk-listener.sh running on the Mac?"
    exit 1
fi

FILENAME=$(basename "$APK_PATH")
SIZE=$(stat -c%s "$APK_PATH" 2>/dev/null || stat -f%z "$APK_PATH")

echo ""
echo -e "  ${BLUE}⏳${NC} Pushing ${BOLD}${FILENAME}${NC} ($(human_size "$SIZE"))..."
echo -e "  ${DIM}(waiting for Mac listener to pick up...)${NC}"

# Writing to the FIFO blocks until the Mac listener reads it —
# so when this returns, the Mac has the path and is pulling.
echo "$APK_PATH" > "$FIFO"

echo -e "  ${GREEN}✓${NC} Mac is pulling and installing."
echo -e "  ${DIM}Watch the Mac terminal for progress.${NC}"
echo ""
