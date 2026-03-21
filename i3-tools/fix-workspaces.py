#!/usr/bin/env python3
"""
Fix wezterm workspace layout after CRD/VNC restart.

1. Query wezterm mux for all windows and derive project names from tab cwds
2. Kill any existing wezterm-gui processes (mux keeps all tabs safe)
3. Launch a new wezterm-gui
4. Distribute windows to separate temp workspaces (one each, for correct sizing)
5. Fullscreen-toggle each window to fix wezterm's rendering surface bug
6. Move to final named workspaces
7. Move any non-wezterm windows (emulator, etc.) to a separate workspace
"""

import json
import os
import re
import signal
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

DISPLAY = os.environ.get("DISPLAY", ":1")
ENV = {
    **os.environ,
    "DISPLAY": DISPLAY,
    "XAUTHORITY": os.path.expanduser("~/.Xauthority"),
}


def run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, env=ENV, **kwargs)


def i3cmd(command):
    result = run(["i3-msg", command])
    return result.stdout


TRACKER_FILE = Path.home() / ".cache" / "i3-window-workspaces.json"


def _get_pane_sizes():
    """Get all wezterm mux pane sizes as a list of (window_id, cols, rows)."""
    result = run(["wezterm", "cli", "--prefer-mux", "list", "--format", "json"])
    if result.returncode != 0:
        return []
    try:
        panes = json.loads(result.stdout)
        return [
            (p.get("window_id"), p.get("size", {}).get("cols", 999), p.get("size", {}).get("rows", 0)) for p in panes
        ]
    except (json.JSONDecodeError, KeyError):
        return []


def _any_narrow_panes(min_cols=40):
    """Check if any wezterm mux pane has fewer than min_cols columns."""
    return any(cols < min_cols for _, cols, _ in _get_pane_sizes())


def _count_narrow_panes(min_cols=40):
    """Count how many wezterm mux panes have fewer than min_cols columns."""
    return sum(1 for _, cols, _ in _get_pane_sizes() if cols < min_cols)


def _cycle_all_tabs(mux_windows):
    """Cycle through all tabs in every mux window to propagate rendering resize."""
    for wid, tabs in mux_windows.items():
        tab_ids = sorted(set(t["tab_id"] for t in tabs))
        if len(tab_ids) <= 1:
            continue
        first_pane = tabs[0]["pane_id"]
        for tab_id in tab_ids:
            run(
                [
                    "wezterm",
                    "cli",
                    "--prefer-mux",
                    "activate-tab",
                    "--tab-id",
                    str(tab_id),
                    "--pane-id",
                    str(first_pane),
                ]
            )
            time.sleep(0.2)
        # Return to first tab
        run(
            [
                "wezterm",
                "cli",
                "--prefer-mux",
                "activate-tab",
                "--tab-id",
                str(tab_ids[0]),
                "--pane-id",
                str(first_pane),
            ]
        )


