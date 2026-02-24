#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["textual>=0.40.0", "rich>=13.0.0"]
# ///
"""
APK Listener TUI — watches a remote FIFO for APK paths, pulls them via rsync,
and installs them via adb. Replaces apk-listener.sh with a polished Textual UI.

Usage:
    uv run apk_listener.py
    # or: pip install textual && python3 apk_listener.py
"""
from __future__ import annotations

import asyncio
import os
import re
import shlex
import shutil
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from pathlib import Path

from rich.columns import Columns
from rich.table import Table
from rich.text import Text
from rich.progress_bar import ProgressBar
from textual import work
from textual.app import App, Binding, ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, OptionList, RichLog, Static
from textual.worker import Worker

# ── Configuration ────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent

@dataclass
class Config:
    remote: str = "ssilver@192.168.1.138"
    remote_script_dir: str = "~/scripts"
    fifo: str = "/tmp/apk-push-pipe"
    sock: str = "/tmp/apk-listener-ssh.sock"
    adb: str = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
    password: str = ""
    device: str = ""
    backoff_initial: float = 1.0
    backoff_max: float = 30.0


# ── Transfer tracking ────────────────────────────────────────────────────────

class TransferStatus(Enum):
    PULLING = auto()
    INSTALLING = auto()
    INSTALLED = auto()
    FAILED = auto()


@dataclass
class Transfer:
    filename: str
    percent: int = 0
    speed: str = ""
    status: TransferStatus = TransferStatus.PULLING
    worker: Worker | None = field(default=None, repr=False)
    proc: asyncio.subprocess.Process | None = field(default=None, repr=False)


# ── rsync progress regex ─────────────────────────────────────────────────────
# openrsync outputs lines like:  "  1048576  42%  999.49kB/s  0:00:02"
PROGRESS_RE = re.compile(r"\s*[\d,]+\s+(\d+)%\s+(\S+/s)")


# ── Widgets ──────────────────────────────────────────────────────────────────

class ConnectionStatus(Static):
    """One-line banner showing connection state."""

    connected: reactive[bool] = reactive(False)
    status_text: reactive[str] = reactive("Disconnected")

    def render(self) -> Text:
        if self.connected:
            dot = Text("● ", style="green bold")
            msg = Text(f"Connected  {self.app.cfg.remote}", style="green")
        else:
            dot = Text("● ", style="yellow bold")
            msg = Text(self.status_text, style="yellow")
        return dot + msg


class TransferTable(Static):
    """Renders active/recent transfers as a Rich table with row selection."""

    selected: reactive[int] = reactive(0)

    def render(self) -> Table | Text:
        transfers: dict[str, Transfer] = getattr(self.app, "transfers", {})
        if not transfers:
            return Text("  No active transfers", style="dim")

        table = Table(expand=True, show_header=True, padding=(0, 1), show_edge=False)
        table.add_column("", width=1)
        table.add_column("APK", ratio=2, no_wrap=True)
        table.add_column("Progress", ratio=4)
        table.add_column("Status", ratio=1, justify="right")

        status_styles = {
            TransferStatus.PULLING: "blue",
            TransferStatus.INSTALLING: "yellow",
            TransferStatus.INSTALLED: "green",
            TransferStatus.FAILED: "red",
        }

        for i, (_key, t) in enumerate(transfers.items()):
            style = status_styles.get(t.status, "white")
            is_active = t.status in (TransferStatus.PULLING, TransferStatus.INSTALLING)
            is_selected = i == self.selected and is_active

            bar = ProgressBar(
                total=100, completed=t.percent, width=20,
                complete_style=style, finished_style=style,
            )
            pct_text = f" {t.percent}%"
            if t.speed:
                pct_text += f"  {t.speed}"
            progress_cell = Columns(
                [bar, Text(pct_text, style="dim")], padding=(0, 0)
            )

            table.add_row(
                Text("►" if is_selected else " ", style=style),
                Text(t.filename, style="bold"),
                progress_cell,
                Text(t.status.name.lower(), style=style),
            )

        return table


