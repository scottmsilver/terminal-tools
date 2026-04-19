# Gemini-CLI workspace namer

## Context

`agent-tools/i3-tools/workspace_namer.py` currently uses local Ollama (Gemma 3) to auto-name i3 workspaces based on what's running in each one. It is not wired to any keybinding or polybar button; it's only runnable on demand from a shell.

The user wants:

1. A polybar button that, when clicked, renames **all active i3 workspaces** in one shot.
2. The naming to come from the `gemini` CLI (Google Gemini) rather than Ollama.
3. Ollama removed from the project entirely.

The Gemini CLI (v0.35.1, at `~/.nvm/versions/node/v24.12.0/bin/gemini`) is already installed and authenticated. A probe confirms `gemini -p "<prompt>" -o text --approval-mode plan` returns clean JSON on stdout in ~14s on this box.

## Goals

- One-click rename of all non-empty i3 workspaces using the Gemini CLI as the LLM.
- Feedback via `notify-send` at start and on completion/failure.
- Graceful handling of missing binary, auth failure, malformed response, and timeout.
- Clean removal of Ollama code, deps, docs.

## Non-goals

- Background / automatic renaming (deferred — earlier decision was on-demand only).
- Screenshot / vision input. Text-only context keeps the prompt small and avoids figuring out the CLI's image-input path.
- Per-workspace rename buttons or per-workspace triggers.
- Provider abstraction / fallback to Ollama. Gemini CLI is the only backend.
- HTTP Gemini API integration. The CLI handles auth; we treat it as a subprocess.

## Architecture

```
┌──────────────────┐   click-left
│   polybar ✨     │─────────────────────┐
└──────────────────┘                     │
                                         ▼
                          ┌────────────────────────────────┐
                          │ ~/scripts/workspace_namer.py   │
                          │ (symlink into repo)            │
                          └────────────────────────────────┘
                                         │
       ┌─────────────────────────────────┼────────────────────────┐
       ▼                                 ▼                        ▼
 ┌───────────────┐               ┌───────────────┐        ┌──────────────┐
 │ gather_ctx    │               │ ask_gemini    │        │ apply_names  │
 │ (wezterm cli, │──build_prompt→│ subprocess.run│→parse→ │ i3ipc rename │
 │  git, i3ipc)  │               │ gemini -p ... │        │              │
 └───────────────┘               └───────────────┘        └──────────────┘
                                         │
                                         ▼
                                  notify-send start / done / error
```

## Components

All live in a single file, `agent-tools/i3-tools/workspace_namer.py`. The file is script-oriented (no package structure), matching the rest of the repo.

### `gather_context(i3) → dict[int, WorkspaceContext]`

Per active workspace:

- `git_repo`: canonical repo name derived from the focused wezterm pane's cwd (reuses existing `get_git_repo_name`, walking up to the git common dir). `None` when the pane isn't in a repo or is in `/tmp`.
- `wezterm_text`: last ~50 lines of the focused pane's scrollback (reuses existing `get_pane_text`). Cleaned of surrogate codepoints.
- `window_classes`: sorted unique list of X11 window classes in that workspace, for non-wezterm windows (browsers, editors, etc.).
- `current_name`: the current workspace name (to let the model preserve a good existing one if it wants).

Keys are workspace numbers. Empty workspaces are excluded.

### `build_prompt(contexts: dict) → str`

Returns a single prompt string. Shape:

```
You are naming i3 workspaces. For each workspace below, propose ONE short name
(1-3 words, lowercase, no emoji, no punctuation other than dashes) that describes
what is happening in it. Preserve a short existing name if it still fits.
Return ONLY a JSON object mapping workspace_number (string) to name (string).
No prose, no code fences.

Workspaces:
{ "1": { "git_repo": "...", "wezterm_text": "...", "window_classes": [...], "current_name": "..." }, ... }
```

The context dict is JSON-encoded into the prompt.

### `ask_gemini(prompt: str) → str`

- `subprocess.run(["gemini", "-p", prompt, "-o", "text", "--approval-mode", "plan"], capture_output=True, text=True, timeout=45)`.
- Returns `stdout` on exit 0. Raises a typed exception otherwise.
- `--approval-mode plan` ensures the CLI runs read-only — no tool invocation, no filesystem writes.
- Timeout: 45s (the probe took 14.5s; 3× gives headroom).

### `parse_response(stdout: str) → dict[str, str]`

The CLI sometimes emits banner lines (e.g. "Loaded cached credentials") that in the probe appeared on stderr but could leak to stdout in future versions. So we tolerate prefix/suffix text and extract the first `{...}` JSON object via a greedy match between the first `{` and the matching last `}`, then `json.loads`. If extraction fails or the result isn't a flat `str → str` dict of workspace numbers, raise a typed exception.

