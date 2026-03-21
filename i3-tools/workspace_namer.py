#!/usr/bin/env python3
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime

import i3ipc
import requests

# Configuration
DEFAULT_MODELS = ["gemma3:12b", "gemma3:4b"]
OLLAMA_URL = "http://localhost:11434/api/generate"
SHOT_DIR = "/tmp/i3_shots"

# Ensure output can handle emojis/unicode
try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass


def clean_text(text):
    if not text:
        return ""
    if isinstance(text, (bytes, bytearray)):
        text = text.decode("utf-8", "ignore")
    return re.sub(r"[\ud800-\udfff]", "", text)


def get_wez_state():
    try:
        res = subprocess.run(["wezterm", "cli", "list", "--format", "json"], capture_output=True, text=True)
        return json.loads(res.stdout)
    except Exception:
        return []


def get_focused_pane_id():
    try:
        res = subprocess.run(["wezterm", "cli", "list-clients", "--format", "json"], capture_output=True, text=True)
        return json.loads(res.stdout)[0].get("focused_pane_id")
    except Exception:
        return None


def get_pane_text(pane_id):
    try:
        res = subprocess.run(
            ["wezterm", "cli", "get-text", "--pane-id", str(pane_id), "--start-line", "-50"],
            capture_output=True,
            text=True,
        )
        return clean_text(res.stdout)
    except Exception:
        return ""


def get_git_repo_name(path):
    """Find the canonical root Git repository name by searching upwards."""
    try:
        path = os.path.realpath(path)

        # IGNORE temporary directories
        if path.startswith("/tmp") or path.startswith("/var/tmp"):
            return None

        curr = path
        while curr != "/":
            res = subprocess.run(["git", "-C", curr, "rev-parse", "--git-common-dir"], capture_output=True, text=True)
            if res.returncode == 0:
                common_dir = res.stdout.strip()
                if not os.path.isabs(common_dir):
                    common_dir = os.path.normpath(os.path.join(curr, common_dir))
                repo_root = os.path.dirname(common_dir)
                repo_name = os.path.basename(repo_root)
                if repo_name in ["backend", "frontend", "worktrees", "claude", ".claude", ".git"]:
                    parent_name = os.path.basename(os.path.dirname(repo_root))
                    if parent_name:
                        return parent_name
                return repo_name
            if os.path.exists(os.path.join(curr, ".git")):
                repo_name = os.path.basename(curr)
                if repo_name in ["backend", "frontend", "worktrees", "claude", ".claude"]:
                    return os.path.basename(os.path.dirname(curr))
                return repo_name
            curr = os.path.dirname(curr)

        # Absolute fallback for development folder
        if "/development/" in path:
            return path.split("/development/")[1].split("/")[0]

        return None  # Don't fall back to generic folder names
    except Exception:
        return None


def get_workspace_name(windows, workspace_num, repo_names):
    if not windows:
        return str(workspace_num)

    unique_repos = sorted(list(set(repo_names)))
    primary_repo = unique_repos[0] if unique_repos else "Unknown"

    window_info = json.dumps(windows, indent=2)
    prompt = f"""
    You are an expert organized developer naming i3wm workspaces.
    A good name helps the user quickly identify what project or task is in each workspace.
    Most of these are GitHub programming projects.

    GOAL: Create a short, descriptive workspace name.

    CONTEXT:
    - Likely Primary Project: {primary_repo}
    - Identified Repos: {', '.join(unique_repos) if unique_repos else 'None'}

    STRICT RULES:
    1. CONTENT: Return ONLY the Project/Repo name.
    2. NO generic words like "dev", "code", "work", "fix".
    3. NO EMOJIS. NO punctuation.
    4. STRICT LENGTH: MAX 20 CHARACTERS.

    WORKSPACE METADATA:
    {window_info}

    Return ONLY the name.
    """

    screenshot_b64 = None
    shot_path = f"{SHOT_DIR}/{workspace_num}.jpg"
    if os.path.exists(shot_path):
        with open(shot_path, "rb") as f:
            screenshot_b64 = base64.b64encode(f.read()).decode("utf-8")

    for model in DEFAULT_MODELS:
        try:
            payload = {
                "model": model,
                "prompt": prompt,
                "images": [screenshot_b64] if screenshot_b64 else [],
                "stream": False,
                "options": {"temperature": 0.1, "num_ctx": 32768},
            }
            # Save debug payload
            with open(f"payload_{workspace_num}.json", "w") as f:
                json.dump(payload, f)

            res = requests.post(OLLAMA_URL, json=payload, timeout=30)
            if res.status_code == 200:
                text = re.sub(r"<[^>]+>", "", res.json().get("response", "")).strip()
                for line in text.split("\n"):
                    line = line.strip().strip("*").strip()
                    if line and "Reasoning" not in line and "Analysis" not in line:
                        line = clean_text(line)
                        if len(line) > 20:
                            idx = max(line.rfind(" ", 0, 21), line.rfind("-", 0, 21))
                            line = line[:idx] if idx > 5 else line[:20]
                        return line
        except Exception:
            continue
    return str(workspace_num)


