#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="ssilver@192.168.1.138"
ADB="$HOME/Library/Android/sdk/platform-tools/adb"
HISTORY_FILE="$HOME/.apk_installer_history"
MAX_HISTORY=10

touch "$HISTORY_FILE"

add_to_history() {
    local path="$1"
    # Remove duplicates, prepend new entry, keep last N
    grep -vxF "$path" "$HISTORY_FILE" > "$HISTORY_FILE.tmp" 2>/dev/null || true
    { echo "$path"; cat "$HISTORY_FILE.tmp"; } | head -n "$MAX_HISTORY" > "$HISTORY_FILE"
    rm -f "$HISTORY_FILE.tmp"
}

show_menu() {
    local entries=()
    while IFS= read -r line; do
        [[ -n "$line" ]] && entries+=("$line")
    done < "$HISTORY_FILE"

    if [[ ${#entries[@]} -eq 0 ]]; then
        echo "No history yet. Pass a remote APK path as an argument."
        echo "Usage: $0 <remote-apk-path>"
        echo "Example: $0 ~/development/unifi/android-app/app/build/outputs/apk/debug/app-debug.apk"
        exit 1
    fi

    echo "Recent APKs:"
    echo ""
    for i in "${!entries[@]}"; do
        printf "  %d) %s\n" $((i + 1)) "${entries[$i]}"
    done
    echo ""
    printf "Pick one (1-%d): " "${#entries[@]}"
    read -r choice

    if ! [[ "$choice" =~ ^[0-9]+$ ]] || (( choice < 1 || choice > ${#entries[@]} )); then
        echo "Invalid choice."
        exit 1
    fi

    echo "${entries[$((choice - 1))]}"
}

install_apk() {
    local remote_path="$1"
    local filename
    filename=$(basename "$remote_path")
    local local_path="/tmp/$filename"

    echo ""
    echo "Syncing $remote_path ..."
    rsync -ah --progress "$REMOTE_HOST:$remote_path" "$local_path"

    echo ""
    echo "Installing $filename ..."
    "$ADB" install "$local_path"

    add_to_history "$remote_path"
    echo ""
    echo "Done."
}

if [[ $# -ge 1 ]]; then
    remote_path="$1"
else
    remote_path=$(show_menu)
fi

install_apk "$remote_path"
