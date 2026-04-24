# terminal-tools

Utilities for managing a remote Linux development environment accessed via Chrome Remote Desktop.

## Sub-projects

### [`i3-tools/`](i3-tools/)

i3wm + WezTerm desktop toolkit. Versioned i3 config with `conf.d/` per-machine overrides, AI-powered workspace naming, DPI calibration for multi-device CRD access, workspace recovery after reconnects, window origin tracking, status bar enhancements, and a Chrome-Remote-Desktop client-OS detector that adds Ctrl+Super fallbacks for the handful of shortcuts ChromeOS captures (Super+Tab, Super+L, Super+Arrows) even with "Send system keys" enabled.

### [`apk-remote-install/`](apk-remote-install/)

Push-based Android APK installer. A Python Textual TUI maintains an SSH tunnel to a remote build machine, watches a FIFO for APK paths, pulls via rsync, and installs via adb.

## Setup

```bash
# i3-tools: install the i3 config (and the CRD client detector symlink)
cd i3-tools/i3
./install.sh

# i3-tools: companion scripts (DPI, workspace recovery, etc.)
cd ..
pip install -r requirements.txt
cd ~/scripts && ln -sf ~/development/i3/agent-tools/i3-tools/set_dpi.sh .

# apk-remote-install
cd apk-remote-install
uv run apk_listener.py
```

See each sub-project's README for details.