def robust_rename(i3, ws_num, proposed_name):
    target_name = f"{ws_num}: {proposed_name}" if proposed_name != str(ws_num) else str(ws_num)
    tree = i3.get_tree()
    nodes = [n for n in tree.workspaces() if n.num == ws_num]
    if not nodes:
        return
    for node in nodes:
        for leaf in node.leaves():
            i3.command(f'[con_id="{leaf.id}"] move container to workspace "{target_name}"')


def main():
    parser = argparse.ArgumentParser(description="AI-powered i3 workspace namer.")
    parser.parse_args()  # For future args

    i3 = i3ipc.Connection()
    active_workspaces = i3.get_workspaces()
    original_ws = next(ws for ws in active_workspaces if ws.focused).name

    print("Mapping workspaces via Focus Probe...")
    ws_to_wez_window = {}
    tree = i3.get_tree()
    for ws in sorted(active_workspaces, key=lambda x: x.num):
        ws_node = next((n for n in tree.workspaces() if n.num == ws.num), None)
        if not ws_node or not any("wezterm" in (l.window_class or "").lower() for l in ws_node.leaves()):
            continue
        i3.command(f"workspace {ws.name}")
        time.sleep(0.15)
        focused_p = get_focused_pane_id()
        for p in get_wez_state():
            if p["pane_id"] == focused_p:
                ws_to_wez_window[ws.num] = p["window_id"]
                break
    i3.command(f"workspace {original_ws}")

    wez_panes = get_wez_state()
    for ws in sorted(active_workspaces, key=lambda x: x.num):
        try:
            print(f"Processing Workspace {ws.num}...")
            ws_node = next((n for n in i3.get_tree().workspaces() if n.num == ws.num), None)
            windows, repo_names = [], []
            wid = ws_to_wez_window.get(ws.num)

            if ws_node:
                for leaf in ws_node.leaves():
                    win_data = {"class": clean_text(leaf.window_class), "title": clean_text(leaf.name)}
                    # Heuristic for non-terminals
                    title_match = re.match(r"^([\w-]+)\s*[\u2014-]", win_data["title"])
                    if title_match:
                        repo_names.append(title_match.group(1))

                    if wid is not None and "wezterm" in (leaf.window_class or "").lower():
                        for p in wez_panes:
                            if p["window_id"] == wid and p.get("is_active") and p.get("cwd"):
                                path = re.sub(r"^file://[^/]*", "", p["cwd"]).rstrip("/")
                                repo = get_git_repo_name(path)
                                if repo:
                                    repo_names.append(repo)
                                win_data["terminal_text"] = get_pane_text(p["pane_id"])
                    windows.append(win_data)

            new_name = get_workspace_name(windows, ws.num, list(set(repo_names)))
            print(f"[{ws.num}] Final Name: {new_name}")
            robust_rename(i3, ws.num, new_name)
        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    main()
