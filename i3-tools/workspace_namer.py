#!/usr/bin/env python3
"""Rename i3 workspaces using the Gemini CLI based on what each contains.

Invoked on-demand (e.g. via a polybar click). No args. Exits 0 on success,
1 on any error; the error is surfaced as a critical `notify-send`.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Any

import i3ipc

GEMINI_TIMEOUT_SECONDS = 45
PANE_SCROLLBACK_LINES = 50
PANE_TEXT_CAP_CHARS = 4000
SANITIZED_NAME_MAX = 20


class NamerError(Exception):
    """User-facing error; message goes straight into a notification."""


def clean_text(text: str | bytes | None) -> str:
    if not text:
        return ""
    if isinstance(text, (bytes, bytearray)):
        text = text.decode("utf-8", "ignore")
    return re.sub(r"[\ud800-\udfff]", "", text)


def notify(summary: str, body: str, urgency: str = "normal") -> None:
    try:
        subprocess.run(
            ["notify-send", "-u", urgency, "-a", "workspace-namer", summary, body],
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


def _wez_list() -> list[dict[str, Any]]:
    try:
        res = subprocess.run(
            ["wezterm", "cli", "list", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            return json.loads(res.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    return []


def _wez_focused_pane_id() -> int | None:
    try:
        res = subprocess.run(
            ["wezterm", "cli", "list-clients", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            clients = json.loads(res.stdout)
            if clients:
                return clients[0].get("focused_pane_id")
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    return None


def _wez_pane_text(pane_id: int) -> str:
    try:
        res = subprocess.run(
            ["wezterm", "cli", "get-text", "--pane-id", str(pane_id), "--start-line", f"-{PANE_SCROLLBACK_LINES}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0:
            return clean_text(res.stdout)[-PANE_TEXT_CAP_CHARS:]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return ""


def _git_repo_name(path: str | None) -> str | None:
    if not path:
        return None
    try:
        path = os.path.realpath(path)
    except OSError:
        return None
    if path.startswith("/tmp") or path.startswith("/var/tmp"):
        return None
    curr = path
    while curr and curr != "/":
        try:
            res = subprocess.run(
                ["git", "-C", curr, "rev-parse", "--git-common-dir"],
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if res.returncode == 0:
            common_dir = res.stdout.strip()
            if not os.path.isabs(common_dir):
                common_dir = os.path.normpath(os.path.join(curr, common_dir))
            repo_root = os.path.dirname(common_dir)
            return os.path.basename(repo_root) or None
        curr = os.path.dirname(curr)
    return None


def _pane_cwd(pane: dict[str, Any]) -> str | None:
    cwd = pane.get("cwd") or ""
    if cwd.startswith("file://"):
        cwd = cwd[len("file://") :]
        if cwd.startswith("/"):
            pass
        else:
            idx = cwd.find("/")
            cwd = cwd[idx:] if idx != -1 else ""
    return cwd or None


def gather_context(i3: i3ipc.Connection) -> dict[int, dict[str, Any]]:
    """Per-workspace: current name, window classes/titles, best-effort wezterm text + git repo."""
    tree = i3.get_tree()
    panes = _wez_list()
    focused_pane_id = _wez_focused_pane_id()

    # A given wezterm pane must not be assigned to more than one workspace,
    # otherwise workspaces whose window titles collide (e.g. two shells named
    # "zsh") all get the same context and Gemini names them identically.
    used_pane_ids: set[int] = set()
    ctx: dict[int, dict[str, Any]] = {}
    for ws in tree.workspaces():
        leaves = list(ws.leaves())
        if not leaves:
            continue
        classes = sorted({l.window_class for l in leaves if l.window_class})
        titles = [l.name for l in leaves if l.name]

        chosen_pane: dict[str, Any] | None = None
        has_wezterm = any(
            (c or "").lower().startswith("org.wezfurlong") or "wezterm" in (c or "").lower() for c in classes
        )
        if has_wezterm:
            for title in titles:
                for p in panes:
                    pid = p.get("pane_id")
                    if pid in used_pane_ids:
                        continue
                    if p.get("title") and title and p["title"] in title:
                        chosen_pane = p
                        break
                if chosen_pane:
                    break
            # Only the focused workspace may consume the globally focused pane
            # as a fallback. Otherwise the first workspace iterated (usually
            # workspace 1) would steal scrollback from whichever workspace the
            # user is actually in.
            if not chosen_pane and getattr(ws, "focused", False) and focused_pane_id not in used_pane_ids:
                chosen_pane = next((p for p in panes if p.get("pane_id") == focused_pane_id), None)

        wezterm_text = ""
        git_repo = None
        if chosen_pane:
            pid = chosen_pane.get("pane_id")
            if isinstance(pid, int):
                used_pane_ids.add(pid)
                wezterm_text = _wez_pane_text(pid)
            git_repo = _git_repo_name(_pane_cwd(chosen_pane))

        # Strip the "N:" prefix from the workspace name so the model sees only
        # the human-chosen intent. Otherwise the model can "preserve" the whole
        # "1: foo" string and robust_rename prepends the number again, yielding
        # "1: 1-foo" — non-idempotent across repeated runs.
        current_intent = re.sub(r"^\s*\d+\s*:?\s*", "", ws.name or "").strip()
        ctx[ws.num] = {
            "current_name": current_intent,
            "window_classes": classes,
            "window_titles": titles[:8],
            "git_repo": git_repo,
            "wezterm_text": wezterm_text,
        }
    return ctx


def build_prompt(contexts: dict[int, dict[str, Any]]) -> str:
    body = {str(k): v for k, v in contexts.items()}
    return (
        "You are naming i3 workspaces. For each workspace below, propose ONE short "
        "name (1-3 words, lowercase, no emoji, no punctuation other than dashes) "
        "that describes what is happening in it. Preserve a short existing name if "
        "it still fits the content. Return ONLY a JSON object mapping workspace_number "
        "(string) to name (string). No prose, no code fences.\n\n"
        f"Workspaces:\n{json.dumps(body, ensure_ascii=False)}"
    )


def _nvm_version_key(name: str) -> tuple[int, ...]:
    # Parse an nvm directory name like "v24.12.0" into (24, 12, 0) for numeric
    # sorting. Non-numeric components collapse to -1 so weird entries sort
    # last. Lexicographic sort would rank "v9.11.0" above "v24.12.0".
    try:
        return tuple(int(p) for p in name.lstrip("v").split("."))
    except ValueError:
        return (-1,)


def _find_gemini() -> str:
    # Prefer nvm-managed installs (newest version first) over anything on PATH.
    # Rationale: a stale system-wide gemini may exist alongside a current nvm one;
    # polybar's minimal PATH hits the system one first and it may require a newer
    # Node than /usr/bin/node provides.
    nvm_root = os.path.expanduser("~/.nvm/versions/node")
    if os.path.isdir(nvm_root):
        for version in sorted(os.listdir(nvm_root), key=_nvm_version_key, reverse=True):
            candidate = os.path.join(nvm_root, version, "bin", "gemini")
            if os.access(candidate, os.X_OK):
                return candidate
    for part in os.environ.get("PATH", "").split(os.pathsep):
        candidate = os.path.join(part, "gemini")
        if os.access(candidate, os.X_OK):
            return candidate
    raise NamerError("gemini CLI not found in ~/.nvm or on PATH")


def ask_gemini(prompt: str) -> str:
    binary = _find_gemini()
    # The gemini script's shebang is `#!/usr/bin/env -S node ...`, so the kernel
    # re-resolves `node` through PATH. If the caller (e.g. polybar) has a minimal
    # PATH that finds a stale /usr/bin/node, the CLI crashes on ES2024 regex
    # syntax. Prepend the bin dir of the chosen gemini so its sibling node wins.
    env = os.environ.copy()
    env["PATH"] = os.path.dirname(binary) + os.pathsep + env.get("PATH", "")
    try:
        res = subprocess.run(
            [binary, "-p", prompt, "-o", "text", "--approval-mode", "plan"],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=GEMINI_TIMEOUT_SECONDS,
            env=env,
        )
    except FileNotFoundError:
        raise NamerError("gemini CLI not found on PATH")
    except subprocess.TimeoutExpired:
        raise NamerError(f"Gemini timed out after {GEMINI_TIMEOUT_SECONDS}s")
    if res.returncode != 0:
        err_full = (res.stderr or res.stdout or "").strip()
        print(f"--- gemini exit {res.returncode} full stderr ---\n{err_full}\n--- end ---", file=sys.stderr, flush=True)
        first = err_full.splitlines()[0] if err_full else ""
        raise NamerError(f"gemini exited {res.returncode}: {first[:200]}")
    return res.stdout


def parse_response(stdout: str) -> dict[str, str]:
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start == -1 or end <= start:
        raise NamerError("No JSON object in Gemini response")
    try:
        data = json.loads(stdout[start : end + 1])
    except json.JSONDecodeError as e:
        raise NamerError(f"Malformed JSON from Gemini: {e}")
    if not isinstance(data, dict):
        raise NamerError("Gemini response is not a JSON object")
    out: dict[str, str] = {}
    for k, v in data.items():
        if not (isinstance(k, str) and k.lstrip("-").isdigit() and isinstance(v, str)):
            raise NamerError("Gemini response keys/values have wrong types")
        out[k] = v
    return out


def sanitize(name: str) -> str:
    s = (name or "").strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:SANITIZED_NAME_MAX]


def robust_rename(i3: i3ipc.Connection, ws_num: int, proposed: str) -> str:
    all_ws = i3.get_workspaces()
    current = next((w.name for w in all_ws if w.num == ws_num), None)
    if current is None:
        return ""
    target = f"{ws_num}: {proposed}"
    existing = {w.name for w in all_ws if w.num != ws_num}
    if target in existing:
        for i in range(2, 10):
            candidate = f"{ws_num}: {proposed}-{i}"
            if candidate not in existing:
                target = candidate
                break
    if current == target:
        return target
    i3.command(f'rename workspace "{current}" to "{target}"')
    return target


def apply_names(i3: i3ipc.Connection, proposed: dict[str, str]) -> list[tuple[int, str, str]]:
    current_names = {w.num: w.name for w in i3.get_workspaces()}
    applied: list[tuple[int, str, str]] = []
    for num_str, raw in proposed.items():
        try:
            ws_num = int(num_str)
        except ValueError:
            continue
        name = sanitize(raw)
        if not name or ws_num not in current_names:
            continue
        old = current_names[ws_num]
        new = robust_rename(i3, ws_num, name)
        if new:
            applied.append((ws_num, old, new))
    return applied


def summarize(applied: list[tuple[int, str, str]]) -> str:
    if not applied:
        return "No workspaces renamed."
    return "\n".join(new for _num, _old, new in applied)


def main() -> int:
    try:
        notify("workspace-namer", "Naming workspaces with Gemini…")
        i3 = i3ipc.Connection()
        ctx = gather_context(i3)
        if not ctx:
            notify("workspace-namer", "No active workspaces to name.")
            return 0
        prompt = build_prompt(ctx)
        stdout = ask_gemini(prompt)
        proposed = parse_response(stdout)
        applied = apply_names(i3, proposed)
        notify("workspace-namer", summarize(applied))
        return 0
    except NamerError as e:
        notify("workspace-namer", f"Error: {e}", urgency="critical")
        return 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    sys.exit(main())
