# CLAUDE.md

## Project overview

Tools for installing Android APKs built on a remote Linux machine onto a locally-connected device via adb. The main component is `apk_listener.py`, a Python Textual TUI that maintains an SSH tunnel to a build machine, watches a FIFO for APK paths, pulls them via rsync, and installs via adb. Companion shell scripts (`push-apk.sh`, `apk-listener.sh`, `apk-install.sh`) provide the build-machine side and a bash fallback.

## Key files

- `apk_listener.py` — Main TUI app (Python, Textual). Single-file script using PEP 723 inline metadata.
- `push-apk.sh` — Runs on the build machine; writes APK paths into the FIFO.
- `apk-listener.sh` — Bash fallback for the listener (same functionality, flat log output).
- `apk-install.sh` — Standalone pull-based install script.

## Build and run

```bash
# Run with uv (auto-installs dependencies):
uv run apk_listener.py

# Or manually:
pip install textual rich && python3 apk_listener.py
```

No build step. Dependencies are declared inline in the script header (`textual>=0.40.0`, `rich>=13.0.0`). Python 3.10+ required.

## Architecture notes

- **Single-file TUI**: All Python code lives in `apk_listener.py`. No package structure.
- **SSH control socket**: One SSH master connection (`-M -S sock`) is established in `_ensure_ssh`; all subsequent SSH/rsync commands reuse it via `-S sock`. Password auth uses SSH_ASKPASS with a temp script.
- **Startup flow**: `on_mount` → `PasswordScreen` → `_check_dependencies` → `_detect_devices` → `_run_ssh_manager`. Each stage gates the next.
- **Concurrency**: rsync pulls run in parallel (separate `@work` workers). adb installs are serialized via `_install_lock`. The FIFO reader is a single persistent SSH session.
- **Config**: Hardcoded defaults in the `Config` dataclass. `password` and `device` are set at runtime via startup screens.

## Code style

- No type stubs or mypy config; uses `from __future__ import annotations` for forward refs.
- Textual widgets use `reactive` descriptors. Screens use `dismiss(value)` to return data to callbacks.
- Shell scripts use bash with `set -euo pipefail`.

## Lint / test

No test suite or linter config. Verify syntax with:

```bash
python3 -c "import ast; ast.parse(open('apk_listener.py').read())"
```
