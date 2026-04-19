# Gemini-CLI workspace namer — implementation plan

> **For agentic workers:** Follow tasks in order. Checkbox syntax tracks progress.
>
> **Commit policy for this repo:** The user's global CLAUDE.md forbids committing without explicit permission. Do NOT run `git commit`/`git push` at any point in this plan. Stage changes with `git add` when the task says so; the user will review and commit on their own. The tasks below group related changes so the final staged diff makes sense as one commit.

**Goal:** Replace the Ollama-based workspace namer with one that shells out to the Gemini CLI, and expose it as a polybar ✨ button that names all active i3 workspaces on click.

**Architecture:** Single Python script (`workspace_namer.py`) gathers per-workspace context via `i3ipc` + `wezterm cli` + git, builds one JSON-in/JSON-out prompt, runs `gemini -p` as a subprocess, parses the response, and renames via `i3ipc`. User feedback via `notify-send` at start / finish / error. No HTTP, no Ollama.

**Tech Stack:** Python 3.10+, `i3ipc`, `subprocess` (for `gemini`, `wezterm`, `git`, `notify-send`), polybar 3.7+. No new dependencies.

**Spec:** `agent-tools/docs/superpowers/specs/2026-04-18-gemini-workspace-namer-design.md`

**Verification style for this repo:** No test suite exists (see project `CLAUDE.md`). Use the repo's established pattern: `ast.parse` for syntax, then manual invocation + observed behavior. Error paths are simulated by temporarily shadowing the `gemini` binary on PATH.

---

## Task 1: Rewrite `workspace_namer.py` for Gemini CLI

**Files:**
- Modify (full rewrite): `agent-tools/i3-tools/workspace_namer.py`

The existing file is 238 lines of Ollama-flavored code. We replace it entirely.

- [ ] **Step 1: Read the existing file top-to-bottom to confirm which helpers we're keeping vs dropping.**

Run: `cat agent-tools/i3-tools/workspace_namer.py | sed -n '1,240p'`

Expected helpers to preserve in spirit (names may change): `clean_text`, `get_wez_state`, `get_focused_pane_id`, `get_pane_text`, `get_git_repo_name`, `robust_rename`.

Expected code to drop: `OLLAMA_URL`, `DEFAULT_MODELS`, `SHOT_DIR`, anything under `base64`/screenshot/POST paths, `requests` import.

- [ ] **Step 2: Overwrite `agent-tools/i3-tools/workspace_namer.py` with this content:**