# ── Password Screen ─────────────────────────────────────────────────────────

class PasswordScreen(Screen):
    """Startup screen that optionally collects an SSH password."""

    BINDINGS = [("escape", "skip", "Skip (use key-based auth)")]

    def compose(self) -> ComposeResult:
        yield Static(
            "Enter SSH password for the remote host, or press Enter/Escape to skip "
            "(key-based auth).",
            id="pw-prompt",
        )
        yield Input(placeholder="Press Enter to skip", password=True, id="pw-input")

    def on_mount(self) -> None:
        self.query_one("#pw-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_skip(self) -> None:
        self.dismiss("")


class DeviceScreen(Screen):
    """Startup screen for selecting an ADB device when multiple are connected."""

    BINDINGS = [("escape", "skip", "Skip")]

    def __init__(self, devices: list[tuple[str, str]]) -> None:
        super().__init__()
        self._devices = devices

    def compose(self) -> ComposeResult:
        yield Static("Multiple ADB devices connected. Select one:", id="dev-prompt")
        options = []
        for serial, desc in self._devices:
            label = f"{serial}  {desc}" if desc else serial
            options.append(label)
        yield OptionList(*options, id="dev-list")

    def on_mount(self) -> None:
        self.query_one("#dev-list", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        serial = self._devices[event.option_index][0]
        self.dismiss(serial)

    def action_skip(self) -> None:
        self.dismiss("")


# ── Main Application ─────────────────────────────────────────────────────────

class ApkListenerApp(App):
    """Textual TUI for listening to APK pushes from a remote build machine."""

    TITLE = "APK Listener"
    CSS = """
    #connection {
        height: 1;
        padding: 0 1;
        background: $surface;
        dock: top;
    }
    #transfer-table {
        height: auto;
        max-height: 12;
        padding: 0 1;
        dock: top;
        border-bottom: solid $accent;
    }
    #log {
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", show=False, priority=True),
        Binding("ctrl+z", "quit", "Quit", show=False, priority=True),
        ("q", "quit", "Quit"),
        ("up", "select_prev", "Prev"),
        ("down", "select_next", "Next"),
        ("x", "cancel_transfer", "Cancel"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.cfg = Config()
        self.transfers: dict[str, Transfer] = {}
        self._install_lock = asyncio.Lock()
        self._transfer_counter = 0
        self._askpass_path: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield ConnectionStatus(id="connection")
            yield TransferTable(id="transfer-table")
            yield RichLog(id="log", highlight=True, markup=True)
        yield Footer()

    def on_mount(self) -> None:
        self.push_screen(PasswordScreen(), callback=self._on_password_entered)

    def _on_password_entered(self, password: str) -> None:
        """Called when the password screen is dismissed."""
        if password:
            self.cfg.password = password
            self._setup_askpass()
        self.log_event("Starting APK Listener...")
        self.log_event(f"Remote: {self.cfg.remote}")
        self.log_event(f"FIFO: {self.cfg.fifo}")
        if not self._check_dependencies():
            return
        self._detect_devices()

    def _check_dependencies(self) -> bool:
        """Verify required external tools are available. Returns False if fatal."""
        ok = True

        # PATH-based tools
        for tool in ("ssh", "rsync", "script"):
            path = shutil.which(tool)
            if path:
                self.log_event(f"[dim]Found {tool}: {path}[/dim]")
            else:
                self.log_event(f"[red]Required tool not found: {tool}[/red]")
                ok = False

        # adb — check configured path, env-based SDK dirs, well-known
        # install locations, then PATH
        adb_found = self._find_adb()
        if adb_found:
            if adb_found != self.cfg.adb:
                self.log_event(f"[yellow]adb: using {adb_found}[/yellow]")
                self.cfg.adb = adb_found
            else:
                self.log_event(f"[dim]Found adb: {adb_found}[/dim]")
        else:
            self.log_event("[red]adb not found[/red]")
            ok = False

        if not ok:
            self.log_event(
                "[red bold]Missing dependencies — cannot continue. "
                "Press q to quit.[/red bold]"
            )
        return ok

    def _find_adb(self) -> str | None:
        """Search for adb: configured path, env vars, well-known dirs, PATH."""
        # Configured path first
        if os.path.isfile(self.cfg.adb) and os.access(self.cfg.adb, os.X_OK):
            return self.cfg.adb

        home = Path.home()
        sdk_roots: list[Path] = []
        for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
            val = os.environ.get(var)
            if val:
                sdk_roots.append(Path(val))

        sdk_roots.extend([
            home / "Library" / "Android" / "sdk",          # macOS (Android Studio)
            home / "Android" / "Sdk",                      # Linux (Android Studio)
            Path("/usr/local/lib/android/sdk"),             # CI images
        ])

        for root in sdk_roots:
            candidate = root / "platform-tools" / "adb"
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)

        # Last resort: PATH
        return shutil.which("adb")

    @work(exclusive=False, thread=False)
    async def _detect_devices(self) -> None:
        """Detect ADB devices, prompt if needed, then start SSH manager."""
        devices = await self._list_adb_devices()
        if not devices:
            self.log_event(
                "[yellow]No ADB devices found — will retry at install time[/yellow]"
            )
            self._run_ssh_manager()
        elif len(devices) == 1:
            serial, desc = devices[0]
            self.cfg.device = serial
            label = f"{serial} ({desc})" if desc else serial
            self.log_event(f"[dim]Using device: {label}[/dim]")
            self._run_ssh_manager()
        else:
            self.log_event(f"Found {len(devices)} ADB devices")
            self.push_screen(
                DeviceScreen(devices), callback=self._on_device_selected,
            )

    def _on_device_selected(self, serial: str) -> None:
        """Called when the device screen is dismissed."""
        if serial:
            self.cfg.device = serial
            self.log_event(f"Using device: {serial}")
        else:
            self.log_event("[yellow]No device selected — will use default[/yellow]")
        self._run_ssh_manager()

    async def _list_adb_devices(self) -> list[tuple[str, str]]:
        """Run ``adb devices -l`` and return [(serial, description), ...]."""
        try:
            proc = await asyncio.create_subprocess_exec(
                self.cfg.adb, "devices", "-l",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
        except FileNotFoundError:
            return []

        devices: list[tuple[str, str]] = []
        for line in stdout.decode().splitlines():
            if not line.strip() or line.startswith("List of"):
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                serial = parts[0]
                # Remaining tokens are key:value pairs like model:Pixel_7
                desc = " ".join(parts[2:])
                devices.append((serial, desc))
        return devices

    def _setup_askpass(self) -> None:
        """Write a temporary SSH_ASKPASS script that echoes the password."""
        fd, path = tempfile.mkstemp(prefix="apk-askpass-")
        with os.fdopen(fd, "w") as f:
            f.write(f"#!/bin/sh\necho {shlex.quote(self.cfg.password)}\n")
        os.chmod(path, stat.S_IRWXU)  # mode 700
        self._askpass_path = path

    def _ssh_env(self) -> dict[str, str] | None:
        """Return env dict for SSH_ASKPASS auth, or None for key-based auth."""
        if not self._askpass_path:
            return None
        env = os.environ.copy()
        env["SSH_ASKPASS"] = self._askpass_path
        env["SSH_ASKPASS_REQUIRE"] = "force"
        env["DISPLAY"] = env.get("DISPLAY", ":0")
        return env

    # ── Helpers ──────────────────────────────────────────────────────────

    def log_event(self, msg: str) -> None:
        """Append a timestamped line to the event log."""
        ts = datetime.now().strftime("%H:%M:%S")
        self.query_one("#log", RichLog).write(f"[dim]{ts}[/dim]  {msg}")

    def update_connection(self, connected: bool, text: str = "") -> None:
        widget = self.query_one("#connection", ConnectionStatus)
        widget.connected = connected
        if text:
            widget.status_text = text

    def _next_key(self, tag: str) -> str:
        self._transfer_counter += 1
        return f"{tag}-{self._transfer_counter}"

    def add_transfer(self, key: str, filename: str) -> None:
        self.transfers[key] = Transfer(filename=filename)
        self.query_one("#transfer-table", TransferTable).refresh()

    def update_transfer(self, key: str, **kwargs) -> None:
        if key not in self.transfers:
            return
        for k, v in kwargs.items():
            setattr(self.transfers[key], k, v)
        self.query_one("#transfer-table", TransferTable).refresh()

    def remove_transfer(self, key: str, after: float = 5.0) -> None:
        """Remove a finished transfer from the table after a delay."""
        def _remove() -> None:
            self.transfers.pop(key, None)
            table = self.query_one("#transfer-table", TransferTable)
            table.selected = min(table.selected, max(0, len(self.transfers) - 1))
            table.refresh()
        self.set_timer(after, _remove)

    @contextmanager
    def _track_proc(self, key: str, proc: asyncio.subprocess.Process):
        """Register a subprocess with a transfer so it can be cancelled."""
        if key in self.transfers:
            self.transfers[key].proc = proc
        try:
            yield
        finally:
            if key in self.transfers:
                self.transfers[key].proc = None

    # ── Selection & Cancel ───────────────────────────────────────────────

    def action_select_prev(self) -> None:
        table = self.query_one("#transfer-table", TransferTable)
        table.selected = max(0, table.selected - 1)

    def action_select_next(self) -> None:
        table = self.query_one("#transfer-table", TransferTable)
        table.selected = min(max(0, len(self.transfers) - 1), table.selected + 1)

    def action_cancel_transfer(self) -> None:
        table = self.query_one("#transfer-table", TransferTable)
        keys = list(self.transfers.keys())
        idx = table.selected
        if not (0 <= idx < len(keys)):
            return
        key = keys[idx]
        t = self.transfers[key]
        if t.status not in (TransferStatus.PULLING, TransferStatus.INSTALLING):
            return
        self.log_event(f"[yellow]\\[{t.filename}] Cancelling...[/yellow]")
        # Kill subprocess, then cancel worker
        if t.proc:
            try:
                t.proc.kill()
            except ProcessLookupError:
                pass
        if t.worker:
            t.worker.cancel()

    # ── SSH Manager ──────────────────────────────────────────────────────

    @work(exclusive=True, thread=False)
    async def _run_ssh_manager(self) -> None:
        """Maintain SSH connection and run the FIFO read loop."""
        while True:
            try:
                await self._ensure_ssh()
                await self._setup_remote()
                await self._fifo_read_loop()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.log_event(f"[red]Error:[/red] {exc}")
            # Connection lost — tear down and retry
            self.update_connection(False, "Reconnecting...")
            self.log_event("[yellow]Connection lost, reconnecting...[/yellow]")
            await self._ssh_exit()

    async def _ssh_cmd(self, *args: str, timeout: float = 30) -> tuple[int, str, str]:
        """Run an SSH command over the control socket."""
        cmd = ["ssh", "-S", self.cfg.sock, self.cfg.remote, *args]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return 1, "", "timeout"
        return proc.returncode or 0, stdout.decode(), stderr.decode()

    async def _ssh_exit(self) -> None:
        """Tear down the SSH control master."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ssh", "-S", self.cfg.sock, "-O", "exit", self.cfg.remote,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except Exception:
            pass
        _unlink_safe(self.cfg.sock)

    async def _ensure_ssh(self) -> None:
        """Connect SSH with exponential backoff."""
        # Check existing connection
        check = await asyncio.create_subprocess_exec(
            "ssh", "-S", self.cfg.sock, "-O", "check", self.cfg.remote,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        if await check.wait() == 0:
            self.update_connection(True)
            return

        await self._ssh_exit()

        delay = self.cfg.backoff_initial
        while True:
            self.update_connection(False, f"Connecting to {self.cfg.remote}...")
            self.log_event(f"Connecting to {self.cfg.remote}...")

            proc = await asyncio.create_subprocess_exec(
                "ssh", "-M", "-S", self.cfg.sock, "-fN",
                "-o", "ConnectTimeout=5",
                "-o", "ServerAliveInterval=15",
                "-o", "ServerAliveCountMax=3",
                self.cfg.remote,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=self._ssh_env(),
            )
            rc = await proc.wait()
            if rc == 0:
                self.update_connection(True)
                self.log_event("[green]Tunnel up[/green]")
                return

            self.update_connection(False, f"Retry in {delay:.0f}s...")
            self.log_event(f"[yellow]Connection failed, retrying in {delay:.0f}s...[/yellow]")
            await asyncio.sleep(delay)
            delay = min(delay * 2, self.cfg.backoff_max)

    async def _setup_remote(self) -> None:
        """Create FIFO on remote and sync push-apk.sh."""
        # Always recreate the FIFO to clear stale readers from previous sessions.
        # An orphaned `cat` or `read` holding the old inode will get EOF,
        # and new pushes will only go to our fresh reader.
        rc, _, _ = await self._ssh_cmd(
            f"rm -f '{self.cfg.fifo}' && mkfifo '{self.cfg.fifo}'"
        )
        if rc == 0:
            self.log_event("[green]FIFO ready on remote[/green]")
        else:
            self.log_event("[red]FIFO setup failed[/red]")

        # Ensure remote script dir exists
        await self._ssh_cmd(f"mkdir -p {self.cfg.remote_script_dir}")

        # Sync push-apk.sh
        push_script = SCRIPT_DIR / "push-apk.sh"
        if push_script.exists():
            proc = await asyncio.create_subprocess_exec(
                "rsync", "-a",
                "-e", f"ssh -S '{self.cfg.sock}'",
                str(push_script),
                f"{self.cfg.remote}:{self.cfg.remote_script_dir}/push-apk.sh",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            rc = await proc.wait()
            if rc == 0:
                self.log_event(
                    f"[green]push-apk.sh synced to {self.cfg.remote_script_dir}[/green]"
                )
            else:
                self.log_event("[yellow]Could not sync push-apk.sh (non-fatal)[/yellow]")

    # ── FIFO Read Loop ───────────────────────────────────────────────────

    async def _fifo_read_loop(self) -> None:
        """Persistent FIFO reader — one SSH session handles all pushes.

        Opens the FIFO in read-write mode (exec 3<>) to prevent EOF, so
        there is never a gap between reads where a push would be lost.
        """
        self.log_event("Waiting for APK...")

        fifo_cmd = (
            f"exec 3<>'{self.cfg.fifo}' && "
            f"while IFS= read -r line <&3; do echo \"$line\"; done"
        )
        proc = await asyncio.create_subprocess_exec(
            "ssh", "-S", self.cfg.sock, self.cfg.remote, fifo_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            assert proc.stdout is not None
            async for raw_line in proc.stdout:
                apk_path = raw_line.decode().strip()
                if not apk_path:
                    continue

                self.log_event(f"Received: [bold]{apk_path}[/bold]")

                filename = os.path.basename(apk_path)
                tag = filename.removesuffix(".apk")
                key = self._next_key(tag)
                self.add_transfer(key, tag)

                worker = self._process_apk(key, apk_path, tag)
                self.transfers[key].worker = worker

                self.log_event("Waiting for APK...")
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

        # If we get here, the SSH FIFO reader exited
        rc = proc.returncode
        if rc != 0:
            raise ConnectionError(f"FIFO reader exited (rc={rc})")

    # ── APK Processing ───────────────────────────────────────────────────

    @work(exclusive=False, thread=False)
    async def _process_apk(self, key: str, apk_path: str, tag: str) -> None:
        """Pull an APK via rsync, then install via adb."""
        filename = os.path.basename(apk_path)
        local_path = f"/tmp/{filename}"

        try:
            # ── Pull (parallel — multiple rsync can run concurrently) ────
            await self._rsync_pull(key, apk_path, local_path, tag)

            # ── Install (serialized — one adb install at a time) ─────────
            self.update_transfer(key, status=TransferStatus.INSTALLING)
            self.log_event(f"[yellow]\\[{tag}] Installing...[/yellow]")

            async with self._install_lock:
                await self._adb_install(key, local_path, tag)

            self.update_transfer(key, status=TransferStatus.INSTALLED)
            self.log_event(f"[green]\\[{tag}] Installed[/green]")

        except asyncio.CancelledError:
            # Kill any running subprocess
            if key in self.transfers and self.transfers[key].proc:
                try:
                    self.transfers[key].proc.kill()
                except ProcessLookupError:
                    pass
            self.log_event(f"[yellow]\\[{tag}] Cancelled[/yellow]")
            self.update_transfer(key, status=TransferStatus.FAILED)

        except Exception as exc:
            self.log_event(f"[red]\\[{tag}] Failed: {exc}[/red]")
            self.update_transfer(key, status=TransferStatus.FAILED)

        finally:
            self.remove_transfer(key)

    async def _rsync_pull(
        self, key: str, remote_path: str, local_path: str, tag: str
    ) -> None:
        """Pull a file via rsync, parsing progress output in real-time."""
        self.log_event(f"[blue]\\[{tag}] Pulling...[/blue]")

        # Use script(1) to force a pty so rsync emits incremental progress
        proc = await asyncio.create_subprocess_exec(
            "script", "-q", "/dev/null",
            "rsync", "-ah", "--progress",
            "-e", f"ssh -S '{self.cfg.sock}'",
            f"{self.cfg.remote}:{remote_path}",
            local_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        with self._track_proc(key, proc):
            assert proc.stdout is not None
            buf = b""
            while True:
                chunk = await proc.stdout.read(256)
                if not chunk:
                    break
                buf += chunk
                # Split on \r or \n to get progress lines
                while b"\r" in buf or b"\n" in buf:
                    idx_r = buf.find(b"\r")
                    idx_n = buf.find(b"\n")
                    if idx_r == -1:
                        idx = idx_n
                    elif idx_n == -1:
                        idx = idx_r
                    else:
                        idx = min(idx_r, idx_n)
                    line = buf[:idx].decode(errors="replace").strip()
                    buf = buf[idx + 1:]
                    if not line:
                        continue
                    m = PROGRESS_RE.search(line)
                    if m:
                        pct = int(m.group(1))
                        speed = m.group(2)
                        self.update_transfer(key, percent=pct, speed=speed)

            await proc.wait()
            if proc.returncode != 0:
                raise RuntimeError(f"rsync exited with code {proc.returncode}")

            # Final 100%
            self.update_transfer(key, percent=100, speed="")

            # Log file size
            try:
                size = os.path.getsize(local_path)
                self.log_event(f"[green]\\[{tag}] Pulled {_human_size(size)}[/green]")
            except OSError:
                self.log_event(f"[green]\\[{tag}] Pulled[/green]")

    async def _adb_install(self, key: str, local_path: str, tag: str) -> None:
        """Install an APK via adb."""
        cmd = [self.cfg.adb]
        if self.cfg.device:
            cmd.extend(["-s", self.cfg.device])
        cmd.extend(["install", "-r", local_path])
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        with self._track_proc(key, proc):
            assert proc.stdout is not None
            async for raw_line in proc.stdout:
                line = raw_line.decode(errors="replace").strip()
                if line:
                    self.log_event(f"[dim]\\[{tag}] {line}[/dim]")

            await proc.wait()
            if proc.returncode != 0:
                raise RuntimeError(f"adb install exited with code {proc.returncode}")

    # ── Teardown ─────────────────────────────────────────────────────────

    async def action_quit(self) -> None:
        self.log_event("Shutting down...")
        await self._ssh_exit()
        _unlink_safe(self._askpass_path)
        self.exit()


def _unlink_safe(path: str | None) -> None:
    """Remove a file if it exists, ignoring missing files."""
    if path is None:
        return
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _human_size(nbytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024:
            return f"{nbytes:.1f} {unit}" if unit != "B" else f"{nbytes} B"
        nbytes /= 1024  # type: ignore[assignment]
    return f"{nbytes:.1f} TB"


def main() -> None:
    app = ApkListenerApp()
    app.run()


if __name__ == "__main__":
    main()
