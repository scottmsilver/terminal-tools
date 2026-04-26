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

GEMINI_MODEL = "gemini-3.1-pro-preview"  # best naming quality; flash is faster but coarser
GEMINI_TIMEOUT_SECONDS = 180  # 3.1-pro-preview can take 60-150s on a thinking pass
PANE_SCROLLBACK_LINES = 50
PANE_TEXT_CAP_CHARS = 4000
# Target visual width for the workspace name (after the "N: " prefix). Polybar
# tab pills get cramped above this, and the human eye can recognize a short
# devoweled abbreviation about as fast as the full word — see Cambridge
# scrambled-text studies. We treat this as soft: the LLM is asked to aim for
# it, and smart_truncate() guarantees the final name fits.
NAME_TARGET_CHARS = 10


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


def _is_wezterm_class(cls: str | None) -> bool:
    c = (cls or "").lower()
    return c.startswith("org.wezfurlong") or "wezterm" in c


def _match_pane_for_leaf(
    leaf_title: str,
    panes: list[dict[str, Any]],
    used: set[int],
) -> dict[str, Any] | None:
    """Find a not-yet-claimed wezterm pane whose title is contained in this leaf's title."""
    if not leaf_title:
        return None
    for p in panes:
        pid = p.get("pane_id")
        if pid in used:
            continue
        ptitle = p.get("title")
        if ptitle and ptitle in leaf_title:
            return p
    return None


def _panes_in_same_window(
    seed: dict[str, Any],
    panes: list[dict[str, Any]],
    used: set[int],
) -> list[dict[str, Any]]:
    """Return every not-yet-claimed pane sharing the seed's wezterm OS-window.

    A wezterm OS-window typically contains many tabs and panes; matching one
    pane by title (the one whose title bubbles up to the X11 window manager)
    unlocks the rest of the window's tabs/panes for context. Without this,
    we'd only see the focused pane and miss everything in the other tabs of
    the same workspace's wezterm.
    """
    wid = seed.get("window_id")
    if wid is None:
        # Fall back to just the seed pane if wezterm didn't expose window_id.
        return [seed] if seed.get("pane_id") not in used else []
    out: list[dict[str, Any]] = []
    for p in panes:
        if p.get("window_id") != wid:
            continue
        pid = p.get("pane_id")
        if not isinstance(pid, int) or pid in used:
            continue
        out.append(p)
    return out


def gather_context(i3: i3ipc.Connection) -> dict[int, dict[str, Any]]:
    """Per-workspace: current name, window classes/titles, list of wezterm panes."""
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

        # Collect text from EVERY wezterm pane in this workspace, including
        # all tabs/panes inside each wezterm OS-window — not just the focused
        # pane whose title bubbles up to i3. A workspace running tests in pane
        # 1, a server in pane 2, and editing in pane 3 needs context from all
        # three; the focused-pane title alone is misleading.
        wezterm_panes: list[dict[str, Any]] = []
        git_repos: list[str] = []
        for leaf in leaves:
            if not _is_wezterm_class(leaf.window_class):
                continue
            seed = _match_pane_for_leaf(leaf.name or "", panes, used_pane_ids)
            if not seed:
                continue
            for p in _panes_in_same_window(seed, panes, used_pane_ids):
                pid = p.get("pane_id")
                if not isinstance(pid, int):
                    continue
                used_pane_ids.add(pid)
                text = _wez_pane_text(pid)
                repo = _git_repo_name(_pane_cwd(p))
                wezterm_panes.append(
                    {
                        "title": p.get("title") or "",
                        "text": text,
                        **({"git_repo": repo} if repo else {}),
                    }
                )
                if repo:
                    git_repos.append(repo)

        # Focused-workspace fallback: if title matching missed every wezterm
        # leaf (which happens during shell startup before wezterm has set a
        # title), grab the globally focused pane so we still produce useful
        # context for the workspace the user is actually looking at. This may
        # only fire once and only on the focused workspace — otherwise the
        # first workspace iterated would steal it.
        if (
            not wezterm_panes
            and any(_is_wezterm_class(c) for c in classes)
            and getattr(ws, "focused", False)
            and focused_pane_id is not None
            and focused_pane_id not in used_pane_ids
        ):
            chosen = next((p for p in panes if p.get("pane_id") == focused_pane_id), None)
            if chosen:
                used_pane_ids.add(focused_pane_id)
                text = _wez_pane_text(focused_pane_id)
                repo = _git_repo_name(_pane_cwd(chosen))
                wezterm_panes.append(
                    {
                        "title": chosen.get("title") or "",
                        "text": text,
                        **({"git_repo": repo} if repo else {}),
                    }
                )
                if repo:
                    git_repos.append(repo)

        # Strip the "N:" prefix from the workspace name so the model sees only
        # the human-chosen intent. Otherwise the model can "preserve" the whole
        # "1: foo" string and robust_rename prepends the number again, yielding
        # "1: 1-foo" — non-idempotent across repeated runs.
        current_intent = re.sub(r"^\s*\d+\s*:?\s*", "", ws.name or "").strip()
        ctx[ws.num] = {
            "current_name": current_intent,
            "window_classes": classes,
            "window_titles": titles[:8],
            "git_repos": list(dict.fromkeys(git_repos)),  # dedup, preserve order
            "wezterm_panes": wezterm_panes,
        }
    return ctx