```python
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


# ---------- helpers (lifted and tightened from previous version) ----------

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
            check=False, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


def _wez_list() -> list[dict[str, Any]]:
    try:
        res = subprocess.run(
            ["wezterm", "cli", "list", "--format", "json"],
            capture_output=True, text=True, timeout=5,
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
            capture_output=True, text=True, timeout=5,
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
            ["wezterm", "cli", "get-text",
             "--pane-id", str(pane_id),
             "--start-line", f"-{PANE_SCROLLBACK_LINES}"],
            capture_output=True, text=True, timeout=5,
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
                capture_output=True, text=True, timeout=2,
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
        cwd = cwd[len("file://"):]
        # strip optional hostname segment between // and the first /
        if cwd.startswith("/"):
            pass
        else:
            idx = cwd.find("/")
            cwd = cwd[idx:] if idx != -1 else ""
    return cwd or None


# ---------- context gathering ----------

def gather_context(i3: i3ipc.Connection) -> dict[int, dict[str, Any]]:
    """Per-workspace: current name, window classes, best-effort wezterm text + git repo.

    We can't reliably map an X11 wezterm window to a specific wezterm pane_id,
    so for each workspace that contains a wezterm window we pick the wezterm
    pane whose title matches the X11 window title; we fall back to the globally
    focused wezterm pane if nothing matches.
    """
    tree = i3.get_tree()
    panes = _wez_list()
    focused_pane_id = _wez_focused_pane_id()
    focused_pane = next((p for p in panes if p.get("pane_id") == focused_pane_id), None)

    ctx: dict[int, dict[str, Any]] = {}
    for ws in tree.workspaces():
        leaves = list(ws.leaves())
        if not leaves:
            continue
        classes = sorted({l.window_class for l in leaves if l.window_class})
        titles = [l.name for l in leaves if l.name]

        chosen_pane: dict[str, Any] | None = None
        has_wezterm = any((c or "").lower().startswith("org.wezfurlong") or "wezterm" in (c or "").lower() for c in classes)
        if has_wezterm:
            for title in titles:
                for p in panes:
                    if p.get("title") and title and p["title"] in title:
                        chosen_pane = p
                        break
                if chosen_pane:
                    break
            if not chosen_pane and focused_pane:
                chosen_pane = focused_pane

        wezterm_text = ""
        git_repo = None
        if chosen_pane:
            pid = chosen_pane.get("pane_id")
            if isinstance(pid, int):
                wezterm_text = _wez_pane_text(pid)
            git_repo = _git_repo_name(_pane_cwd(chosen_pane))

        ctx[ws.num] = {
            "current_name": ws.name,
            "window_classes": classes,
            "window_titles": titles[:8],  # cap to keep prompt bounded
            "git_repo": git_repo,
            "wezterm_text": wezterm_text,
        }
    return ctx


# ---------- prompt / LLM ----------

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


def ask_gemini(prompt: str) -> str:
    try:
        res = subprocess.run(
            ["gemini", "-p", prompt, "-o", "text", "--approval-mode", "plan"],
            capture_output=True, text=True, timeout=GEMINI_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        raise NamerError("gemini CLI not found on PATH")
    except subprocess.TimeoutExpired:
        raise NamerError(f"Gemini timed out after {GEMINI_TIMEOUT_SECONDS}s")
    if res.returncode != 0:
        err = (res.stderr or res.stdout or "").strip().splitlines()
        first = err[0] if err else ""
        raise NamerError(f"gemini exited {res.returncode}: {first[:200]}")
    return res.stdout


def parse_response(stdout: str) -> dict[str, str]:
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start == -1 or end <= start:
        raise NamerError("No JSON object in Gemini response")
    try:
        data = json.loads(stdout[start:end + 1])
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


# ---------- applying names ----------

def sanitize(name: str) -> str:
    s = (name or "").strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:SANITIZED_NAME_MAX]


def robust_rename(i3: i3ipc.Connection, ws_num: int, proposed: str) -> str:
    """Rename workspace ws_num to '<num>: <proposed>'; dedup if needed."""
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
    # i3 rename syntax: rename workspace "<old>" to "<new>"
    i3.command(f'rename workspace "{current}" to "{target}"')
    return target


def apply_names(i3: i3ipc.Connection,
                proposed: dict[str, str]) -> list[tuple[int, str, str]]:
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
    return "\n".join(f"{num}: {new}" for num, _old, new in applied)


# ---------- main ----------

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
```

- [ ] **Step 3: Make executable and syntax-check.**

Run:
```
chmod +x agent-tools/i3-tools/workspace_namer.py
python3 -c "import ast; ast.parse(open('agent-tools/i3-tools/workspace_namer.py').read())"
```
Expected: no output from either command (clean syntax).

- [ ] **Step 4: Confirm no `requests` / `base64` / `OLLAMA_URL` references remain.**

Run:
```
grep -nE 'requests|base64|OLLAMA|SHOT_DIR' agent-tools/i3-tools/workspace_namer.py || echo "clean"
```
Expected output: `clean`

---

## Task 2: Install symlink from `~/scripts` into the repo

The repo is the source of truth (see `INSTALL.md`). Other scripts follow this pattern.

**Files:**
- Symlink: `~/scripts/workspace_namer.py` → `~/development/i3/agent-tools/i3-tools/workspace_namer.py`

- [ ] **Step 1: Create the symlink.**

Run:
```
ln -sfn /home/ssilver/development/i3/agent-tools/i3-tools/workspace_namer.py /home/ssilver/scripts/workspace_namer.py
```

- [ ] **Step 2: Verify.**

