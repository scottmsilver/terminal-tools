# apk-remote-install

Tools for installing Android APKs built on a remote machine onto a locally-connected device via `adb`.

The typical setup: you build on a Linux server but have your Android device plugged into your Mac via USB. These scripts bridge that gap.

## Scripts

### `apk-listener.sh` + `push-apk.sh` — Push-based install

A two-part system where the build machine pushes installs to your Mac automatically.

**How it works:**
1. Your Mac opens an SSH tunnel to the build server and watches a named pipe (FIFO)
2. On the build server, `push-apk.sh` writes the APK path into the FIFO
3. The Mac picks it up, `rsync`s the APK back through the SSH tunnel, and runs `adb install`

No open ports required — everything runs over your existing SSH keys.

**On your Mac:**
```bash
./apk-listener.sh
```

```
  APK Install Listener
  ─────────────────────────────────────
  Remote   ssilver@192.168.1.138
  FIFO     /tmp/apk-push-pipe

[14:30:01] ✓ Tunnel up
[14:30:01] ✓ FIFO ready on remote
[14:30:01] Waiting for APK...
[14:30:15] ↓ Pulling app-debug.apk
[14:30:18] ✓ Pulled  42.3 MB
[14:30:18] ⏳ Installing via adb...
[14:30:22] ✓ Installed successfully
```

**On the build machine:**
```bash
./push-apk.sh ./app/build/outputs/apk/debug/app-debug.apk
```

The write to the FIFO blocks until the Mac listener reads it, so when the script completes you know the Mac has started pulling. You can also call this from a build script to auto-install after every build.

**Auto-update:** `push-apk.sh` keeps itself up to date automatically:
- Every time the Mac listener starts, it syncs the latest `push-apk.sh` to `~/scripts/` on the build machine.
- Every time `push-apk.sh` runs, it checks GitHub for a newer version and replaces itself before proceeding.

### `apk-install.sh` — Pull-based install (manual)

A simpler standalone script you run on the Mac when you want to manually pull and install an APK.

```bash
# Install a specific APK
./apk-install.sh ~/development/myapp/app/build/outputs/apk/debug/app-debug.apk

# Or run with no args to pick from recent history
./apk-install.sh
```

```
Recent APKs:

  1) ~/development/myapp/app/build/outputs/apk/debug/app-debug.apk
  2) ~/development/other/app-release.apk

Pick one (1-2):
```

Remembers your last 10 APK paths in `~/.apk_installer_history`.

## Configuration

Edit the variables at the top of each script:

| Variable | Script | Default | Description |
|----------|--------|---------|-------------|
| `REMOTE` / `REMOTE_HOST` | all | `ssilver@192.168.1.138` | SSH user@host for the build machine |
| `ADB` | listener, install | `$HOME/Library/Android/sdk/platform-tools/adb` | Path to `adb` |
| `FIFO` | listener, push | `/tmp/apk-push-pipe` | Named pipe path on the build machine |
| `SOCK` | listener | `/tmp/apk-listener-ssh.sock` | SSH multiplexing socket path |
| `REMOTE_SCRIPT_DIR` | listener | `~/scripts` | Where to sync `push-apk.sh` on the build machine |

## Requirements

- **Mac side:** `rsync`, `adb` (Android SDK), SSH key access to the build machine
- **Build machine side:** `rsync`, `ssh`, `curl` (for self-update), standard coreutils
