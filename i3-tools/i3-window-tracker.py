#!/usr/bin/env python3
"""
i3 window tracker daemon.

Listens for i3 window events and records which workspace each window was
created on. This captures user intent — if you open Chrome from workspace
"3:unifi", that Chrome window belongs to the unifi project.

Writes mappings to ~/.cache/i3-window-workspaces.json:
  { "<x_window_id>": { "workspace": "3:unifi", "class": "Google-chrome", "title": "...", "time": ... } }

Run this alongside i3 (e.g., from i3 config or xstartup).
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

MAPPING_FILE = Path.home() / ".cache" / "i3-window-workspaces.json"
DISPLAY = os.environ.get("DISPLAY", ":1")
ENV = {**os.environ, "DISPLAY": DISPLAY}


def load_mapping():
    if MAPPING_FILE.exists():
        try:
            return json.loads(MAPPING_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_mapping(mapping):
    MAPPING_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Prune entries older than 7 days
    cutoff = time.time() - 7 * 86400
    pruned = {k: v for k, v in mapping.items() if v.get("time", 0) > cutoff}
    MAPPING_FILE.write_text(json.dumps(pruned, indent=2))


def get_focused_workspace():
    """Get the name of the currently focused i3 workspace."""
    try:
        result = subprocess.run(
            ["i3-msg", "-t", "get_workspaces"],
            capture_output=True,
            text=True,
            env=ENV,
        )
        workspaces = json.loads(result.stdout)
        for ws in workspaces:
            if ws.get("focused"):
                return ws["name"]
    except Exception:
        pass
    return None


def main():
    print(f"i3 window tracker starting, writing to {MAPPING_FILE}")
    mapping = load_mapping()

    # Subscribe to window events via i3 IPC
    proc = subprocess.Popen(
        ["i3-msg", "-t", "subscribe", "-m", '["window"]'],
        stdout=subprocess.PIPE,
        text=True,
        env=ENV,
    )

    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        change = event.get("change")
        container = event.get("container", {})
        window_id = container.get("window")
        wclass = container.get("window_properties", {}).get("class", "")
        title = container.get("name", "")

        if change == "new" and window_id:
            workspace = get_focused_workspace()
            if workspace:
                key = str(window_id)
                mapping[key] = {
                    "workspace": workspace,
                    "class": wclass,
                    "title": title,
                    "time": time.time(),
                }
                save_mapping(mapping)
                print(f"  Tracked: {wclass} '{title}' -> {workspace}")

        elif change == "close" and window_id:
            key = str(window_id)
            if key in mapping:
                del mapping[key]
                save_mapping(mapping)

        elif change == "title" and window_id:
            # Update title (Chrome changes title as you navigate)
            key = str(window_id)
            if key in mapping:
                mapping[key]["title"] = title
                save_mapping(mapping)


if __name__ == "__main__":
    main()