def load_window_tracker():
    """Load the window-to-workspace mapping from the tracker daemon."""
    if TRACKER_FILE.exists():
        try:
            return json.loads(TRACKER_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def get_mux_windows():
    """Query wezterm mux for all windows and their tabs."""
    result = run(["wezterm", "cli", "--prefer-mux", "list", "--format", "json"])
    if result.returncode != 0:
        result = run(["wezterm", "cli", "list", "--format", "json"])
    if result.returncode != 0:
        print(f"ERROR: Cannot query wezterm mux: {result.stderr}")
        sys.exit(1)

    panes = json.loads(result.stdout)
    windows = defaultdict(list)
    for p in panes:
        cwd = re.sub(r"^file://[^/]*", "", p.get("cwd", ""))
        windows[p["window_id"]].append(
            {
                "tab_id": p["tab_id"],
                "pane_id": p["pane_id"],
                "cwd": cwd,
                "title": p.get("title", ""),
            }
        )
    return windows


def derive_project_name(tabs):
    """Derive a short project name from the cwds of all tabs in a window."""
    cwds = [t["cwd"] for t in tabs if t["cwd"]]
    if not cwds:
        return "misc"

    # Extract the first path component under ~/development/ for each tab
    home = str(Path.home())
    top_dirs = []
    sub_dirs = []
    for cwd in cwds:
        if cwd.startswith(home + "/development/"):
            rel = cwd[len(home + "/development/") :]
            parts = rel.strip("/").split("/")
            if parts[0]:
                top_dirs.append(parts[0])
            if len(parts) >= 2:
                sub_dirs.append(parts[0] + "/" + parts[1])
        elif cwd == home or cwd == home + "/":
            top_dirs.append("home")
        else:
            rel = cwd[len(home) :].strip("/") if cwd.startswith(home) else cwd
            top_dirs.append(rel.split("/")[0] if rel else "misc")

    if not top_dirs:
        return "misc"

    # If all tabs share the same top-level project dir, use that
    top_counter = Counter(top_dirs)
    top_name = top_counter.most_common(1)[0][0]

    # If there's only one top-level dir, check if all tabs are in the same subdir
    if len(top_counter) == 1 and sub_dirs:
        sub_counter = Counter(sub_dirs)
        # Only use the subdir name if ALL tabs are in the same subdir
        if len(sub_counter) == 1:
            return sub_counter.most_common(1)[0][0].split("/")[-1]

    return top_name


def kill_wezterm_guis():
    """Kill all wezterm-gui processes. Mux server keeps all tabs."""
    result = run(["pgrep", "-f", "wezterm-gui"])
    if result.stdout.strip():
        for pid in result.stdout.strip().split("\n"):
            pid = pid.strip()
            if pid:
                print(f"  Killing wezterm-gui pid {pid}")
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
        time.sleep(1)

    # Clean up stale sockets
    sock_dir = f"/run/user/{os.getuid()}/wezterm/"
    if os.path.isdir(sock_dir):
        for f in os.listdir(sock_dir):
            if f.startswith("gui-sock-"):
                try:
                    os.unlink(os.path.join(sock_dir, f))
                except OSError:
                    pass


def get_i3_windows():
    """Get all windows currently in i3, excluding i3bar."""
    result = run(["i3-msg", "-t", "get_tree"])
    tree = json.loads(result.stdout)

    windows = []

    def find_windows(node):
        if node.get("window"):
            wclass = node.get("window_properties", {}).get("class", "")
            # Skip i3bar
            if wclass == "i3bar":
                return
            windows.append(
                {
                    "con_id": node["id"],
                    "window_id": node["window"],
                    "title": node.get("name", ""),
                    "class": wclass,
                }
            )
        for child in node.get("nodes", []) + node.get("floating_nodes", []):
            find_windows(child)

    find_windows(tree)
    return windows


def match_windows_to_projects(wez_windows, mux_windows, window_names):
    """Match i3 wezterm windows to mux windows using tab count and title."""
    tab_count_to_mux = defaultdict(list)
    for wid, tabs in mux_windows.items():
        tab_count_to_mux[len(tabs)].append(wid)

    assigned = {}
    unassigned = []

    for w in wez_windows:
        title = w["title"]
        match = re.match(r"\[(\d+)/(\d+)\]", title)
        if match:
            tab_total = int(match.group(2))
            candidates = tab_count_to_mux.get(tab_total, [])
            if len(candidates) == 1:
                mux_wid = candidates[0]
                assigned[w["con_id"]] = window_names[mux_wid]
                print(f"  {title} -> {window_names[mux_wid]} (unique count {tab_total})")
            else:
                unassigned.append((w, tab_total, candidates))
        else:
            unassigned.append((w, 0, list(mux_windows.keys())))

    # Disambiguate by matching active tab title/cwd
    assigned_mux_names = set(assigned.values())
    for w, tab_total, candidates in unassigned:
        title = w["title"]
        best_name = None

        for mux_wid in candidates:
            name = window_names.get(mux_wid)
            if name in assigned_mux_names:
                continue
            tabs = mux_windows[mux_wid]
            for tab in tabs:
                if tab["title"] and tab["title"] in title:
                    best_name = name
                    break
                if tab["cwd"]:
                    cwd_parts = [p for p in tab["cwd"].split("/")[-2:] if p]
                    if any(part in title for part in cwd_parts):
                        best_name = name
                        break
            if best_name:
                break

        if not best_name:
            for mux_wid in candidates:
                if window_names[mux_wid] not in assigned_mux_names:
                    best_name = window_names[mux_wid]
                    break

        if not best_name:
            best_name = f"project_{len(assigned) + 1}"

        assigned[w["con_id"]] = best_name
        assigned_mux_names.add(best_name)
        print(f"  {title} -> {best_name} (matched)")

    return assigned


def main():
    print("=== Wezterm Workspace Fixer ===\n")

    # Step 1: Query mux for windows and derive names
    print("Step 1: Querying wezterm mux for windows...")
    mux_windows = get_mux_windows()
    window_names = {}
    for wid, tabs in sorted(mux_windows.items()):
        name = derive_project_name(tabs)
        window_names[wid] = name
        print(f"  Window {wid}: {name} ({len(tabs)} tabs)")

    num_wez_windows = len(window_names)
    print(f"\n  Total: {num_wez_windows} wezterm windows\n")

    if num_wez_windows == 0:
        print("No wezterm mux windows found. Nothing to do.")
        sys.exit(0)

    # Step 2: Kill existing GUIs
    print("Step 2: Killing existing wezterm-gui processes...")
    kill_wezterm_guis()
    print()

    # Step 3: Stash non-wezterm windows
    print("Step 3: Stashing non-wezterm windows...")
    existing = get_i3_windows()
    other_count = 0
    for w in existing:
        if w["class"] != "org.wezfurlong.wezterm":
            print(f"  Stashing {w['class']} '{w['title']}'")
            i3cmd(f'[con_id={w["con_id"]}] move to workspace "stash:other"')
            other_count += 1
    print()

    # Step 4: Launch wezterm GUI on a temp workspace
    print("Step 4: Launching wezterm GUI...")
    i3cmd('workspace "temp_launch"')
    time.sleep(0.5)

    proc = subprocess.Popen(
        ["wezterm", "connect", "unix", "--class", "org.wezfurlong.wezterm"],
        env=ENV,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    print("  Waiting for windows to appear...")
    for attempt in range(30):
        time.sleep(0.5)
        wins = [w for w in get_i3_windows() if w["class"] == "org.wezfurlong.wezterm"]
        if len(wins) >= num_wez_windows:
            break
    print(f"  Found {len(wins)} wezterm windows\n")

    # Step 5: Distribute to individual temp workspaces
    print("Step 5: Distributing to temp workspaces...")
    wez_windows = [w for w in get_i3_windows() if w["class"] == "org.wezfurlong.wezterm"]
    for i, w in enumerate(wez_windows):
        i3cmd(f'[con_id={w["con_id"]}] move to workspace "temp_{i}"')
    print(f"  Distributed {len(wez_windows)} windows")

    # Wait for resize
    time.sleep(1)

    # Step 5b: Fullscreen toggle each window to fix wezterm rendering surface bug.
    # Wezterm's software renderer locks the rendering surface to the initial pixel
    # width. Fullscreen toggle forces recalculation, but only for the active tab.
    # After toggling all windows, we cycle through ALL tabs to propagate the size.
    print("  Fixing rendering with fullscreen toggle + tab cycling...")
    num_wins = len(wez_windows)
    for pass_num in range(3):
        if pass_num > 0:
            print(f"    Retry pass {pass_num + 1}...")
        # Phase 1: Fullscreen toggle each window (fixes active tab rendering)
        for i in range(num_wins):
            i3cmd(f'workspace "temp_{i}"')
            time.sleep(0.5)
            i3cmd("fullscreen enable")
            time.sleep(1.5)
            i3cmd("fullscreen disable")
            time.sleep(1.0)
        # Phase 2: Cycle all tabs to propagate correct size to background tabs
        _cycle_all_tabs(mux_windows)
        # Verify: check if any mux pane is still narrow
        time.sleep(1)
        if not _any_narrow_panes():
            print(f"    All windows rendering correctly after pass {pass_num + 1}")
            break
        narrow_count = _count_narrow_panes()
        print(f"    After pass {pass_num + 1}: {narrow_count} narrow panes remain")
    print()

    # Step 6: Match windows to projects
    print("Step 6: Matching windows to projects...")
    wez_windows = [w for w in get_i3_windows() if w["class"] == "org.wezfurlong.wezterm"]
    assigned = match_windows_to_projects(wez_windows, mux_windows, window_names)
    print()

    # Step 7: Move to final named workspaces
    print("Step 7: Moving to final named workspaces...")
    name_counts = Counter(assigned.values())
    name_idx = defaultdict(int)

    for i, (con_id, name) in enumerate(sorted(assigned.items(), key=lambda x: x[1])):
        ws_num = i + 1
        if name_counts[name] > 1:
            name_idx[name] += 1
            ws_name = f"{ws_num}:{name}_{name_idx[name]}"
        else:
            ws_name = f"{ws_num}:{name}"
        print(f"  {ws_name}")
        i3cmd(f'[con_id={con_id}] move to workspace "{ws_name}"')

    # Build a map of workspace number -> workspace name for wezterm workspaces
    ws_name_map = {}  # "apmi" -> "1:apmi"
    for i, (con_id, name) in enumerate(sorted(assigned.items(), key=lambda x: x[1])):
        ws_num = i + 1
        if name_counts[name] > 1:
            ws_key = f"{name}_{name_idx.get(name, 1)}"
        else:
            ws_key = name
        ws_name_map[name] = f"{ws_num}:{ws_key}" if name_counts[name] > 1 else f"{ws_num}:{name}"

    # Move non-wezterm windows using tracker data, then fall back to "other"
    print("\nStep 8: Sorting non-wezterm windows...")
    other_windows = [w for w in get_i3_windows() if w["class"] != "org.wezfurlong.wezterm"]
    if other_windows:
        tracker_data = load_window_tracker()
        other_ws_num = len(assigned) + 1

        for w in other_windows:
            target_ws = None
            xid = str(w["window_id"])

            # Check tracker data for original workspace intent
            if xid in tracker_data:
                original_ws = tracker_data[xid].get("workspace", "")
                # Extract project name from workspace name (e.g., "3:unifi" -> "unifi")
                if ":" in original_ws:
                    project = original_ws.split(":", 1)[1]
                    # Find matching wezterm workspace
                    if project in ws_name_map:
                        target_ws = ws_name_map[project]
                        print(f"  {w['title'][:50]} -> {target_ws} (tracker: was on {original_ws})")

            if not target_ws:
                target_ws = f"{other_ws_num}:other"
                print(f"  {w['title'][:50]} -> {target_ws}")

            i3cmd(f'[con_id={w["con_id"]}] move to workspace "{target_ws}"')

    # Focus first workspace
    first_ws_name = sorted(set(assigned.values()))[0]
    i3cmd(f'workspace "1:{first_ws_name}"')

    print("\nDone!")


if __name__ == "__main__":
    main()
