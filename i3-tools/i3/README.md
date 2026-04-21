# i3 config + CRD client detector

Versioned i3 window-manager config, a per-machine override mechanism, and a
daemon that retunes keybindings when the Chrome Remote Desktop client is a
Chromebook.

## What's here

| File | What it does |
|------|--------------|
| `config` | Main i3 config. Copy of what the wizard generates plus gaps, polybar, DPI, file-picker floating rules, and an `exec_always` that launches the CRD detector. |
| `install.sh` | Installs `config` to `~/.config/i3/config`, symlinks the detector into `~/scripts/`, seeds `~/.config/i3/conf.d/local.conf` from the example, validates, reloads i3. |
| `crd-client-detector.py` | Long-lived daemon. Watches the Chrome Remote Desktop journal for `session-initiate` stanzas, maps the client-id to `~/.config/chrome-remote-desktop/paired-clients/<id>.json` to recover the client's OS string, and — on ChromeOS — generates `~/.config/i3/conf.d/crd-mod.conf` with Alt-based copies of every `$mod` binding. Other clients leave the file empty. |
| `conf.d/local.conf.example` | Seed file for per-machine overrides (font size, gaps, distro-specific paths). Copy to `~/.config/i3/conf.d/local.conf` on each machine. |

## Why a CRD client detector?

ChromeOS swallows the **Search** / **Launcher** key (which normally maps to
`Super_L` / `Mod4`) before it ever leaves the client. That means every i3
shortcut bound to `$mod` (Mod4 by default) silently fails: `$mod+Return`,
`$mod+d`, `$mod+1..9`, `$mod+Shift+r`, all dead.

Dual-binding everything to `Mod1` (Alt) unconditionally *would* work for
ChromeOS, but on Mac/iPad/Linux clients it clobbers common app shortcuts
(Alt+Tab, Alt+Return, Alt+Ctrl+N…). So the detector does it **conditionally**:
only when the currently-connected CRD client is ChromeOS.

## Install

```bash
cd i3-tools/i3
./install.sh
```

`install.sh`:

1. Backs up `~/.config/i3/config` (if present) to `config.bak.<timestamp>`.
2. Copies `config` into place (not a symlink — `set_dpi.sh`'s in-place edits
   would otherwise leak back into the tracked repo).
3. Symlinks `~/scripts/crd-client-detector.py` → repo script, matching the
   convention already used by `set_dpi.sh`, `fix-workspaces.py`, etc.
4. Seeds `~/.config/i3/conf.d/local.conf` from the example (only if the
   override directory is empty).
5. Runs `i3 -C -c` to validate. If it fails, rolls back to the backup.
6. Runs `i3-msg reload` if everything checks out.

Re-run this any time you pull new upstream changes.

> **⚠ DPI drift.** Re-running `install.sh` overwrites the live config's
> font/gap sizes with the repo defaults. If you'd previously run
> `~/scripts/set_dpi.sh`, re-run it after install to re-tune the DPI.

## Per-machine overrides (`conf.d/`)

The main config has `include ~/.config/i3/conf.d/*.conf` near the **end**, so
anything you drop in `~/.config/i3/conf.d/` is spliced in after the defaults
and wins for most directives. `local.conf.example` shows the common knobs —
font size, gaps, distro-specific portal path.

One important exception: i3 variables (`$mod`, `$ws1`, etc.) are substituted
**at parse time where they're referenced**, so redefining `$mod` in a
late-included file will not retroactively change bindings that were already
parsed above. That's why the CRD detector emits fully expanded `bindsym Mod1+…`
lines instead of just redefining `$mod`.

## How the CRD detector works

```
          ┌──────────────────────────┐
          │  CRD host logs to        │
          │  systemd journal         │
          └────────────┬─────────────┘
                       │ session-initiate stanza with
                       │ <pairing-info client-id="UUID"/>
                       ▼
          ┌──────────────────────────┐       ┌────────────────────────────────┐
          │  crd-client-detector.py  │──────▶│  ~/.config/chrome-remote-      │
          │  (journalctl -f -t ...)  │       │  desktop/paired-clients/       │
          └────────────┬─────────────┘       │  <UUID>.json → clientName     │
                       │                      └────────────────────────────────┘
                       │ clientName == "Chrome OS"?
                       │
               yes ────┤──── no
                       │
         ┌─────────────▼──────────────┐   ┌──────────────▼──────────────┐
         │ ~/.config/i3/conf.d/        │   │ ~/.config/i3/conf.d/         │
         │ crd-mod.conf = Mod1 copies  │   │ crd-mod.conf = empty comment │
         │ of every top-level          │   │ (default Mod4 is fine)       │
         │ `bindsym $mod …` line       │   └──────────────────────────────┘
         └─────────────┬──────────────┘
                       │ i3-msg reload + notify-send toast
                       ▼
                 i3 picks up new bindings
```

