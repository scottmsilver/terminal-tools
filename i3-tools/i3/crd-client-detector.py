#!/usr/bin/env python3
"""Detect the CRD client's OS and rebind i3 shortcuts when it's ChromeOS.

ChromeOS swallows the Search/Launcher key (Super_L) before it reaches the
remote host, so Mod4-based i3 shortcuts silently fail. This daemon watches
the chrome-remote-desktop journal for `session-initiate` stanzas, maps the
client-id it finds against ~/.config/chrome-remote-desktop/paired-clients/
to recover the client's OS string, and — on ChromeOS — writes a conf.d file
that dual-binds every `$mod` shortcut to Mod1 (Alt) as well. For any other
client, the file is cleared so plain Mod4 continues to be the only binding.

Designed to be launched once from i3 via `exec_always` — on each i3 reload
it replaces any previous instance via fuser-style pgrep.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
PAIRED_DIR = HOME / ".config/chrome-remote-desktop/paired-clients"
MAIN_CONFIG = HOME / ".config/i3/config"
CONF_D = HOME / ".config/i3/conf.d"
OUT_FILE = CONF_D / "crd-mod.conf"
STATE_FILE = HOME / ".cache/i3-crd-client.state"

CHROMEOS_CLIENT_NAMES = {"Chrome OS", "CrOS", "ChromeOS"}

# Chords that ChromeOS captures before forwarding to the remote, even with
# "Send keyboard shortcuts" / "Pass system keys" enabled. For each of these
# we emit a Ctrl+$mod equivalent as an alternative — Ctrl+Super is essentially
# unused by ChromeOS so it passes through reliably. Keep this list narrow:
# everything else should route through the user's normal `$mod` (Super)
# bindings, so Mac/iPad/Linux clients don't see spurious extra chords either
# (the conf.d file is empty for non-ChromeOS clients).
#
# Match is on the key part AFTER `$mod+`, case-sensitive, with any further
# modifiers collapsed. So `Tab` matches `$mod+Tab` but not `$mod+Shift+Tab`.
# If a chord you hit isn't here, add it — or drop an extra
# `bindsym $mod+Ctrl+<key> ...` into ~/.config/i3/conf.d/local.conf.
CHROMEOS_COLLISION_KEYS: frozenset[str] = frozenset(
    {
        "Tab",  # ChromeOS overview / window switcher
        "l",  # ChromeOS lock screen (Super+L is intercepted on nearly every OS)
        "Left",  # ChromeOS window snap / virtual-desk nav
        "Right",
        "Up",
        "Down",
    }
)
CHROMEOS_FALLBACK_PREFIX = "Ctrl+"  # emitted as `$mod+Ctrl+<key>`

CLIENT_ID_RE = re.compile(r'client-id="([0-9a-f-]+)"')
LOG_FILE = HOME / ".cache/i3-crd-client.log"


def log(msg: str) -> None:
    """Write to stdout *and* a persistent file.

    When i3 launches this via `exec_always`, stdout/stderr usually go to
    `~/.xsession-errors`, but on systems where that file isn't active they
    silently disappear. The persistent log file makes diagnosis possible.
    """
    import datetime

    stamped = f"[{datetime.datetime.now().isoformat(timespec='seconds')}] {msg}"
    try:
        print(stamped, file=sys.stdout, flush=True)
    except OSError:
        pass
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a") as f:
            f.write(stamped + "\n")
    except OSError:
        pass


def client_name(client_id: str) -> str | None:
    path = PAIRED_DIR / f"{client_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text()).get("clientName")
    except (json.JSONDecodeError, OSError):
        return None


BINDSYM_RE = re.compile(
    r"""^\s*bindsym\s+
        (?P<chord>\$mod(?:\+[A-Za-z0-9_]+)*)  # $mod or $mod+Shift+... — the key part
        \s+(?P<action>.+)$                    # rest of the line: the action
    """,
    re.VERBOSE,
)


def chord_key(chord: str) -> str:
    """Return the final key of a chord like `$mod+Shift+Tab` → `Tab`."""
    return chord.rsplit("+", 1)[-1]


def chord_has_extra_modifier(chord: str) -> bool:
    """True if the chord already has Shift/Ctrl/Mod1 on top of $mod."""
    parts = chord.split("+")
    return any(p in {"Shift", "Ctrl", "Control", "Mod1"} for p in parts[1:-1])


def chromeos_overrides(main_cfg: str, reason: str, collisions: set[str]) -> str:
    """Emit a conf.d file with Ctrl+Super alternatives for ChromeOS-reserved chords.

    ChromeOS — even with "Send system keys" on — still captures a small set
    of Super chords (Super+Tab, Super+L, Super+Arrow) for its own UI. For
    each top-level `bindsym $mod+<KEY> ...` where KEY is in the collision
    set, we emit a sibling `bindsym $mod+Ctrl+<KEY> ...` with the same
    action. Ctrl+Super is unused by ChromeOS so it passes through reliably.

    We only rewrite chords *without* an existing extra modifier (Shift/Ctrl/
    Mod1) so we don't generate weird chords like `$mod+Ctrl+Shift+Tab`. The
    original Super binding is left in place — it just never fires on
    ChromeOS because the client ate it, and it still works from Mac/Linux.

    Bindings inside `mode "..." { ... }` blocks are skipped (their scope
    isn't top-level, and for this use-case they aren't what the user is
    hitting from ChromeOS).
    """
    lines = [
        "# Auto-generated by crd-client-detector.py — do not edit by hand.",
        f"# Reason: {reason}",
        "# Adds Ctrl+$mod alternatives for chords ChromeOS captures even with",
        "# 'Send system keys' enabled (Super+Tab, Super+L, Super+Arrow keys).",
        "",
    ]
    brace_depth = 0
    emitted = 0
    for raw in main_cfg.splitlines():
        stripped = raw.lstrip()
        if brace_depth == 0 and stripped.startswith("bindsym $mod"):
            m = BINDSYM_RE.match(stripped)
            if m:
                chord, action = m.group("chord"), m.group("action")
                key = chord_key(chord)
                if key in collisions and not chord_has_extra_modifier(chord):
                    # Inject `Ctrl+` right after `$mod+` so variable substitution
                    # still works and any existing `$mod` alias stays honored.
                    new_chord = chord.replace("$mod+", "$mod+Ctrl+", 1)
                    lines.append(f"bindsym {new_chord} {action}")
                    emitted += 1
        brace_depth += raw.count("{") - raw.count("}")
    lines.append("")
    lines.append(f"# {emitted} override(s) emitted.")
    return "\n".join(lines) + "\n"


def current_state() -> str:
    try:
        return STATE_FILE.read_text().strip()
    except OSError:
        return ""


def set_state(value: str) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(value + "\n")


def reload_i3() -> None:
    subprocess.run(["i3-msg", "reload"], capture_output=True)


def notify(summary: str, body: str = "") -> None:
    subprocess.run(
        ["notify-send", "-t", "3000", "-i", "input-keyboard", summary, body],
        capture_output=True,
    )


def apply_for_client(client_id: str) -> None:
    name = client_name(client_id) or f"unknown ({client_id[:8]}…)"
    is_chromeos = (client_name(client_id) or "") in CHROMEOS_CLIENT_NAMES
    target = "chromeos" if is_chromeos else "default"

    if current_state() == f"{target}:{client_id}":
        log(f"no change: already applied for {name}")
        return

    CONF_D.mkdir(parents=True, exist_ok=True)
    if is_chromeos:
        main = MAIN_CONFIG.read_text()
        OUT_FILE.write_text(
            chromeos_overrides(
                main,
                f"CRD client is {name!r}; adding Ctrl+Super alternatives for "
                "chords ChromeOS still captures with Send-system-keys on.",
                set(CHROMEOS_COLLISION_KEYS),
            )
        )
        log(f"wrote {OUT_FILE} (Ctrl+Super overrides for {name})")
        notify(
            "ChromeOS client detected",
            "Ctrl+Super fallbacks active for Tab / L / Arrows",
        )
    else:
        OUT_FILE.write_text(f"# CRD client is {name!r}; default Mod4 bindings are sufficient.\n")
        log(f"cleared {OUT_FILE} (client {name!r} uses default Mod4)")
        notify("CRD client", f"Default Mod4 shortcuts ({name})")

    set_state(f"{target}:{client_id}")
    reload_i3()


def scan_recent() -> str | None:
    """Look back for the latest session-initiate; return its client-id.

    Scans since boot so long-lived sessions (typical for a desktop where the
    user connects once in the morning) are still picked up hours later. `-r`
    iterates newest-first so we can stop at the first match.
    """
    result = subprocess.run(
        [
            "journalctl",
            "-t",
            "chrome-remote-desktop",
            "--boot",
            "-r",
            "--no-pager",
            "-o",
            "cat",
        ],
        capture_output=True,
        text=True,
    )
    # Newest first — return the first client-id we see on a session-initiate.
    for line in result.stdout.splitlines():
        if "session-initiate" not in line:
            continue
        m = CLIENT_ID_RE.search(line)
        if m:
            return m.group(1)
    return None


def watch_journal() -> None:
    proc = subprocess.Popen(
        [
            "journalctl",
            "-t",
            "chrome-remote-desktop",
            "-f",
            "--since",
            "now",
            "-o",
            "cat",
        ],
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        if "session-initiate" not in line:
            continue
        m = CLIENT_ID_RE.search(line)
        if m:
            apply_for_client(m.group(1))


PID_FILE = HOME / ".cache/i3-crd-client.pid"


def kill_previous() -> None:
    """Replace any prior instance via PID file.

    Avoids `pgrep -f` because that also matches the invoking shell's cmdline
    (any process that has this script's path in its argv) and would kill the
    very bash that just launched us.
    """
    try:
        prev = int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        prev = 0
    if prev and prev != os.getpid():
        try:
            os.kill(prev, signal.SIGTERM)
        except ProcessLookupError:
            pass
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))


def main(argv: list[str]) -> int:
    once = "--once" in argv
    kill_previous()
    log(f"starting (pid={os.getpid()}, once={once})")

    initial = scan_recent()
    if initial:
        apply_for_client(initial)
    else:
        log("no recent session-initiate found; waiting for one")

    if once:
        return 0

    try:
        watch_journal()
    except KeyboardInterrupt:
        log("interrupted")
    except Exception as e:
        log(f"fatal in watch_journal: {e!r}")
        return 1
    log("watch_journal returned (journalctl closed stdout); exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
