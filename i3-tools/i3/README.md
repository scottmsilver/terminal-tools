# i3 config + CRD client detector

Versioned i3 window-manager config, a per-machine override mechanism, and a
daemon that retunes keybindings when the Chrome Remote Desktop client is a
Chromebook.

## What's here

| File | What it does |
|------|--------------|
| `config` | Main i3 config. Copy of what the wizard generates plus gaps, polybar, DPI, file-picker floating rules, and an `exec_always` that launches the CRD detector. |
| `install.sh` | Installs `config` to `~/.config/i3/config`, symlinks the detector into `~/scripts/`, seeds `~/.config/i3/conf.d/local.conf` from the example, validates, reloads i3. |
| `crd-client-detector.py` | Long-lived daemon. Watches the Chrome Remote Desktop journal for `session-initiate` stanzas, maps the client-id to `~/.config/chrome-remote-desktop/paired-clients/<id>.json` to recover the client's OS string, and — on ChromeOS — generates `~/.config/i3/conf.d/crd-mod.conf` with `Ctrl+Super` alternatives for the narrow set of chords ChromeOS still captures client-side even with "Send system keys" enabled (Super+Tab, Super+L, Super+Arrows). Other clients leave the file empty. |
| `conf.d/local.conf.example` | Seed file for per-machine overrides (font size, gaps, distro-specific paths). Copy to `~/.config/i3/conf.d/local.conf` on each machine. |

## Why a CRD client detector?

ChromeOS's CRD client has a **"Send system keys"** toggle. With it off,
the Search / Launcher key (Super / Mod4) is swallowed client-side and
every i3 shortcut breaks — this detector doesn't help in that case; enable
the toggle or fall back to an all-Alt rewrite.

With **"Send system keys" on** (the recommended mode) most of Super passes
through, but ChromeOS *still* intercepts a small set of chords for its own
shelf/overview/lock UI — notably `Super+Tab`, `Super+L`, `Super+Arrow`.
The detector's job is narrow: for just those chords, emit `Ctrl+Super+<key>`
alternatives. `Ctrl+Super` is essentially unused by ChromeOS so it passes
through reliably, and we don't touch the rest of the bindings, so Mac /
iPad / Linux clients see no extra shortcuts at all.

The collision list lives at the top of `crd-client-detector.py`
(`CHROMEOS_COLLISION_KEYS`). Add a key name there, or drop your own
`bindsym $mod+Ctrl+<key> ...` line into `~/.config/i3/conf.d/local.conf`,
if you hit a chord ChromeOS eats that isn't already covered.

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
parsed above. That's why the CRD detector emits concrete `bindsym $mod+Ctrl+<key> …`
lines (which i3 then resolves to `Mod4+Control+<key>`) rather than trying to
swap the `$mod` alias in flight.

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
         │ crd-mod.conf = `bindsym     │   │ crd-mod.conf = empty comment │
         │ $mod+Ctrl+<key> …` for      │   │ (default Mod4 is fine)       │
         │ Tab, L, Arrows (the chords  │   └──────────────────────────────┘
         │ ChromeOS captures even      │
         │ with Send-system-keys on)   │
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

Enable **"Send system keys"** / **"Pass system keys"** in the CRD ChromeOS
client's side panel — without it, Super itself is swallowed and none of the
detector's logic matters. Assume it's on for everything below.

Even with Send-keys on, these Super chords still get eaten before reaching
the remote. Use the Ctrl+Super fallbacks the detector emits (or add more):

| Chord that fails | What ChromeOS does | Detector's fallback |
|------------------|---------------------|---------------------|
| `Super+Tab` | Overview / window switcher | `Super+Ctrl+Tab` → rofi window-switch |
| `Super+L` | Lock screen (hard-captured) | `Super+Ctrl+L` → `focus up` |
| `Super+Left/Right/Up/Down` | Window snap / virtual desk nav | `Super+Ctrl+<arrow>` → `focus <dir>` |

`Shift+Super` is *less* safe than `Ctrl+Super` — recent ChromeOS builds use
`Shift+Super+M`, `Shift+Super+L`, etc. Stick to Ctrl+Super for fallbacks.

To add more fallbacks, append the key to `CHROMEOS_COLLISION_KEYS` at the top
of `crd-client-detector.py` and restart the daemon, or drop a line directly
into `~/.config/i3/conf.d/local.conf`:

```
bindsym $mod+Ctrl+<key> <action>
```

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
| Shortcuts still don't work on ChromeOS | `cat ~/.config/i3/conf.d/crd-mod.conf` — expect `bindsym $mod+Ctrl+<key> …` lines for Tab/L/Arrows. If empty, `cat ~/.cache/i3-crd-client.log`. Also confirm **"Send system keys"** is ON in the CRD client. |
| Daemon not running | `ps -ef \| grep crd-client-detector`. If missing, `i3-msg restart` (not `reload` — exec_always only re-fires on restart in 4.23). |
| Want to force re-detection | See "Starting fresh" above. |
| Changes to `config` aren't live | Re-run `./install.sh` — the live file is a copy, not a symlink. |
| Toast doesn't appear | `dunst` may have died. `systemctl --user restart dunst` or run `pgrep -x dunst`. |