State is cached to `~/.cache/i3-crd-client.state` so re-connects from the same
client don't trigger unnecessary reloads.

### Files the daemon owns

| Path | Purpose |
|------|---------|
| `~/.config/i3/conf.d/crd-mod.conf` | Generated bindings. Do not hand-edit. |
| `~/.cache/i3-crd-client.pid` | Current daemon PID. Used by new instances to replace the old one (avoids double-daemon after i3 restart). |
| `~/.cache/i3-crd-client.state` | `{chromeos,default}:<client-id>`. Short-circuit for repeat detections. |
| `~/.cache/i3-crd-client.log` | Append-only event log. Check here first when debugging. |

### Running manually

```bash
# Daemon mode (blocks, watches journal)
./crd-client-detector.py

# One-shot: scan the last hour, apply the newest client, exit
./crd-client-detector.py --once
```

### Starting fresh

If the state gets confused (e.g. you want to force re-detection after changing
the script):

```bash
kill "$(cat ~/.cache/i3-crd-client.pid)" 2>/dev/null
rm -f ~/.cache/i3-crd-client.{pid,state}
rm -f ~/.config/i3/conf.d/crd-mod.conf
i3-msg restart   # restart, not reload — exec_always only re-fires on restart in i3 4.23
```

## ChromeOS gotchas

The detector makes `$mod` shortcuts work, but a few chords are additionally
swallowed by the ChromeOS client itself, before Alt can reach i3:

| Key | What ChromeOS does with it | Workaround |
|-----|----------------------------|------------|
| `Alt+Tab` | Chrome OS window switcher | Add a different shortcut for rofi window-switch in `conf.d/local.conf`, e.g. `bindsym Mod1+grave exec rofi -show window` |
| `Alt+Ctrl+<n>` | Switches virtual desktops on ChromeOS | Consider remapping the "move-and-follow" chord |
| `Alt+Shift+q` | Usually fine — reaches i3 as `kill` | — |
| `Alt+Return` | Usually fine — reaches i3 as terminal spawn | — |

In the Chrome Remote Desktop client, toggle **"Send keyboard shortcuts"** on
if you want ChromeOS keys like the Launcher to be forwarded — but even with
this on, `Alt+Tab` is still captured.

## Detection reliability

The detector hinges on two facts about CRD:

1. Every incoming session kicks off a Jingle `session-initiate` IQ stanza
   that CRD's host logs verbatim to `journalctl -t chrome-remote-desktop`.
2. Paired clients are stored with a human-readable `clientName` at
   `~/.config/chrome-remote-desktop/paired-clients/<UUID>.json`. Known values
   include `Chrome OS`, `MacIntel`, `iPhone`, `iPad`, `Linux armv81`.

Both are CRD implementation details, not stable public APIs. If Google
restructures the log format or storage path, this detector breaks silently.
Symptoms: the toast never appears, `~/.cache/i3-crd-client.log` stays empty
after a reconnect. In that case, re-run the recon listed in the "digging
deep" commit message to find the new signal, or fall back to an explicit
toggle hotkey.

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Shortcuts still don't work on ChromeOS | `cat ~/.config/i3/conf.d/crd-mod.conf` — expect Mod1 bindings. If empty, `cat ~/.cache/i3-crd-client.log`. |
| Daemon not running | `ps -ef \| grep crd-client-detector`. If missing, `i3-msg restart` (not `reload` — exec_always only re-fires on restart in 4.23). |
| Want to force re-detection | See "Starting fresh" above. |
| Changes to `config` aren't live | Re-run `./install.sh` — the live file is a copy, not a symlink. |
| Toast doesn't appear | `dunst` may have died. `systemctl --user restart dunst` or run `pgrep -x dunst`. |
