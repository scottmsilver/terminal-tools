#!/usr/bin/env python3
"""Hybrid workspace namer: text context + vision per workspace.

Combines the text-based namer's structured context (git_repos, wezterm
pane scrollback, window classes/titles) with a screenshot of each
workspace. Sends both to Gemini's multimodal model in one call and
asks for THREE candidate names per workspace. **Does not rename
anything** — candidates only; you pick.

Why hybrid: vision-alone misreads workspaces because the dominant
on-screen content is whatever the user was actively reading (often an
AI chat session) rather than what the workspace is "about". Text-alone
misses signals only the screen carries — browser tab titles, app UIs,
icons. Combining them lets each compensate for the other's blind spot.

Side effects (visible to the user):
- Workspace cycling: i3-msg switches between workspaces during capture.
  ~120 ms per workspace; total ~1 s for a typical 7-workspace desktop.
  Returns to the originally-focused workspace at the end.
- Dunst toast (replace-by-id) shows progress: "capturing 3/7: ws3".
- Final toast announces "calling gemini" then the candidate JSON.

Why we save screenshots inside the repo's .cache/ instead of /tmp:
Gemini's CLI only allows file reads from its workspace dir or its
project temp dir. Writing into a path under the cwd it's invoked from
(here: the agent-tools repo) is the simplest way to make `@path/to.png`
references resolve. The .cache/ dir is gitignored.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import i3ipc

# Sibling import: gather_context() does the text-side work for us
# (per-workspace git_repos, wezterm pane scrollback, etc.) — keeps the
# text→prompt logic in a single place.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import workspace_namer  # noqa: E402  (path mutation precedes import; intentional)

_ = workspace_namer  # keep formatter from stripping the import as unused

REPO_ROOT = Path("/home/ssilver/development/i3/agent-tools")
SHOTS_DIR = REPO_ROOT / ".cache" / "ws-shots"
# Flash beats pro-preview here: vision-naming is a fast classification task,
# not a thinking task, and pro-preview's reasoning pass burns minutes for no
# quality lift on this kind of shallow visual labeling.
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_TIMEOUT_SECONDS = 180
# Screenshot width before upload. 2308 (typical CRD resolution) → too slow.
# 960 was readable for window-title/icon recognition but lost editor body
# text — fonts at ~40% of original become sub-pixel for vision OCR. 1600
# preserves enough glyph detail for code/notes content while keeping the
# multimodal call snappy (~80-150KB per image).
SHOT_WIDTH = 1600
NOTIFY_ID = "99001"  # arbitrary; replace-by-id keeps a single rolling toast
DISPLAY = ":1"  # the i3 desktop runs on :1 (Xtigervnc); CRD captures :20


def notify(summary: str, body: str = "") -> None:
    subprocess.run(
        ["notify-send", "-r", NOTIFY_ID, "-a", "ws-vision", summary, body],
        capture_output=True,
    )


def find_gemini() -> str:
    """Pick the newest nvm-managed gemini, falling back to PATH (matches workspace_namer.py)."""
    nvm_root = os.path.expanduser("~/.nvm/versions/node")

    def _ver_key(name: str) -> tuple[int, ...]:
        try:
            return tuple(int(p) for p in name.lstrip("v").split("."))
        except ValueError:
            return (-1,)

    if os.path.isdir(nvm_root):
        for v in sorted(os.listdir(nvm_root), key=_ver_key, reverse=True):
            cand = os.path.join(nvm_root, v, "bin", "gemini")
            if os.access(cand, os.X_OK):
                return cand
    for p in os.environ.get("PATH", "").split(os.pathsep):
        cand = os.path.join(p, "gemini")
        if os.access(cand, os.X_OK):
            return cand
    raise SystemExit("gemini CLI not found in ~/.nvm or on PATH")


def screenshot(out_path: Path) -> None:
    """Capture the X root and downscale to SHOT_WIDTH for upload speed.

    `import -resize {W}x` preserves aspect ratio. Quality 70 JPEG-style
    compression on PNG is largely placebo for screenshots, but combined
    with the resize keeps each file in the 30-80 KB range — small enough
    that 7 of them upload in seconds, not minutes.
    """
    env = {**os.environ, "DISPLAY": DISPLAY}
    subprocess.run(
        [
            "import",
            "-window",
            "root",
            "-resize",
            f"{SHOT_WIDTH}x",
            "-quality",
            "70",
            str(out_path),
        ],
        env=env,
        check=True,
    )


def cycle_and_capture(i3: i3ipc.Connection) -> tuple[dict[int, Path], list[int], int]:
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    # Wipe stale shots so a workspace that has since been deleted doesn't
    # get sent to Gemini.
    for old in SHOTS_DIR.glob("ws-*.png"):
        old.unlink()

    all_ws = sorted(i3.get_workspaces(), key=lambda w: w.num)
    all_ws = [w for w in all_ws if w.num >= 0]
    if not all_ws:
        raise SystemExit("no workspaces to capture")
    original_focused = next((w.num for w in all_ws if w.focused), all_ws[0].num)

    captures: dict[int, Path] = {}
    total = len(all_ws)
    for i, w in enumerate(all_ws, 1):
        notify(f"Vision namer {i}/{total}", f"capturing ws {w.num}: {w.name}")
        i3.command(f"workspace number {w.num}")
        # Pause for the X server to commit the new workspace's pixmaps and
        # for slower clients (Electron apps in particular — Discord, VS Code,
        # custom tools) to repaint after being unmapped. 200 ms gives them
        # enough margin without making the cycle feel sluggish.
        time.sleep(0.20)
        out = SHOTS_DIR / f"ws-{w.num}.png"
        screenshot(out)
        captures[w.num] = out

    i3.command(f"workspace number {original_focused}")
    return captures, [w.num for w in all_ws], original_focused


def _compact_text_ctx(ctx: dict) -> dict:
    """Trim per-pane scrollback so the multimodal prompt stays small.

    The text-based namer caps each pane at 4000 chars; with 7 workspaces ×
    ~3 panes each, that's ~84KB of prompt before the images. Vision calls
    bill differently and an LLM reading both modes doesn't need a full
    pane scrollback to confirm what the screenshot shows. Take the last
    400 chars per pane — enough to recognize the project, short enough to
    keep the call fast.
    """
    panes = ctx.get("wezterm_panes", []) or []
    return {
        "current_name": ctx.get("current_name"),
        "git_repos": ctx.get("git_repos") or [],
        "window_classes": ctx.get("window_classes") or [],
        "window_titles": ctx.get("window_titles") or [],
        "wezterm_pane_summaries": [
            {
                "title": p.get("title", ""),
                "tail": (p.get("text") or "")[-400:],
                **({"git_repo": p["git_repo"]} if p.get("git_repo") else {}),
            }
            for p in panes
        ],
    }


def build_prompt(
    captures: dict[int, Path],
    ws_order: list[int],
    text_ctx: dict[int, dict],
) -> str:
    sections = []
    for n in ws_order:
        rel = str(captures[n].relative_to(REPO_ROOT))
        compact = _compact_text_ctx(text_ctx.get(n, {}))
        sections.append(
            f"## Workspace {n}\n" f"Text context: {json.dumps(compact, ensure_ascii=False)}\n" f"Screenshot: @{rel}\n"
        )
    body = "\n".join(sections)
    return (
        "You are naming i3 desktop workspaces. For each workspace below you "
        "have BOTH a structured text context AND a screenshot. Use them "
        "together to propose THREE candidate names per workspace.\n\n"
        "HOW TO WEIGH THE TWO SIGNALS:\n"
        "- git_repos in the text context is the most stable identifier of "
        "what the workspace is about. It tells you what project the "
        "developer is in even when the screen is showing transient activity "
        "(e.g. an AI chat, a help page, a stack trace from a different "
        "tool).\n"
        "- The screenshot adds signals the text context lacks: actual "
        "browser tab titles, app icons, IDE filename bars, dialog titles, "
        "and the visual state of any GUI app. Use it to refine the name "
        "(e.g. distinguish two workspaces in the same git_repo by what "
        "they're actually working on).\n"
        "- The dominant on-screen text is NOT a reliable workspace label "
        "by itself. If the screenshot is mostly an AI chat about topic X "
        'but the git_repo says "foo-bar", the workspace is foo-bar — '
        "the chat is just the user's tool, not the workspace's identity.\n\n"
        "RULES per name (apply to BOTH candidates AND best):\n"
        "- ≤10 characters\n"
        "- lowercase only\n"
        "- dashes are the only allowed punctuation\n"
        "- avoid bare common nouns ('test', 'main', 'config') unless paired "
        "with a distinguishing token\n"
        "- prefer a real readable word over a devoweled abbreviation when "
        "both fit the budget; devowel only to win characters back\n"
        "- the three candidates should explore DIFFERENT axes (one project-"
        "name-derived, one activity-derived, one screenshot-derived) so the "
        "user can compare\n"
        '- "best" is your single recommendation — the candidate most likely '
        "to help a user identify the workspace at a glance. Default to the "
        "project-name-derived candidate unless something on screen makes a "
        "different axis clearly more useful.\n\n"
        "Return ONLY a JSON object mapping workspace_number (string) to an "
        'object with "best" and "candidates" fields:\n'
        '{"1": {"best": "name", "candidates": ["a", "b", "c"]}, "2": {...}}.\n'
        "No prose, no code fences.\n\n"
        f"{body}"
    )


def call_gemini(prompt: str) -> str:
    binary = find_gemini()
    env = {**os.environ, "PATH": os.path.dirname(binary) + os.pathsep + os.environ.get("PATH", "")}
    res = subprocess.run(
        [binary, "-p", prompt, "-m", GEMINI_MODEL, "-o", "text", "--approval-mode", "plan"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=GEMINI_TIMEOUT_SECONDS,
        env=env,
    )
    if res.returncode != 0:
        sys.stderr.write(f"gemini exit {res.returncode}\n{res.stderr or res.stdout}\n")
        raise SystemExit(res.returncode)
    return res.stdout


def parse_response(stdout: str) -> dict[str, dict]:
    """Parse {ws_num: {best, candidates}} from gemini's response."""
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start == -1 or end <= start:
        raise SystemExit(f"no JSON object in response:\n{stdout[:500]}")
    data = json.loads(stdout[start : end + 1])
    out: dict[str, dict] = {}
    for k, v in data.items():
        if not k.lstrip("-").isdigit():
            continue
        if isinstance(v, list):
            # Backwards-compat: if the model emits the old shape
            # (just a list of candidates), treat the first as best.
            out[k] = {"best": v[0] if v else "", "candidates": v[:3]}
        elif isinstance(v, dict):
            best = v.get("best") or ""
            cands = v.get("candidates") or []
            if isinstance(cands, list):
                out[k] = {"best": best, "candidates": [c for c in cands if isinstance(c, str)][:3]}
    return out