### `apply_names(i3, proposed: dict[str,str]) → list[Applied]`

For each `(number, name)`:

- Sanitize `name` (strip, collapse whitespace to single dashes, drop chars outside `[a-z0-9-]`, cap at 20 chars).
- Rename via `i3-msg rename workspace <number> to <number>: <name>` using existing `robust_rename`, which already handles duplicate-name conflicts.

Returns a list of `(number, old_name, new_name)` tuples for the notification.

### `main()`

```python
def main():
    try:
        notify("namer", "Naming workspaces with Gemini…")
        i3 = i3ipc.Connection()
        ctx = gather_context(i3)
        if not ctx:
            notify("namer", "No active workspaces to name.")
            return 0
        prompt = build_prompt(ctx)
        stdout = ask_gemini(prompt)
        proposed = parse_response(stdout)
        applied = apply_names(i3, proposed)
        notify("namer", summarize(applied))
        return 0
    except NamerError as e:
        notify("namer", f"Error: {e}", urgency="critical")
        return 1
```

No CLI arguments. The script always names everything when invoked.

## Error handling

| Condition | Detection | User sees |
|---|---|---|
| `gemini` not on PATH | `FileNotFoundError` from `subprocess.run` | `notify-send`: "gemini CLI not found on PATH" |
| Auth failure / non-zero exit | `returncode != 0` | `notify-send` with first 200 chars of stderr |
| Timeout (>45s) | `subprocess.TimeoutExpired` | `notify-send`: "Gemini timed out after 45s" |
| Malformed output | `parse_response` raises | `notify-send`: "Unexpected response from Gemini" |
| i3 rename conflict | `robust_rename` already handles dup-name retry | transparent |
| No wezterm running | `get_wez_state` returns `[]` → contexts still built from window classes / current name | works, degraded |

All errors exit with code 1 and a critical-urgency notification. No log files — the notification is the surface.

## Polybar integration

Append one module to `agent-tools/i3-tools/polybar/config.ini`:

```ini
[module/namer]
type = custom/text
content = ✨
content-foreground = ${colors.yellow}
click-left = ~/scripts/workspace_namer.py &
```

Add `sep namer` to `modules-right`, placed between `keys` and the tray so it sits next to the cheat-sheet button:

```ini
modules-right = cpu sep load sep memory sep filesystem sep date sep dpi-down dpi dpi-up sep keys sep namer
```

(polybar restart picks it up.)

## Installation wiring

- Symlink the script like other i3-tools scripts:
  `ln -sf ~/development/i3/agent-tools/i3-tools/workspace_namer.py ~/scripts/workspace_namer.py`
- Add this symlink line to `INSTALL.md`'s "Create symlinks" section.
- Ensure the file's shebang is `#!/usr/bin/env python3` and it is `chmod +x`.

## Repo hygiene (ride-along, scoped)

- `requirements.txt`: drop `requests` **if** nothing else in `agent-tools/i3-tools/` imports it. Verify with a grep before removal.
- `INSTALL.md`:
  - Remove the "install Ollama and pull a model" section (lines ~27–31).
  - Add `gemini` CLI as a prerequisite, with a pointer to `https://geminicli.com/` for install.
  - Add the workspace_namer.py symlink line.
  - Add a File-map row for `workspace_namer.py`.
- `README.md`: anywhere it says "Gemma 3 via Ollama", rewrite to "Gemini via the `gemini` CLI". Short rewrite only — no reformatting of unrelated sections.
- `CLAUDE.md` (project file at repo root): the line "AI-powered workspace naming (Gemma 3 via Ollama)" becomes "AI-powered workspace naming (Gemini via the `gemini` CLI)".

## Testing strategy

No test suite exists in this repo; verification is manual:

1. **CLI probe**: run `workspace_namer.py` directly from a shell with several active workspaces. Confirm a notification appears, workspaces are renamed, and the script exits 0.
2. **Error paths**:
   - Temporarily rename `~/.nvm/versions/node/v24.12.0/bin/gemini` to simulate "not on PATH" → expect the critical notification.
   - Pass `--approval-mode default` instead of `plan` with no other change and re-run; expect success (sanity that plan mode wasn't load-bearing for function, only for safety).
3. **Polybar click**: restart polybar, click `✨`, confirm the same outcome as (1). Log nothing; look at the notification stack only.
4. **Syntax check** (matches repo convention in `CLAUDE.md`):
   `python3 -c "import ast; ast.parse(open('agent-tools/i3-tools/workspace_namer.py').read())"`

## Rollout

Single PR-equivalent change (the user doesn't commit on my behalf; author will review and commit). No migration. No feature flag. Previous Ollama-based behavior is removed in the same change, since it has no current users beyond the author.
