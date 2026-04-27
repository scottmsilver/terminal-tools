# i3 Tools

Utilities for managing an i3wm + WezTerm desktop environment. Includes a versioned i3 config with per-machine overrides, AI-powered workspace naming, DPI switching for laptop/desktop modes, workspace recovery after CRD reconnects, status bar enhancements, and a Chrome-Remote-Desktop client-OS detector that adds Ctrl+Super fallbacks for the handful of shortcuts ChromeOS captures (Super+Tab, Super+L, Super+Arrows) even with "Send system keys" enabled.

## Quick start

```bash
cd i3/
./install.sh            # installs ~/.config/i3/config + ~/scripts/crd-client-detector.py
```

See [`i3/README.md`](i3/README.md) for the full i3-config + CRD detector story. Everything below covers the companion scripts that live in `i3-tools/` proper.

## Prerequisites

- **i3wm** and **WezTerm**
- **Python 3.10+** with `i3ipc` and `requests` (`pip install -r requirements.txt`)
- **Gemini CLI** authenticated once interactively (for workspace namer — `npm install -g @google/gemini-cli`)
- **ImageMagick** (for screenshots)
- **xrdb**, **xsettingsd** (for DPI switching)

## Tools

### `i3/` — i3 config + CRD client-OS detector

The versioned i3 config, a `conf.d/` override pattern for per-machine
settings, and a daemon that watches the Chrome Remote Desktop journal and
— when the connected client is a Chromebook — adds `Ctrl+Super+<key>`
fallbacks for the narrow set of chords ChromeOS still captures even with
"Send system keys" enabled (Super+Tab, Super+L, Super+Arrows). Install via
`i3/install.sh`. Full details in [`i3/README.md`](i3/README.md).

### `workspace_namer.py` — AI Workspace Namer (text-only)

Renames i3 workspaces based on their content by shelling out to the `gemini` CLI. Gathers WezTerm terminal text, git metadata, and X11 window classes/titles per workspace, sends one prompt, and applies the returned names.

```bash
./workspace_namer.py             # gather + call gemini + apply
./workspace_namer.py --no-apply  # gather + call gemini, print preview, no rename
./workspace_namer.py --dry-run   # gather only; print prompt, no API call
```

No arguments by default; always names all active workspaces. Names are clamped to ≤10 chars by `smart_truncate()` (cascade: as-is → drop dashes → devowel → ellipsis). Errors surface via `notify-send`.

### `workspace_namer_vision.py` — Hybrid (text + vision) namer

Augments the text-based namer with a screenshot of each workspace, sent to gemini's multimodal model. Useful when the workspace's identity isn't fully captured in scrollback — browser tabs, GUI app state, IDE filename bars, etc. Briefly cycles through every workspace to capture each (~200 ms per workspace, returns to the originally-focused one). Imports `workspace_namer` for `gather_context()` and `sanitize()` so all the text-side logic stays in one place.

```bash
./workspace_namer_vision.py             # gather text + screenshots + call gemini + apply
./workspace_namer_vision.py --no-apply  # preview only
```

Each workspace gets THREE candidate names spanning different axes (project / activity / screenshot-derived) plus a single `best` pick which is what actually gets applied. Screenshots cached in `agent-tools/.cache/ws-shots/` (gitignored). The polybar ✨ button is wired to this hybrid script — clicking N runs the full text+vision pipeline.

Why hybrid: vision-alone misreads workspaces because the dominant on-screen content is whatever the user was actively reading (often an AI chat session), not what the workspace is "about". Text-alone misses signals only the screen carries. Combining lets each compensate for the other's blind spot.

> **If the ✨ button does nothing:** polybar swallows stderr from click-handlers, so the notify-send toast won't fire on import errors. Run `~/scripts/workspace_namer.py` directly from a terminal to see the traceback. The most common failure is `ModuleNotFoundError: No module named 'i3ipc'` — the script's `#!/usr/bin/env python3` shebang resolves to whichever `python3` is first on PATH (often anaconda), which needs the deps explicitly installed:
>
> ```bash
> pip3 install i3ipc requests
> ```

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
