#!/usr/bin/env python3
"""Wraps i3status output to add a clickable [? Keys] block and round percentages."""

import json
import os
import re
import subprocess
import sys
import threading

KEYS_BLOCK = {
    "name": "keybindings",
    "full_text": " ? Keys ",
    "color": "#bd93f9",
    "separator": True,
    "separator_block_width": 15,
}

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "show-keybindings.py")


def round_match(match):
    """Round a float percentage string to the nearest integer."""
    val = float(match.group(1))
    return f"{round(val)}%"


def handle_clicks():
    """Read click events from i3bar on stdin."""
    for line in sys.stdin:
        line = line.strip().lstrip(",")
        if not line or line == "[":
            continue
        try:
            click = json.loads(line)
            if click.get("name") == "keybindings":
                subprocess.Popen([SCRIPT])
        except (json.JSONDecodeError, KeyError):
            pass


def main():
    proc = subprocess.Popen(
        ["i3status", "-c", os.path.expanduser("~/.config/i3status/config")],
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    # Read i3status header, enable click events
    header = json.loads(proc.stdout.readline())
    header["click_events"] = True
    sys.stdout.write(json.dumps(header) + "\n")

    # Pass through opening bracket
    sys.stdout.write(proc.stdout.readline())
    sys.stdout.flush()

    # Handle click events in background
    t = threading.Thread(target=handle_clicks, daemon=True)
    t.start()

    # Regex to find percentages like "35.7%"
    pct_regex = re.compile(r"(\d+\.\d+)%")

    # Process each status update
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue

        prefix = ""
        if line.startswith(","):
            prefix = ","
            line = line[1:]

        try:
            blocks = json.loads(line)
            # Round percentages in memory and disk blocks
            for block in blocks:
                if block.get("name") in ("memory", "disk_info"):
                    block["full_text"] = pct_regex.sub(round_match, block["full_text"])

            blocks.insert(0, KEYS_BLOCK)
            sys.stdout.write(prefix + json.dumps(blocks) + "\n")
            sys.stdout.flush()
        except json.JSONDecodeError:
            sys.stdout.write(prefix + line + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