Run:
```
ls -la /home/ssilver/scripts/workspace_namer.py
readlink /home/ssilver/scripts/workspace_namer.py
```
Expected: symlink pointing at the repo file.

---

## Task 3: End-to-end dry run from the shell

Validates the Python path end-to-end before polybar is involved, so failure modes are easy to read.

- [ ] **Step 1: Run the script directly.**

Run:
```
/home/ssilver/scripts/workspace_namer.py; echo "exit=$?"
```
Expected:
- A "Naming workspaces with Gemini…" desktop notification at start.
- A completion notification ~15–30s later listing the new names.
- `exit=0`.
- `i3-msg -t get_workspaces | jq -r '.[].name'` should now show LLM-proposed names formatted as `<num>: <name>`.

- [ ] **Step 2: Simulate `gemini` missing by shadowing PATH for one invocation.**

Run:
```
env PATH=/usr/bin:/bin /home/ssilver/scripts/workspace_namer.py; echo "exit=$?"
```
Expected:
- A critical notification "Error: gemini CLI not found on PATH".
- `exit=1`.

- [ ] **Step 3: Simulate malformed response by feeding an unrelated prompt via a one-off wrapper.**

Run:
```
python3 - <<'PY'
import sys
sys.path.insert(0, '/home/ssilver/scripts')
from workspace_namer import parse_response, NamerError
try:
    parse_response("not json at all")
except NamerError as e:
    print("OK:", e); raise SystemExit(0)
print("FAIL: parse_response did not raise")
raise SystemExit(1)
PY
```
Expected: `OK: No JSON object in Gemini response`.

---

## Task 4: Polybar module

**Files:**
- Modify: `agent-tools/i3-tools/polybar/config.ini` (installed at `~/.config/polybar/config.ini` via symlink)

- [ ] **Step 1: Append the module definition to the end of the file.**

Append:
```ini

[module/namer]
type = custom/text
content = ✨
content-foreground = ${colors.yellow}
click-left = ~/scripts/workspace_namer.py &
```

- [ ] **Step 2: Add `sep namer` to the `modules-right` line.**

Change the line
```
modules-right = cpu sep load sep memory sep filesystem sep date sep dpi-down dpi dpi-up sep keys
```
to
```
modules-right = cpu sep load sep memory sep filesystem sep date sep dpi-down dpi dpi-up sep keys sep namer
```

- [ ] **Step 3: Reload polybar.**

Run:
```
polybar-msg cmd restart
```
Expected: "Successfully wrote command 'restart' to PID <pid>". No new errors in `/tmp/polybar.log` (check with `tail -20 /tmp/polybar.log`).

- [ ] **Step 4: Click the ✨ button once and confirm same behavior as Task 3 Step 1.**

Expected:
- Start notification, completion notification ~15–30s later.
- Polybar workspace labels update to the new names.

---

## Task 5: Docs — drop Ollama, describe Gemini CLI

**Files:**
- Modify: `agent-tools/i3-tools/INSTALL.md`
- Modify: `agent-tools/i3-tools/README.md`
- Modify: `CLAUDE.md` (repo root — the project file, not `~/.claude/CLAUDE.md`)

- [ ] **Step 1: `INSTALL.md` — remove the Ollama block.**

Delete these lines (currently after the apt install + pip install block):
```
For the workspace namer's AI features, install Ollama and pull a model:
\`\`\`
# Install Ollama from https://ollama.ai
ollama pull gemma3:12b
\`\`\`
```

Replace them with:
```
For the workspace namer's AI features, install the Gemini CLI and authenticate
it once interactively:
\`\`\`
npm install -g @google/gemini-cli    # or follow https://geminicli.com/
gemini                                # first run prompts for auth; afterward it caches credentials
\`\`\`
```

- [ ] **Step 2: `INSTALL.md` — add the symlink line for `workspace_namer.py`.**

In the "Create symlinks" section, directly after the `i3-window-tracker.py` symlink line, add:
```
ln -sf ~/development/i3/agent-tools/i3-tools/workspace_namer.py ~/scripts/workspace_namer.py
```

- [ ] **Step 3: `INSTALL.md` — add a file-map row.**