def main() -> int:
    no_apply = "--no-apply" in sys.argv

    i3 = i3ipc.Connection()
    notify("Hybrid namer", "gathering text context…")
    # Gather text first so we don't lose it if anything later fails — and so
    # the user sees feedback before the visible workspace cycle starts.
    text_ctx = workspace_namer.gather_context(i3)
    notify("Hybrid namer", "starting workspace cycle…")
    captures, order, _original = cycle_and_capture(i3)
    notify("Hybrid namer", f"captured {len(captures)}; calling gemini…")

    prompt = build_prompt(captures, order, text_ctx)
    stdout = call_gemini(prompt)

    print("=" * 60)
    print("RAW GEMINI RESPONSE")
    print("=" * 60)
    print(stdout.strip())
    print()

    try:
        results = parse_response(stdout)
    except SystemExit:
        notify("Hybrid namer", "couldn't parse response — see stdout")
        raise

    # Build the rename plan: sanitize() routes through smart_truncate, so any
    # over-budget LLM output gets clamped to the 10-char limit before we hand
    # it to i3. The candidates are kept for display only — only `best` is
    # applied.
    proposed: dict[str, str] = {}
    print("=" * 60)
    print("CANDIDATES + BEST PICK (text + vision hybrid)")
    print("=" * 60)
    current = {w.num: w.name for w in i3.get_workspaces()}
    for ws_num in order:
        entry = results.get(str(ws_num), {})
        cands = entry.get("candidates", [])
        raw_best = entry.get("best", "")
        clean_best = workspace_namer.sanitize(raw_best)
        proposed[str(ws_num)] = clean_best
        existing = current.get(ws_num, "?")
        cand_str = "  |  ".join(f"{c!r} ({len(c)})" for c in cands) or "(none)"
        flag = "" if clean_best == raw_best else f"  (sanitized from {raw_best!r})"
        print(f"  ws {ws_num}  current: {existing!r:18}  best: {clean_best!r:14}{flag}")
        print(f"        candidates: {cand_str}")
    print()

    if no_apply:
        notify("Hybrid namer", "preview only (--no-apply)")
        return 0

    notify("Hybrid namer", "applying best names…")
    applied = workspace_namer.apply_names(i3, proposed)
    summary = workspace_namer.summarize(applied)
    notify("Hybrid namer", summary[:240])
    print(summary)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    sys.exit(main())
