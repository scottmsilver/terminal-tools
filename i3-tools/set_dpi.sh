#!/bin/bash

# ==========================================
# DPI Switcher for CRD + i3 + WezTerm + Chrome
# ==========================================
# Sets ALL scaling knobs consistently for the given DPI.
# Auto-detects the right DPI from saved resolution→DPI mappings,
# or accepts an explicit DPI value.
#
# Usage:
#   set_dpi.sh                  Auto-detect from current resolution
#   set_dpi.sh <dpi>            Set an exact DPI value (e.g., 96, 120, 138)
#   set_dpi.sh save             Save current resolution→DPI mapping
#   set_dpi.sh diagnose         Print all current DPI/scaling state
#   set_dpi.sh calibrate        Open the visual calibration page
#
# All values are derived from a single DPI number to ensure consistency.
# WezTerm auto-scales by reading Xft.dpi, so it doesn't need manual config.

set -euo pipefail

# ==========================================
# CONFIGURATION — base values at 96 DPI
# ==========================================
XSETTINGS_CFG="$HOME/.config/xsettingsd/xsettingsd.conf"
WEZTERM_MAIN="$HOME/.wezterm.lua"
I3_CONFIG="$HOME/.config/i3/config"
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
DPI_MAP_FILE="$HOME/.config/dpi-profiles.conf"

BASE_DPI=96
BASE_I3_FONT=11
BASE_I3_FLOAT_BORDER=8
BASE_I3_GAPS_INNER=8
BASE_I3_GAPS_OUTER=2

# ==========================================
# FUNCTIONS
# ==========================================

