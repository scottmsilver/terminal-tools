# i3 Tools

Utilities for managing an i3wm + WezTerm desktop environment. Includes AI-powered workspace naming, DPI switching for laptop/desktop modes, workspace recovery after CRD reconnects, and status bar enhancements.

## Prerequisites

- **i3wm** and **WezTerm**
- **Python 3.10+** with `i3ipc` and `requests` (`pip install -r requirements.txt`)
- **Gemini CLI** authenticated once interactively (for workspace namer — `npm install -g @google/gemini-cli`)
- **ImageMagick** (for screenshots)
- **xrdb**, **xsettingsd** (for DPI switching)

## Tools

### `workspace_namer.py` — AI Workspace Namer

Renames i3 workspaces based on their content by shelling out to the `gemini` CLI. Gathers WezTerm terminal text, git metadata, and X11 window classes/titles per workspace, sends one prompt, and applies the returned names.

```bash
./workspace_namer.py
```

Invoked by the polybar ✨ button. No arguments; always names all active workspaces. Errors surface via `notify-send`.

### `set_dpi.sh` — DPI / Font Switcher

Sets all scaling knobs consistently from a single DPI value: X resources, xsettingsd (Chrome/GTK), i3 font, borders, gaps, and polybar font/height. WezTerm auto-scales by reading `Xft.dpi`. Saves resolution→DPI profiles so it can auto-detect the right DPI when switching between CRD client devices.

```bash
./set_dpi.sh              # Auto-detect from saved resolution→DPI profile
./set_dpi.sh 138          # Set exact DPI (all settings derived automatically)
./set_dpi.sh save         # Save current resolution→DPI mapping
./set_dpi.sh calibrate    # Open visual calibration page (ruler + slider)
./set_dpi.sh diagnose     # Print all current DPI/scaling state
./set_dpi.sh laptop       # Alias for 192 DPI
./set_dpi.sh desktop      # Alias for 96 DPI
```

Profiles stored in `~/.config/dpi-profiles.conf`. The calibration page (`calibrate.html`) includes a physical ruler measurement tool and an interactive DPI slider with live previews of terminal, Chrome, and i3 bar rendering.

### `polybar/` — Status Bar Configuration

Polybar config (Dracula theme) replacing i3bar. Features scrollable workspace tabs (mouse wheel cycles through workspaces that don't fit), compact status modules (C/L/M/D), and a `?` keybinding cheat sheet button. Font size and bar height are automatically scaled by `set_dpi.sh`.

Symlink to `~/.config/polybar/`:
```bash
ln -sf ~/development/i3/agent-tools/i3-tools/polybar ~/.config/polybar
```

### `wezterm.lua` — WezTerm Configuration

Reference copy of `~/.wezterm.lua`. Key features:
- **DPI-aware font scaling**: Reads `Xft.dpi` via xrdb, scales `BASE_FONT_SIZE` (11pt) proportionally. At 192 DPI → 22pt, at 96 DPI → 11pt.
- **Unix domain mux**: Persistent server via `unix` domain so tabs survive GUI restarts.
- **Smart scrollbar**: Hides in alternate screen or when there's no scrollback.
- **Clickable workspace titles**: Click the tab bar title to set a custom workspace prefix.

### `fix-workspaces.py` — Workspace Recovery

Rebuilds i3 workspace layout after a CRD/VNC reconnect scrambles WezTerm windows. Queries the wezterm mux for all windows, kills and relaunches the GUI, matches windows to projects by tab count and title, and distributes them to named workspaces. Includes a fullscreen-toggle workaround for WezTerm's software renderer surface bug.

```bash
./fix-workspaces.py
```

### `i3-window-tracker.py` — Window Origin Tracker

Daemon that listens for i3 window events and records which workspace each window was created on. This captures user intent — if you open Chrome from workspace "3:unifi", that Chrome window belongs to the unifi project. Used by `fix-workspaces.py` to restore non-WezTerm windows to the correct workspace.

```bash
# Run as a daemon (e.g., from i3 config or xstartup)
./i3-window-tracker.py
```

Writes mappings to `~/.cache/i3-window-workspaces.json`. Auto-prunes entries older than 7 days.

### `i3status-wrapper.py` — Status Bar Wrapper

Wraps i3status output to add a clickable `[? Keys]` block that opens the keybinding cheat sheet, and rounds float percentages in memory/disk blocks.

```bash
# In i3 config:
# bar { status_command python3 ~/path/to/i3status-wrapper.py }
```

### `show-keybindings.py` — Keybinding Cheat Sheet

Floating tkinter window showing all i3 keybindings. Triggered by clicking `[? Keys]` in the status bar. Press Escape or q to close.

## Deployment

These scripts are the canonical source. To deploy, symlink them into `~/scripts/` (where i3 config references them):

```bash
cd ~/scripts
ln -sf ~/development/i3/agent-tools/i3-tools/set_dpi.sh .
ln -sf ~/development/i3/agent-tools/i3-tools/fix-workspaces.py .
ln -sf ~/development/i3/agent-tools/i3-tools/i3-window-tracker.py .
ln -sf ~/development/i3/agent-tools/i3-tools/i3status-wrapper.py .
ln -sf ~/development/i3/agent-tools/i3-tools/show-keybindings.py .
```

For WezTerm config, symlink or copy `wezterm.lua` to `~/.wezterm.lua`.

## Other Files

- `inspect_workspace.py` — Dumps raw metadata for a specific workspace number.
- `get_i3_windows.py` — Low-level i3 window listing (prints JSON).
- `test_visual.py` — Screenshot + LLM analysis demo.
- `debug.html` — Web UI for inspecting LLM payloads.
- `requirements.txt` — Python dependencies (`i3ipc`, `requests`).