def build_prompt(contexts: dict[int, dict[str, Any]]) -> str:
    body = {str(k): v for k, v in contexts.items()}
    n = NAME_TARGET_CHARS
    return (
        f"You are naming i3 workspaces for a developer's tab bar. For each "
        f"workspace, propose ONE name ≤{n} characters that someone glancing at "
        f"the tab can match to the underlying project at a glance.\n\n"
        f"WHY THIS WORKS: English readers don't decode words letter-by-letter. "
        f"They use word shape, leading characters, and the consonant skeleton. "
        f'Vowels carry less information than consonants — "cnsnnts crry mst f '
        f'th sgnl" stays readable. So when compression is needed, dropping '
        f"vowels (especially interior ones) preserves recognizability while "
        f"shortening. Word-initial letters are most load-bearing; protect them. "
        f"Trailing letters help too. Middle vowels are nearly free to drop. "
        f"Drop a vowel only when needed to fit the budget — never preemptively, "
        f"because every dropped letter is a small cost paid in reading effort.\n\n"
        f"CORE PRINCIPLES (apply in this priority order, top wins):\n"
        f"  P1. SPECIFICITY: the name must distinguish this workspace from a "
        f"hypothetical other workspace doing similar work. Generic English "
        f'nouns ("pool", "test", "main", "config", "namer") are '
        f"uninformative when used alone — even if the user previously chose "
        f"them. Always prefer a project/repo-derived name over a current_name "
        f"that is a bare common noun.\n"
        f"  P2. MINIMUM COMPRESSION: never drop letters you don't need to drop. "
        f"If a real readable token fits the {n}-char budget verbatim, use it "
        f"verbatim. Devoweling is a last resort to win characters back, not a "
        f"stylistic choice.\n"
        f"  P3. BREVITY: among recognizable forms, prefer the shorter one. "
        f"If a 6-char form and a 10-char form both read clearly, the 6-char "
        f"one wins — tab labels reward brevity. Don't pad to fill the budget. "
        f"Brevity does NOT override P1: a short generic noun still loses to a "
        f"longer specific name.\n\n"
        f"DECISION CASCADE — try each in order, take the first that produces a "
        f"name ≤{n} chars:\n"
        f"  1. The most specific source string (project repo name, app name, "
        f"clearly-named tool).\n"
        f"  2. The same string with separator dashes removed.\n"
        f"  3. The single most distinctive token from that string.\n"
        f"  4. Combine the most specific token with one extra distinguishing "
        f"word (compress one segment, keep the other readable).\n"
        f"  5. Devowel: drop interior vowels but keep the first letter of each "
        f"word; keep all consonants. Preserve dashes if they aid readability.\n"
        f"  6. Devowel + drop dashes.\n"
        f'  7. Last resort: "first…last" with U+2026 ellipsis.\n\n'
        f"EXAMPLES (illustrative — apply the cascade to the actual data, not "
        f"these literal strings):\n"
        f'  source "x-y" (3)               →  "x-y"               '
        f"(already short, pass through)\n"
        f'  source "alpha-beta" (10)       →  "alpha-beta"        '
        f"(fits; do NOT devowel)\n"
        f'  source "alpha-beta" (10)       →  NOT "alph-beta"     '
        f"(same length, less readable — violates P2)\n"
        f'  source "deployment-runner" (17)→  "dply-rnr"          '
        f"(must compress; consonants + dash survive)\n"
        f'  source "deployment" alone      →  "deploy"            '
        f'(preserve recognizable real word over "dplymnt")\n'
        f'  current_name "main" + repo "foo-bar" → "foo-bar"        '
        f"(P1: a generic current_name loses to a specific repo name)\n\n"
        f"GENERAL RULES:\n"
        f"- Lowercase only; dashes are the only allowed punctuation; no emoji "
        f"or other symbols.\n"
        f"- PASS THROUGH already-short identifiers (≤{n} chars and recognizable "
        f"as a name) unchanged.\n"
        f"- Preserve current_name ONLY if it is BOTH specific (not a bare "
        f"common noun) AND ≤{n} chars.\n\n"
        f"CONTEXT WEIGHTING (read these stably, in this order):\n"
        f"  1. git_repos — strongest signal; the project the developer is "
        f"working in. Stable across panes and over time.\n"
        f"  2. wezterm_panes[].text — what is actually happening RIGHT NOW. "
        f"Read every pane's text and look for the unifying theme. Do NOT "
        f"anchor on the first pane's content — panes are listed in i3-tree "
        f"order, not importance order.\n"
        f"  3. wezterm_panes[].title — a snapshot label of whichever process "
        f'the pane was last running (e.g. "vim", "npm-test", a stale '
        f"command name). Useful as weak corroboration but DO NOT name the "
        f"workspace after a single pane's title alone, especially the first "
        f"pane's title — titles drift as the user runs new commands.\n"
        f"  4. window_titles — noisy fallback (often the X11 window manager "
        f"title, which mirrors the focused pane's title).\n"
        f"  5. current_name — what the workspace is called RIGHT NOW. Often "
        f"stale: it was set at some past moment by this same namer or by the "
        f"user, and the workspace's purpose may have shifted since. Preserve "
        f"only if BOTH (a) current_name is specific (passes P1) AND (b) the "
        f"recent pane text still matches that name. Otherwise rename.\n\n"
        f"Return ONLY a JSON object mapping workspace_number (string) to name "
        f"(string). No prose, no code fences.\n\n"
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
            [binary, "-p", prompt, "-m", GEMINI_MODEL, "-o", "text", "--approval-mode", "plan"],
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


def _devowel_word(word: str) -> str:
    """Drop interior vowels (a/e/i/o/u) while keeping the first letter.

    Humans recover devoweled words quickly because consonants carry most of
    the information ("cnsnnts crry mst f th nfrmtn"). We always preserve the
    very first character so the word's leading sound stays intact, which is
    what your eye anchors on.
    """
    if len(word) <= 2:
        return word
    return word[0] + re.sub(r"[aeiou]", "", word[1:])


def smart_truncate(name: str, max_chars: int = NAME_TARGET_CHARS) -> str:
    """Compress *name* to ≤max_chars, preserving recognizability.

    Cascade (try in order, take the first that fits):
      1. As-is.
      2. Drop separator dashes (joins compound words).
      3. Devowel each segment, keep dashes.
      4. Devowel each segment, drop dashes.
      5. Ellipsis (U+2026, 1 visual char): for compound names we keep
         "first…last" so both ends remain readable; for a single word we
         truncate the tail with a trailing ellipsis.
    """
    n = (name or "").strip()
    if len(n) <= max_chars:
        return n

    # 2. Drop dashes.
    no_dash = n.replace("-", "")
    if len(no_dash) <= max_chars:
        return no_dash

    # 3. Devowel each dash-separated segment.
    parts = [p for p in n.split("-") if p]
    devoweled = "-".join(_devowel_word(p) for p in parts)
    if len(devoweled) <= max_chars:
        return devoweled

    # 4. Devowel + drop dashes.
    no_dash_dev = devoweled.replace("-", "")
    if len(no_dash_dev) <= max_chars:
        return no_dash_dev

    # 5. Ellipsis. For multi-word names, keep the head of the first segment
    # and the tail of the last segment — that pattern reads as "this thing
    # in the middle of <first>…<last>" rather than just "starts with…".
    if len(parts) >= 2:
        budget = max_chars - 1  # ellipsis takes 1 visual char
        first_keep = max(1, budget // 2)
        last_keep = max(1, budget - first_keep)
        candidate = parts[0][:first_keep] + "…" + parts[-1][-last_keep:]
        if len(candidate) <= max_chars:
            return candidate

    # Single-word fallback: head + ellipsis.
    return no_dash_dev[: max_chars - 1] + "…"


def sanitize(name: str) -> str:
    s = (name or "").strip().lower()
    s = re.sub(r"\s+", "-", s)
    # Allow lowercase letters, digits, dashes, and the U+2026 ellipsis
    # character that smart_truncate may inject downstream — strip everything
    # else (emoji, punctuation, accents, etc.).
    s = re.sub(r"[^a-z0-9\-…]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return smart_truncate(s, NAME_TARGET_CHARS)


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


def _print_section(title: str, body: str) -> None:
    print("=" * 60)
    print(title)
    print("=" * 60)
    print(body)


def main(argv: list[str]) -> int:
    # Three modes:
    #   default     — gather, call gemini, apply renames (what polybar's
    #                 ✨ button invokes).
    #   --dry-run   — gather only; print context + prompt and stop. No API
    #                 call, no rename. Use for fast iteration on the prompt
    #                 or context shape.
    #   --no-apply  — gather + call gemini and print everything (context,
    #                 prompt, raw response, sanitized name table) but do not
    #                 rename anything. Use for previewing what the namer
    #                 would do before letting it touch your workspaces.
    dry_run = "--dry-run" in argv
    no_apply = "--no-apply" in argv
    cli_mode = dry_run or no_apply
    try:
        i3 = i3ipc.Connection()
        ctx = gather_context(i3)
        if not ctx:
            if cli_mode:
                print("(no active workspaces)", file=sys.stderr)
            else:
                notify("workspace-namer", "No active workspaces to name.")
            return 0

        prompt = build_prompt(ctx)

        if cli_mode:
            _print_section(
                "GATHERED CONTEXT (per workspace)",
                json.dumps({str(k): v for k, v in ctx.items()}, indent=2, ensure_ascii=False),
            )
            print()
            _print_section("PROMPT SENT TO GEMINI" if no_apply else "PROMPT THAT WOULD BE SENT TO GEMINI", prompt)
            print()

        if dry_run:
            return 0

        if not cli_mode:
            notify("workspace-namer", "Naming workspaces with Gemini…")
        stdout = ask_gemini(prompt)
        proposed = parse_response(stdout)

        if cli_mode:
            _print_section("RAW GEMINI RESPONSE", stdout.strip())
            print()
            # Show what apply_names() WOULD do, including any post-truncation
            # via sanitize() / smart_truncate(). That way the preview matches
            # the exact rename text we'd send to i3.
            current_names = {w.num: w.name for w in i3.get_workspaces()}
            preview = []
            for num_str, raw in proposed.items():
                try:
                    ws_num = int(num_str)
                except ValueError:
                    continue
                cleaned = sanitize(raw)
                old = current_names.get(ws_num, "(unknown)")
                preview.append(f"  {ws_num}: {old!r:30} -> {cleaned!r} ({len(cleaned)} chars)")
            _print_section("PROPOSED RENAMES (sanitized; not applied)", "\n".join(preview))
            return 0

        applied = apply_names(i3, proposed)
        notify("workspace-namer", summarize(applied))
        return 0
    except NamerError as e:
        if cli_mode:
            print(f"Error: {e}", file=sys.stderr)
        else:
            notify("workspace-namer", f"Error: {e}", urgency="critical")
        return 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    sys.exit(main(sys.argv[1:]))