In the "File map" table, after the `i3-window-tracker.py` row, insert:
```
| `workspace_namer.py` | `~/scripts/workspace_namer.py` | AI workspace naming via the `gemini` CLI; invoked by the polybar ✨ button |
```
Also change the existing `workspace_namer.py` row (it currently reads `| (run directly) | AI workspace naming via Ollama |`) — remove that row since it's now duplicative with the new one.

- [ ] **Step 4: `README.md` — replace Ollama phrasing with Gemini CLI.**

Run:
```
grep -n 'Ollama\|Gemma\|gemma' agent-tools/i3-tools/README.md
```
For each match, rewrite the sentence to describe the Gemini CLI path instead. Keep the change surgical — no reformatting of surrounding sections. The canonical replacement phrasing is: "AI workspace naming via the `gemini` CLI (invoked by the polybar ✨ button)".

- [ ] **Step 5: Project `CLAUDE.md` — same rewrite.**

Run:
```
grep -n 'Ollama\|Gemma' CLAUDE.md
```
Replace every occurrence of "Gemma 3 via Ollama" with "Gemini via the `gemini` CLI", and drop any Ollama-specific install line.

- [ ] **Step 6: Verify no stray Ollama references remain in the working tree.**

Run:
```
grep -rniE 'ollama|gemma3' agent-tools/i3-tools/ CLAUDE.md README.md 2>/dev/null || echo "clean"
```
Expected: `clean`. Historical references inside `agent-tools/docs/superpowers/specs/` or `plans/` are fine to keep — they are the design record.

---

## Task 6: `requirements.txt` — leave as-is

`requests` is still used by `test_visual.py` (grep confirmed). Do **not** remove it.

- [ ] **Step 1: Confirm no edits are needed.**

Run:
```
grep -rln 'import requests\|from requests' agent-tools/i3-tools/
```
Expected: only `agent-tools/i3-tools/test_visual.py`.
If that expectation is wrong at the time you execute this plan, follow up: remove `requests` from `requirements.txt` only when no file in `agent-tools/i3-tools/` still imports it.

---

## Task 7: Stage for review

- [ ] **Step 1: Show the staged diff.**

Run:
```
cd /home/ssilver/development/i3/agent-tools
git add i3-tools/workspace_namer.py i3-tools/polybar/config.ini i3-tools/INSTALL.md i3-tools/README.md CLAUDE.md docs/superpowers/specs/2026-04-18-gemini-workspace-namer-design.md docs/superpowers/plans/2026-04-18-gemini-workspace-namer.md
git status
git diff --staged --stat
```
Expected: the seven files above are staged, nothing else.

- [ ] **Step 2: Stop. Hand back to the user.**

Do not commit. Report: "Implementation complete and staged. Let me know when to commit."

---

## Spec → plan coverage check

| Spec requirement | Where implemented |
|---|---|
| Polybar ✨ button triggers name-all | Task 4 |
| `gather_context` with git_repo / wezterm_text / window_classes / current_name | Task 1, `gather_context` |
| `build_prompt` with JSON-in/JSON-out instructions | Task 1, `build_prompt` |
| `ask_gemini` subprocess with 45s timeout + plan mode | Task 1, `ask_gemini` |
| `parse_response` tolerates prefix/suffix text | Task 1, `parse_response` |
| `apply_names` via `robust_rename`, sanitized | Task 1, `apply_names` + `sanitize` |
| Error surface via `notify-send` (start / done / error) | Task 1, `notify` + `main` |
| Timeout 45s + error rows in spec table | Task 1 constants + `ask_gemini` raises |
| Polybar module wired into `modules-right` | Task 4 step 2 |
| Symlink `~/scripts/workspace_namer.py` | Task 2 |
| `INSTALL.md` doc updates (Ollama out, Gemini in, symlink, file map) | Task 5 steps 1–3 |
| `README.md` + project `CLAUDE.md` doc updates | Task 5 steps 4–5 |
| `requirements.txt` audit | Task 6 |
| Manual verification path (no test suite) | Task 3 + Task 4 step 4 |
