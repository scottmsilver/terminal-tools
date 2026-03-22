# Polybar Migration Design

Replace i3bar + i3status + i3status-wrapper.py with polybar for scrolling workspace tabs, better styling, and DPI-aware rendering.

## Problem

On narrow CRD displays, i3bar's workspace tabs overflow and get clipped — there's no scroll, truncation, or overflow handling. Making fonts smaller doesn't help because they become unreadable. Polybar's i3 module supports mouse-scroll cycling through workspaces and better per-label control.

## Architecture

```
Before:  i3 config bar {} → i3status → i3status-wrapper.py → show-keybindings.sh
After:   i3 config exec_always → launch.sh → polybar (config.ini) → show-keybindings.sh
```

Polybar's built-in modules replace i3status entirely. i3status will no longer run (it was only launched as i3bar's child process). The only external script is show-keybindings.sh (click action from the Keys button).

## Bar Layout

```
[workspaces ◄►scroll]                    [CPU] [Load] [Mem] [Disk] [Date] [? Keys]
```

- **Left:** `internal/i3` module. Active workspace gets underline + bright text. Mouse scroll cycles workspaces. Workspace labels show `N:name` format. When workspaces exceed available width, scroll reveals hidden ones.
- **Right:** Built-in `cpu`, `memory`, `filesystem`, `date` modules plus a `load` script module. Pipe separators. Dracula-themed colors.
- **Far right:** Custom `keys` module — clickable text that launches `show-keybindings.sh`.

## Polybar Config

### File: `~/.config/polybar/config.ini`

```ini
[colors]
background = #282a36
foreground = #f8f8f2
primary = #bd93f9
green = #50fa7b
red = #ff5555
yellow = #f1fa8c
cyan = #8be9fd
active-bg = #44475a
urgent = #ff5555

[bar/main]
width = 100%
height = 32
background = ${colors.background}
foreground = ${colors.foreground}
font-0 = Noto Sans:size=11;3
padding-right = 2
module-margin = 1
modules-left = i3
modules-right = cpu load memory filesystem date keys
separator = |
separator-foreground = #6272a4
cursor-click = pointer
enable-ipc = true
wm-restack = i3
override-redirect = true
tray-position = right

[module/i3]
type = internal/i3
pin-workspaces = true
enable-scroll = true
wrapping-scroll = false
label-focused = %name%
label-focused-foreground = ${colors.foreground}
label-focused-background = ${colors.active-bg}
label-focused-underline = ${colors.primary}
label-focused-padding = 2
label-unfocused = %name%
label-unfocused-foreground = #6272a4
label-unfocused-padding = 2
label-urgent = %name%
label-urgent-foreground = ${colors.urgent}
label-urgent-underline = ${colors.urgent}
label-urgent-padding = 2

[module/cpu]
type = internal/cpu
interval = 5
format-prefix = "CPU "
format-prefix-foreground = ${colors.cyan}
label = %percentage%%

[module/load]
type = custom/script
exec = awk '{printf "%.1f", $1}' /proc/loadavg
interval = 5
format-prefix = "Load "
format-prefix-foreground = ${colors.cyan}

[module/memory]
type = internal/memory
interval = 5
format-prefix = "Mem "
format-prefix-foreground = ${colors.cyan}
label = %used%

[module/filesystem]
type = internal/fs
mount-0 = /
interval = 60
format-mounted-prefix = "Disk "
format-mounted-prefix-foreground = ${colors.cyan}
label-mounted = %percentage_used%%

[module/date]
type = internal/date
interval = 5
date = %a %b %d
time = %I:%M %p
label = %date%  %time%
label-foreground = ${colors.green}

[module/keys]
type = custom/text
content = ? Keys
content-foreground = ${colors.primary}
click-left = ~/scripts/show-keybindings.sh &
```

### File: `~/.config/polybar/launch.sh`

```bash
#!/bin/bash
# Guard: do nothing if polybar is not installed
command -v polybar >/dev/null || { echo "polybar not found" >&2; exit 1; }

# Kill existing polybar instances
killall -q polybar
# Wait for them to shut down
while pgrep -u $UID -x polybar >/dev/null; do sleep 0.2; done
# Launch
polybar main 2>&1 | tee -a /tmp/polybar.log &
disown
```