usage() {
    cat <<'EOF'
Usage: set_dpi.sh [<dpi>|<command>]

With no arguments, auto-detects the right DPI from saved
resolution→DPI mappings (see "save" command).

DPI values (examples):
  96        Standard (1080p / 27" QHD desktop)
  120       Comfortable (4K 27" desktop)
  138       Moderate HiDPI
  144       HiDPI (4K 24" / most laptops via CRD)
  192       2x Retina (13" HiDPI)

Commands:
  (none)    Auto-detect DPI from current screen resolution
  save      Save current resolution→DPI as a profile
  list      Show saved resolution→DPI profiles
  diagnose  Print all current DPI/scaling state
  calibrate Open the visual calibration page in Chrome

Examples:
  set_dpi.sh                   # Auto-detect and apply
  set_dpi.sh 138               # Set 138 DPI
  set_dpi.sh save              # Save current res→DPI mapping
  set_dpi.sh diagnose          # Show current state
EOF
    exit 1
}

get_resolution() {
    xrandr --current 2>/dev/null | grep '\*' | awk '{print $1}'
}

get_current_dpi() {
    xrdb -query 2>/dev/null | grep 'Xft.dpi' | awk '{print $2}'
}

# Look up DPI for a resolution in the map file.
# Returns empty string if not found.
lookup_dpi() {
    local res="$1"
    if [ -f "$DPI_MAP_FILE" ]; then
        # Format: resolution=dpi (e.g., 3046x1804=138)
        grep "^${res}=" "$DPI_MAP_FILE" 2>/dev/null | tail -1 | cut -d= -f2
    fi
}

# Find the closest saved resolution (by total pixel count) and return its DPI.
find_closest_dpi() {
    local target_res="$1"
    local target_w target_h target_pixels
    target_w=$(echo "$target_res" | cut -dx -f1)
    target_h=$(echo "$target_res" | cut -dx -f2)
    target_pixels=$((target_w * target_h))

    if [ ! -f "$DPI_MAP_FILE" ] || [ ! -s "$DPI_MAP_FILE" ]; then
        return 1
    fi

    # Read all mappings, find closest by pixel count
    local best_diff=999999999 best_dpi=""
    while IFS='=' read -r res dpi; do
        [ -z "$res" ] || [ -z "$dpi" ] && continue
        [[ "$res" == \#* ]] && continue
        local w h pixels diff
        w=$(echo "$res" | cut -dx -f1)
        h=$(echo "$res" | cut -dx -f2)
        pixels=$((w * h))
        diff=$((target_pixels - pixels))
        [ "$diff" -lt 0 ] && diff=$((-diff))
        if [ "$diff" -lt "$best_diff" ]; then
            best_diff=$diff
            best_dpi=$dpi
        fi
    done < "$DPI_MAP_FILE"

    if [ -n "$best_dpi" ]; then
        # Scale proportionally by the square root of the pixel ratio.
        # More pixels in the same physical screen = higher density = higher DPI.
        # But CRD may also give more pixels because the client window is bigger,
        # which would mean LOWER density. Without knowing physical size, the
        # closest saved profile is the best guess.
        echo "$best_dpi"
        return 0
    fi
    return 1
}

save_profile() {
    local res
    res=$(get_resolution)
    local dpi
    dpi=$(get_current_dpi)

    if [ -z "$res" ] || [ -z "$dpi" ]; then
        echo "Error: Could not detect current resolution or DPI."
        exit 1
    fi

    mkdir -p "$(dirname "$DPI_MAP_FILE")"

    # Remove existing entry for this resolution, if any
    if [ -f "$DPI_MAP_FILE" ]; then
        local tmp
        tmp=$(grep -v "^${res}=" "$DPI_MAP_FILE" 2>/dev/null || true)
        echo "$tmp" > "$DPI_MAP_FILE"
    fi

    echo "${res}=${dpi}" >> "$DPI_MAP_FILE"
    # Clean up blank lines
    sed -i '/^$/d' "$DPI_MAP_FILE"

    echo "Saved: ${res} → ${dpi} DPI"
    echo "Next time CRD connects at ${res}, 'set_dpi.sh' will auto-apply ${dpi} DPI."
}

list_profiles() {
    if [ ! -f "$DPI_MAP_FILE" ] || [ ! -s "$DPI_MAP_FILE" ]; then
        echo "No saved profiles. Run 'set_dpi.sh save' after calibrating."
        return
    fi

    local current_res
    current_res=$(get_resolution)

    echo "Saved resolution → DPI profiles ($DPI_MAP_FILE):"
    echo
    while IFS='=' read -r res dpi; do
        [ -z "$res" ] || [ -z "$dpi" ] && continue
        [[ "$res" == \#* ]] && continue
        local marker=""
        [ "$res" = "$current_res" ] && marker=" ← current"
        printf "  %-16s → %s DPI%s\n" "$res" "$dpi" "$marker"
    done < "$DPI_MAP_FILE"
}

auto_detect() {
    local res
    res=$(get_resolution)
    if [ -z "$res" ]; then
        echo "Error: Could not detect screen resolution."
        exit 1
    fi

    echo "Current resolution: $res"

    # Exact match?
    local dpi
    dpi=$(lookup_dpi "$res" || true)
    if [ -n "$dpi" ]; then
        echo "Found saved profile: ${res} → ${dpi} DPI"
        apply_dpi "$dpi"
        return
    fi

    # Try closest saved resolution
    dpi=$(find_closest_dpi "$res" || true)
    if [ -n "$dpi" ]; then
        echo "No exact match. Closest saved profile suggests ${dpi} DPI."
        echo "Applying ${dpi} DPI (run 'set_dpi.sh save' after calibrating to remember)."
        apply_dpi "$dpi"
        return
    fi

    echo "No saved profiles found for any resolution."
    echo "Run 'set_dpi.sh calibrate' to find your ideal DPI,"
    echo "then 'set_dpi.sh save' to remember it."
    exit 1
}

diagnose() {
    echo "============================================"
    echo "  DPI DIAGNOSTIC REPORT"
    echo "  $(date '+%Y-%m-%d %H:%M:%S')"
    echo "============================================"
    echo

    # Screen resolution
    echo "--- SCREEN (xrandr) ---"
    local res
    res=$(get_resolution)
    local phys
    phys=$(xrandr --current 2>/dev/null | grep ' connected' | grep -oP '\d+mm x \d+mm')
    echo "  Resolution:  $res"
    echo "  Physical:    ${phys:-(unknown/VNC)}"
    echo

    # X11 DPI
    echo "--- X11 DPI ---"
    local xft_dpi
    xft_dpi=$(get_current_dpi)
    local xdpy_dpi
    xdpy_dpi=$(xdpyinfo 2>/dev/null | grep resolution | awk '{print $2}')
    echo "  Xft.dpi (xrdb):        ${xft_dpi:-(not set)}"
    echo "  xdpyinfo resolution:   ${xdpy_dpi:-(unknown)}"
    echo

    # xsettingsd
    echo "--- XSETTINGSD (GTK/Chrome) ---"
    if [ -f "$XSETTINGS_CFG" ]; then
        local xset_raw
        xset_raw=$(awk '/Xft\/DPI/{print $2}' "$XSETTINGS_CFG")
        local xset_dpi=$((xset_raw / 1024))
        echo "  Raw value:    $xset_raw"
        echo "  Effective:    $xset_dpi DPI"
        if [ -n "$xft_dpi" ] && [ "$xft_dpi" != "$xset_dpi" ]; then
            echo "  ⚠ MISMATCH:  Xft.dpi=$xft_dpi but xsettingsd=$xset_dpi"
            echo "                WezTerm and Chrome will scale DIFFERENTLY!"
        else
            echo "  ✓ Consistent with Xft.dpi"
        fi
    else
        echo "  (not configured)"
    fi
    local xset_running
    xset_running=$(pgrep -c xsettingsd 2>/dev/null || echo 0)
    echo "  Daemon:       ${xset_running} process(es)"
    echo

    # Environment variables
    echo "--- ENVIRONMENT ---"
    echo "  DISPLAY=             ${DISPLAY:-(not set)}"
    echo "  GDK_SCALE=           ${GDK_SCALE:-(not set)}"
    echo "  GDK_DPI_SCALE=       ${GDK_DPI_SCALE:-(not set)}"
    echo "  QT_SCALE_FACTOR=     ${QT_SCALE_FACTOR:-(not set)}"
    echo "  QT_AUTO_SCREEN_SCALE_FACTOR= ${QT_AUTO_SCREEN_SCALE_FACTOR:-(not set)}"
    echo

    # i3 config
    echo "--- I3 CONFIG ---"
    if [ -f "$I3_CONFIG" ]; then
        echo "  Font:         $(grep '^font' "$I3_CONFIG")"
        echo "  Float border: $(grep '^default_floating_border' "$I3_CONFIG")"
        echo "  Tiled border: $(grep '^default_border' "$I3_CONFIG")"
        echo "  Inner gaps:   $(grep '^gaps inner' "$I3_CONFIG")"
        echo "  Outer gaps:   $(grep '^gaps outer' "$I3_CONFIG")"
    else
        echo "  (config not found at $I3_CONFIG)"
    fi
    echo

    # WezTerm
    echo "--- WEZTERM ---"
    if [ -f "$WEZTERM_MAIN" ]; then
        local wez_base
        wez_base=$(grep 'BASE_FONT_SIZE' "$WEZTERM_MAIN" | head -1 | grep -oP '[\d.]+')
        local scale
        if [ -n "$xft_dpi" ]; then
            scale=$(echo "scale=2; $xft_dpi / 96" | bc)
            local effective
            effective=$(echo "scale=1; ${wez_base:-11} * $scale" | bc)
            echo "  Base font:    ${wez_base:-?}pt"
            echo "  Scale factor: ${scale}x (from Xft.dpi=$xft_dpi)"
            echo "  Effective:    ${effective}pt"
        else
            echo "  Base font:    ${wez_base:-?}pt"
            echo "  (Cannot compute scale — Xft.dpi not set)"
        fi
    else
        echo "  (config not found at $WEZTERM_MAIN)"
    fi
    echo

    # Saved profiles
    echo "--- SAVED PROFILES ---"
    if [ -f "$DPI_MAP_FILE" ] && [ -s "$DPI_MAP_FILE" ]; then
        while IFS='=' read -r pres pdpi; do
            [ -z "$pres" ] || [ -z "$pdpi" ] && continue
            [[ "$pres" == \#* ]] && continue
            local marker=""
            [ "$pres" = "$res" ] && marker=" ← current"
            echo "  ${pres} → ${pdpi} DPI${marker}"
        done < "$DPI_MAP_FILE"
    else
        echo "  (none — run 'set_dpi.sh save' after calibrating)"
    fi
    echo

    # Computed summary
    echo "--- SCALING CHAIN ---"
    echo "  Your screen → CRD client → Server framebuffer (${res})"
    echo "    ├─ Xft.dpi=${xft_dpi} → WezTerm font = 11 * (${xft_dpi}/96)"
    echo "    ├─ xsettingsd=${xset_dpi:-?} DPI → Chrome/GTK rendering"
    echo "    ├─ i3 font → $(grep '^font' "$I3_CONFIG" 2>/dev/null | grep -oP '\d+$')pt"
    echo "    └─ i3 borders/gaps → separate px values"
    echo
}

open_calibration() {
    local cal_file="$SCRIPT_DIR/calibrate.html"
    if [ ! -f "$cal_file" ]; then
        echo "Error: calibrate.html not found at $cal_file"
        exit 1
    fi
    echo "Opening calibration page..."
    if command -v google-chrome >/dev/null 2>&1; then
        google-chrome "file://$cal_file" &
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "file://$cal_file" &
    else
        echo "Open this file in your browser: file://$cal_file"
    fi
}

apply_dpi() {
    local TARGET_DPI="$1"
    local SCALE
    SCALE=$(echo "scale=4; $TARGET_DPI / $BASE_DPI" | bc)

    # Compute all derived values
    local I3_FONT_SIZE
    I3_FONT_SIZE=$(echo "scale=0; $BASE_I3_FONT * $SCALE / 1" | bc)
    local I3_FLOAT_BORDER
    I3_FLOAT_BORDER=$(echo "scale=0; $BASE_I3_FLOAT_BORDER * $SCALE / 1" | bc)
    local I3_GAPS_INNER
    I3_GAPS_INNER=$(echo "scale=0; $BASE_I3_GAPS_INNER * $SCALE / 1" | bc)
    local I3_GAPS_OUTER
    I3_GAPS_OUTER=$(echo "scale=0; $BASE_I3_GAPS_OUTER * $SCALE / 1" | bc)
    # xsettingsd uses DPI * 1024 — SAME DPI as Xft.dpi for consistency
    local XSET_DPI=$((TARGET_DPI * 1024))

    # Ensure minimums
    [ "$I3_FONT_SIZE" -lt 8 ] && I3_FONT_SIZE=8
    [ "$I3_FLOAT_BORDER" -lt 3 ] && I3_FLOAT_BORDER=3
    [ "$I3_GAPS_INNER" -lt 2 ] && I3_GAPS_INNER=2
    [ "$I3_GAPS_OUTER" -lt 1 ] && I3_GAPS_OUTER=1

    echo "============================================"
    echo "  Setting DPI to $TARGET_DPI (${SCALE}x scale)"
    echo "============================================"
    echo "  Xft.dpi:           $TARGET_DPI"
    echo "  xsettingsd:        $XSET_DPI ($TARGET_DPI DPI)"
    echo "  i3 font:           ${I3_FONT_SIZE}pt"
    echo "  i3 float border:   ${I3_FLOAT_BORDER}px"
    echo "  i3 inner gaps:     ${I3_GAPS_INNER}px"
    echo "  i3 outer gaps:     ${I3_GAPS_OUTER}px"
    echo "  WezTerm font:      $(echo "scale=1; 11 * $SCALE" | bc)pt (auto)"
    echo "============================================"
    echo

    # 1. Update X11 Resources
    echo -n "[1/6] Setting Xft.dpi=$TARGET_DPI... "
    echo "Xft.dpi: $TARGET_DPI" | xrdb -merge
    # Verify
    local MAX_RETRIES=20 count=0
    while true; do
        local CURRENT_DPI
        CURRENT_DPI=$(xrdb -query | grep 'Xft.dpi' | awk '{print $2}')
        if [ "$CURRENT_DPI" = "$TARGET_DPI" ]; then
            echo "done."
            break
        fi
        if [ "$count" -ge "$MAX_RETRIES" ]; then
            echo "timeout (current: $CURRENT_DPI)!"
            break
        fi
        count=$((count + 1))
        sleep 0.1
    done

    # 2. Update i3 config
    echo -n "[2/6] Updating i3 config... "
    if [ -f "$I3_CONFIG" ]; then
        sed -i "s/^font pango:Noto Sans [0-9]*/font pango:Noto Sans $I3_FONT_SIZE/" "$I3_CONFIG"
        sed -i "s/^default_floating_border normal [0-9]*/default_floating_border normal $I3_FLOAT_BORDER/" "$I3_CONFIG"
        sed -i "s/^gaps inner [0-9]*/gaps inner $I3_GAPS_INNER/" "$I3_CONFIG"
        sed -i "s/^gaps outer [0-9]*/gaps outer $I3_GAPS_OUTER/" "$I3_CONFIG"
        echo "done."
    else
        echo "skipped (config not found)."
    fi

    # 3. Update xsettingsd (GTK/Chrome) — SAME DPI as Xft.dpi
    echo -n "[3/6] Updating xsettingsd ($TARGET_DPI DPI)... "
    mkdir -p "$(dirname "$XSETTINGS_CFG")"
    echo "Xft/DPI $XSET_DPI" > "$XSETTINGS_CFG"
    if pgrep xsettingsd > /dev/null; then
        killall -HUP xsettingsd
        echo "done (signaled)."
    else
        if command -v xsettingsd >/dev/null; then
            xsettingsd &
            echo "done (started)."
        else
            echo "done (daemon not available)."
        fi
    fi

    # 4. Trigger WezTerm config reload
    echo -n "[4/6] Triggering WezTerm reload... "
    if [ -f "$WEZTERM_MAIN" ]; then
        touch "$WEZTERM_MAIN"
        echo "done."
    else
        echo "skipped."
    fi

    # 5. Reload i3
    echo -n "[5/6] Restarting i3... "
    i3-msg restart >/dev/null 2>&1 || true
    echo "done."

    # 6. Summary
    echo "[6/6] Verifying..."
    sleep 0.5
    local final_xft
    final_xft=$(xrdb -query 2>/dev/null | grep 'Xft.dpi' | awk '{print $2}')
    local final_xset_raw
    final_xset_raw=$(awk '/Xft\/DPI/{print $2}' "$XSETTINGS_CFG" 2>/dev/null)
    local final_xset_dpi=$((final_xset_raw / 1024))
    echo
    echo "  Xft.dpi:      $final_xft"
    echo "  xsettingsd:   $final_xset_dpi DPI"
    if [ "$final_xft" = "$final_xset_dpi" ]; then
        echo "  ✓ Consistent — WezTerm and Chrome will scale identically."
    else
        echo "  ⚠ MISMATCH — Xft.dpi=$final_xft vs xsettingsd=$final_xset_dpi"
    fi
    echo
    echo "Done. Run 'set_dpi.sh save' to remember this for resolution $(get_resolution)."
}

# ==========================================
# MAIN
# ==========================================

# No arguments → auto-detect
if [ $# -eq 0 ]; then
    auto_detect
    exit 0
fi

case "$1" in
    save)
        save_profile
        ;;
    list|profiles)
        list_profiles
        ;;
    diagnose|diag|status)
        diagnose
        ;;
    calibrate|cal)
        open_calibration
        ;;
    help|-h|--help)
        usage
        ;;
    *)
        # Treat as numeric DPI
        if [[ "$1" =~ ^[0-9]+$ ]] && [ "$1" -ge 48 ] && [ "$1" -le 384 ]; then
            apply_dpi "$1"
        else
            echo "Error: '$1' is not a valid DPI (48-384) or command."
            echo
            usage
        fi
        ;;
esac
