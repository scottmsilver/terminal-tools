# Installing i3-tools

Instructions for setting up i3-tools on a fresh machine running i3wm + WezTerm, typically accessed via Chrome Remote Desktop.

## Prerequisites

Install these system packages:

- `i3` (i3wm with gaps support)
- `polybar` (status bar — requires version 3.7+)
- `wezterm` (terminal emulator)
- `xsettingsd` (GTK/Chrome DPI propagation)
- `imagemagick` (screenshots for workspace namer)
- `rofi` (app launcher, referenced in i3 config)
- `dunst` (notifications)
- `parcellite` (clipboard manager)
- `xdotool` (window manipulation)
- `bc` (arithmetic in set_dpi.sh)
- `python3` with `i3ipc` and `requests` (workspace namer)
- `python3-tk` (tkinter — required by `show-keybindings.py` for the polybar `?` cheat-sheet popup; the anaconda `python3` has it but the system one at `/usr/bin/python3` that polybar's `PATH` hits does not)

On Ubuntu/Debian:
```
sudo apt install i3 polybar wezterm xsettingsd imagemagick rofi dunst parcellite xdotool bc python3-pip python3-tk
pip install i3ipc requests
```

For the workspace namer's AI features, install the Gemini CLI and authenticate it once interactively:
```
npm install -g @google/gemini-cli    # or follow https://geminicli.com/
gemini                                # first run prompts for auth; credentials are cached after
```

## Clone the repo

```
mkdir -p ~/development/i3
cd ~/development/i3
git clone https://github.com/scottmsilver/terminal-tools.git agent-tools
```

## Create symlinks

The repo is the source of truth. Symlink config files to where i3, polybar, and WezTerm expect them.

```
mkdir -p ~/scripts

# Core scripts
ln -sf ~/development/i3/agent-tools/i3-tools/set_dpi.sh ~/scripts/set_dpi.sh
ln -sf ~/development/i3/agent-tools/i3-tools/fix-workspaces.py ~/scripts/fix-workspaces.py
ln -sf ~/development/i3/agent-tools/i3-tools/show-keybindings.py ~/scripts/show-keybindings.sh
ln -sf ~/development/i3/agent-tools/i3-tools/i3-window-tracker.py ~/scripts/i3-window-tracker.py
ln -sf ~/development/i3/agent-tools/i3-tools/workspace_namer.py ~/scripts/workspace_namer.py

# Polybar config (symlink the whole directory)
ln -sf ~/development/i3/agent-tools/i3-tools/polybar ~/.config/polybar

# WezTerm config
ln -sf ~/development/i3/agent-tools/i3-tools/wezterm.lua ~/.wezterm.lua
```

## Configure i3

The i3 config at `~/.config/i3/config` needs these key sections. Don't overwrite an existing config — merge these in:

### Font (managed by set_dpi.sh)
```
font pango:Noto Sans 11
```

### Borders and gaps (managed by set_dpi.sh)
```
default_border pixel 3
default_floating_border normal 8
gaps inner 8
gaps outer 2
smart_borders on
smart_gaps on
```

### Polybar (replaces i3bar)
Remove any existing `bar { ... }` block and add:
```
exec_always --no-startup-id ~/.config/polybar/launch.sh
```

### DPI auto-detect keybinding
```
bindsym $mod+Shift+d exec --no-startup-id ~/scripts/set_dpi.sh
```

### Workspace recovery after CRD reconnect
```
bindsym $mod+Shift+w exec --no-startup-id ~/scripts/fix-workspaces.py
```

### Keybinding cheat sheet
```
for_window [title="^Keybindings$"] floating enable, border pixel 2
```

### Window tracker daemon (optional, improves fix-workspaces accuracy)
```
exec --no-startup-id ~/scripts/i3-window-tracker.py
```

## Configure xsettingsd

Create the config directory and start the daemon:
```
mkdir -p ~/.config/xsettingsd
echo "Xft/DPI 98304" > ~/.config/xsettingsd/xsettingsd.conf
xsettingsd &
```

The DPI value (98304 = 96 * 1024) will be managed by `set_dpi.sh` going forward.

## Calibrate DPI

Run the calibration for your current display:

1. Open the calibration page:
   ```
   set_dpi.sh calibrate
   ```
2. Hold a physical ruler to the screen and measure the white bar.
3. Use the slider to find a comfortable DPI.
4. Apply it:
   ```
   set_dpi.sh <your-dpi>
   ```
5. Save the profile for this resolution:
   ```
   set_dpi.sh save
   ```

Repeat from each client device you connect from (each produces a different CRD resolution). After saving, `set_dpi.sh` with no args auto-detects the right DPI.

## Verify

After restarting i3 (`$mod+Shift+r`):

- [ ] Polybar visible at the bottom with workspace tabs, status modules, DPI +/- buttons
- [ ] Mouse wheel on polybar scrolls through workspaces
- [ ] DPI +/- buttons in polybar adjust scaling
- [ ] `$mod+Shift+d` auto-applies saved DPI for current resolution
- [ ] WezTerm font scales with DPI changes
- [ ] Chrome/GTK apps scale with DPI changes
- [ ] `$mod+Shift+w` recovers workspace layout after CRD reconnect

## File map

| Repo file | Symlinked to | Purpose |
|-----------|-------------|---------|
| `set_dpi.sh` | `~/scripts/set_dpi.sh` | DPI calibration and switching |
| `polybar/` | `~/.config/polybar/` | Status bar config and launcher |
| `wezterm.lua` | `~/.wezterm.lua` | Terminal config with DPI-aware font scaling |
| `fix-workspaces.py` | `~/scripts/fix-workspaces.py` | Workspace recovery after CRD reconnect |
| `show-keybindings.py` | `~/scripts/show-keybindings.sh` | Floating keybinding cheat sheet |
| `i3-window-tracker.py` | `~/scripts/i3-window-tracker.py` | Window origin tracking daemon |
| `workspace_namer.py` | `~/scripts/workspace_namer.py` | AI workspace naming via the `gemini` CLI; invoked by the polybar ✨ button |
| `calibrate.html` | (opened by set_dpi.sh) | Visual DPI calibration page |

## Profiles

DPI profiles are saved per-resolution in `~/.config/dpi-profiles.conf`. Format:
```
3046x1804=138
2880x1468=138
1920x1080=96
```

Each line maps a CRD resolution to the preferred DPI. When you run `set_dpi.sh` with no arguments, it looks up the current xrandr resolution and applies the matching DPI.