## DPI Integration

`set_dpi.sh` already updates i3 font, xsettingsd, and triggers WezTerm reload. Add polybar font/height update as a new step before the i3 restart (which re-launches polybar via `exec_always`). No separate `polybar-msg cmd restart` is needed since `i3-msg restart` triggers `exec_always launch.sh`.

In `apply_dpi()`, after the xsettingsd step and before the WezTerm step, add:

1. Compute polybar font size: `POLYBAR_FONT_SIZE = round(BASE_POLYBAR_FONT * scale)` where `BASE_POLYBAR_FONT=11` (at 96 DPI)
2. Compute font vertical offset: `POLYBAR_FONT_OFFSET = round(3 * scale)`
3. Compute bar height: `POLYBAR_HEIGHT = round(32 * scale)`
4. `sed -i` the `font-0` line (size and offset), and `height` in `~/.config/polybar/config.ini`
5. Skip this step silently if the config file doesn't exist

The step count in `apply_dpi()` increases from 6 to 7.

### Base values at 96 DPI

| Setting | Base (96 DPI) | At 138 DPI |
|---------|--------------|------------|
| Font size | 11 | 16 |
| Font offset | 3 | 4 |
| Bar height | 32 | 46 |

### Sed patterns

```bash
sed -i "s/^font-0 = Noto Sans:size=[0-9]*;[0-9]*/font-0 = Noto Sans:size=${POLYBAR_FONT_SIZE};${POLYBAR_FONT_OFFSET}/" "$POLYBAR_CFG"
sed -i "s/^height = [0-9]*/height = $POLYBAR_HEIGHT/" "$POLYBAR_CFG"
```

## i3 Config Changes

Back up first: `cp ~/.config/i3/config ~/.config/i3/config.bak`

Remove:
```
bar {
        status_command /home/ssilver/scripts/i3status-wrapper.py
}
```

Add:
```
exec_always --no-startup-id ~/.config/polybar/launch.sh
```

## Theme: Dracula

Consistent with the existing i3 window colors and i3status config's Dracula palette:

| Element | Color | Hex |
|---------|-------|-----|
| Background | Dark grey | `#282a36` |
| Foreground | White | `#f8f8f2` |
| Active workspace bg | Lighter grey | `#44475a` |
| Active underline | Purple | `#bd93f9` |
| Inactive workspace | Comment | `#6272a4` |
| Urgent workspace | Red | `#ff5555` |
| Status prefixes | Cyan | `#8be9fd` |
| Date/time | Green | `#50fa7b` |
| Keys button | Purple | `#bd93f9` |

## Installation Steps

1. `sudo apt install polybar` (available in Ubuntu universe repo, version 3.7.1)
2. `cp ~/.config/i3/config ~/.config/i3/config.bak`
3. Write `~/.config/polybar/config.ini` and `~/.config/polybar/launch.sh`
4. Update `~/.config/i3/config` — remove `bar {}`, add `exec_always` for polybar
5. Update `set_dpi.sh` — add polybar font/height update step
6. Run `set_dpi.sh 138` to apply current DPI to polybar config
7. Restart i3 (`$mod+Shift+r`)

## Behavior changes from i3bar

- **Filesystem:** Now shows `%percentage_used%` (same as i3status had)
- **Load average:** Preserved via custom script module reading `/proc/loadavg`
- **Memory:** Shows used memory in human-readable format (polybar's `%used%`)
- **i3status:** No longer runs — polybar has its own modules

## What Gets Retired

- `i3status-wrapper.py` — no longer needed; polybar has native modules + click actions
- `bar {}` section in i3 config — replaced by `exec_always` polybar launch

## Rollback

If polybar doesn't work out:
1. `killall polybar`
2. `cp ~/.config/i3/config.bak ~/.config/i3/config`
3. Remove polybar sed block from `set_dpi.sh` (or it will harmlessly try to sed a config that exists but isn't used)
4. `i3-msg restart`
