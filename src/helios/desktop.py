"""Helios as a desktop app: one shortcut, no Docker, no terminal.

Docker Compose is the right way to run Helios on a server and the wrong way to open it on a
laptop -- it needs Docker Desktop running, it cannot reach the OS keyring, and it makes the
settings page read-only. This module is the other path. It supervises the same three processes
Compose would (API, worker, dashboard) directly on the machine, opens the dashboard in its own
app window, and sits in the system tray until you quit it.

Design decisions worth knowing before changing any of it:

* **The window is Edge (or Chrome) in app mode, not an embedded webview.** Edge ships with
  Windows 11 and renders the dashboard exactly as it is tested; an embedded webview would add a
  native dependency (pythonnet) that does not yet exist for every Python Helios supports. The
  window uses its own browser profile under the runtime directory, so it never touches your
  normal browsing profile, cookies, or extensions.
* **Closing the window does not stop Helios.** The worker keeps syncing on its schedule and the
  tray icon reopens the window. "Quit Helios" in the tray stops everything. That matches how a
  background-syncing desktop app is expected to behave, and it sidesteps detecting when a browser
  window closes, which is unreliable across browser versions.
* **This launcher is the supervisor.** The API runs with ``HELIOS_RESTART_MODE=exit``, so the
  dashboard's Restart button makes it exit and the launcher starts it again -- together with the
  worker, which otherwise would keep running on the settings it started with.
* **The dashboard is built once and copied out of the source tree.** The standalone Next server
  runs from the runtime directory, not from ``web/.next``, so a developer running ``npm run
  build`` or the Playwright suite cannot overwrite files the running app is serving. The copy is
  rebuilt only when the web sources' content hash changes.
* **Everything runs on loopback,** exactly as the Compose stack does. Nothing here opens a port
  beyond 127.0.0.1.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Final, Protocol

logger = logging.getLogger("helios.desktop")

APP_NAME: Final = "Helios"
DEFAULT_API_PORT: Final = 8001
DEFAULT_WEB_PORT: Final = 3001

#: Web sources whose content decides whether the dashboard must be rebuilt. Tests are excluded
#: on purpose: editing a spec should not cost a minute-long rebuild on the next launch.
WEB_SOURCE_DIRS: Final = ("app", "components", "lib", "public")
WEB_SOURCE_FILES: Final = (
    "package.json",
    "package-lock.json",
    "next.config.ts",
    "postcss.config.mjs",
    "tsconfig.json",
)

#: A service restarted more often than this inside the window is treated as crash-looping and
#: left down, with a notice, rather than restarted forever and hiding the failure.
RESTART_LIMIT: Final = 5
RESTART_WINDOW_SECONDS: Final = 300.0

#: Log files are rotated past this size, keeping one previous generation.
LOG_ROTATE_BYTES: Final = 5_000_000

#: A settings restart implies a restart of these too: they read configuration once at startup.
DEPENDENTS: Final[Mapping[str, tuple[str, ...]]] = {"api": ("worker",)}

CREATE_NO_WINDOW: Final = 0x08000000


class DesktopError(RuntimeError):
    """Something the operator has to fix. The message is written to be shown to them as-is."""


# ---------------------------------------------------------------------------
# Paths and ports
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DesktopPaths:
    """Where everything lives.

    ``root`` is the Helios checkout: its `.env`, `config/`, `data/` and migrations are the same
    ones the CLI and Compose use, so the desktop app and the CLI see one database. ``runtime`` is
    per-user scratch state that is safe to delete: the built dashboard, the window's browser
    profile, logs, and the single-instance lock.
    """

    root: Path
    runtime: Path

    @property
    def web_source(self) -> Path:
        return self.root / "web"

    @property
    def web_server(self) -> Path:
        return self.runtime / "web"

    @property
    def browser_profile(self) -> Path:
        return self.runtime / "browser"

    @property
    def logs(self) -> Path:
        return self.runtime / "logs"

    @property
    def lock_file(self) -> Path:
        return self.runtime / "desktop.lock"

    @property
    def icon(self) -> Path:
        return self.runtime / "helios.ico"

    @property
    def env_file(self) -> Path:
        return self.root / ".env"


def find_project_root(start: Path | None = None) -> Path:
    """The Helios checkout this module belongs to, or the working directory if it is one.

    Desktop mode needs the checkout -- the migrations and web sources are not installed into
    site-packages -- so a non-editable install run from elsewhere is refused with a reason.
    """

    override = os.environ.get("HELIOS_HOME", "").strip()
    candidates = [Path(override)] if override else []
    candidates += [Path(__file__).resolve().parents[2], start or Path.cwd()]
    for candidate in candidates:
        if (candidate / "alembic.ini").is_file() and (candidate / "web").is_dir():
            return candidate.resolve()
    raise DesktopError(
        "Could not find the Helios folder (the one containing alembic.ini and web/). Install "
        "Helios with `pip install -e .` from that folder, or set HELIOS_HOME to it."
    )


def default_runtime_dir() -> Path:
    override = os.environ.get("HELIOS_DESKTOP_HOME", "").strip()
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        return Path(local) / APP_NAME
    state = os.environ.get("XDG_STATE_HOME", "").strip()
    return (Path(state) if state else Path.home() / ".local" / "state") / "helios"


def read_env_file(path: Path) -> dict[str, str]:
    """``KEY=value`` pairs from a dotenv file, without evaluating anything in it."""

    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Ports:
    api: int
    web: int

    @property
    def api_url(self) -> str:
        return f"http://127.0.0.1:{self.api}"

    @property
    def web_url(self) -> str:
        return f"http://127.0.0.1:{self.web}"


def resolve_ports(environ: Mapping[str, str], env_file: Mapping[str, str]) -> Ports:
    """The same `HELIOS_API_PORT` / `HELIOS_WEB_PORT` Compose uses, environment first."""

    def pick(name: str, default: int) -> int:
        raw = environ.get(name) or env_file.get(name) or ""
        if not raw.strip():
            return default
        try:
            port = int(raw)
        except ValueError as exc:
            raise DesktopError(f"{name}={raw!r} is not a port number.") from exc
        if not 1 <= port <= 65535:
            raise DesktopError(f"{name}={port} is outside the valid port range.")
        return port

    ports = Ports(
        api=pick("HELIOS_API_PORT", DEFAULT_API_PORT), web=pick("HELIOS_WEB_PORT", DEFAULT_WEB_PORT)
    )
    if ports.api == ports.web:
        raise DesktopError("HELIOS_API_PORT and HELIOS_WEB_PORT must differ.")
    return ports


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def http_ok(url: str, timeout: float = 2.0) -> bool:
    """Whether a GET returns any non-5xx answer. A 404 still proves the server is up."""

    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return int(response.status) < 500
    except urllib.error.HTTPError as exc:
        return exc.code < 500
    except (urllib.error.URLError, OSError, ValueError):
        return False


def is_helios_api(api_url: str) -> bool:
    """Whether whatever holds the API port is a Helios API, judged by its health payload."""

    try:
        with urllib.request.urlopen(f"{api_url}/health", timeout=2.0) as response:
            body = response.read(4096).decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, ValueError):
        return False
    return '"databaseReady"' in body


def wait_until(probe: Callable[[], bool], *, timeout: float, interval: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if probe():
            return True
        time.sleep(interval)
    return probe()


# ---------------------------------------------------------------------------
# The dashboard build
# ---------------------------------------------------------------------------


def web_source_fingerprint(web_dir: Path) -> str:
    """A content hash of everything that shapes the built dashboard.

    Content rather than modification times, so a `git checkout` that rewrites timestamps
    without changing a byte does not force a rebuild.
    """

    digest = hashlib.sha256()
    files: list[Path] = []
    for name in WEB_SOURCE_DIRS:
        directory = web_dir / name
        if directory.is_dir():
            files.extend(path for path in directory.rglob("*") if path.is_file())
    files.extend(web_dir / name for name in WEB_SOURCE_FILES if (web_dir / name).is_file())
    for path in sorted(files, key=lambda item: item.relative_to(web_dir).as_posix()):
        digest.update(path.relative_to(web_dir).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def web_build_is_current(paths: DesktopPaths) -> bool:
    stamp = paths.web_server / ".helios-build"
    if not (paths.web_server / "server.js").is_file() or not stamp.is_file():
        return False
    return stamp.read_text(encoding="utf-8").strip() == web_source_fingerprint(paths.web_source)


def find_executable(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        raise DesktopError(
            f"`{name}` was not found on PATH. The dashboard needs Node.js 22 or newer: "
            "install it from https://nodejs.org and start Helios again."
        )
    return found


def build_web(paths: DesktopPaths, *, run: Callable[..., None] | None = None) -> None:
    """Build the standalone dashboard and stage it into the runtime directory.

    Staged into a sibling directory and swapped in at the end, so an interrupted build leaves
    the previous working copy in place rather than half a server.
    """

    runner = run or _run_logged
    npm = find_executable("npm")
    source = paths.web_source
    if not (source / "node_modules").is_dir():
        runner([npm, "ci"], cwd=source, log_name="web-build")
    runner([npm, "run", "build"], cwd=source, log_name="web-build")

    standalone = source / ".next" / "standalone"
    if not (standalone / "server.js").is_file():
        raise DesktopError(
            "The dashboard build finished but produced no standalone server. Check "
            f"{paths.logs / 'web-build.log'}."
        )

    staging = paths.web_server.with_name(paths.web_server.name + ".staging")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(standalone, staging)
    # `next build` leaves static assets and `public/` for the host to place; the standalone
    # server expects them beside it, exactly as the web Dockerfile arranges.
    shutil.copytree(source / ".next" / "static", staging / ".next" / "static")
    if (source / "public").is_dir():
        shutil.copytree(source / "public", staging / "public")
    (staging / ".helios-build").write_text(web_source_fingerprint(source), encoding="utf-8")

    shutil.rmtree(paths.web_server, ignore_errors=True)
    staging.rename(paths.web_server)


def _run_logged(command: Sequence[str], *, cwd: Path, log_name: str) -> None:
    paths = _active_paths()
    with open_log(paths.logs, log_name) as log:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            stdout=log,
            stderr=subprocess.STDOUT,
            env={**os.environ, "NEXT_TELEMETRY_DISABLED": "1"},
            creationflags=_no_window_flags(),
            check=False,
        )
    if completed.returncode != 0:
        raise DesktopError(
            f"`{Path(command[0]).name} {' '.join(command[1:])}` failed "
            f"with exit code {completed.returncode}. See {paths.logs / (log_name + '.log')}."
        )


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def open_log(directory: Path, name: str) -> Iterator[IO[bytes]]:
    handle = open_log_handle(directory, name)
    try:
        yield handle
    finally:
        handle.close()


def open_log_handle(directory: Path, name: str) -> IO[bytes]:
    """Append to ``<name>.log``, rotating it to ``<name>.1.log`` once it grows past the cap."""

    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.log"
    if path.is_file() and path.stat().st_size > LOG_ROTATE_BYTES:
        path.replace(directory / f"{name}.1.log")
    handle = path.open("ab")
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    handle.write(f"\n===== {stamp} {name} starting =====\n".encode())
    handle.flush()
    return handle


# ---------------------------------------------------------------------------
# Supervision
# ---------------------------------------------------------------------------


class ProcessLike(Protocol):
    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    command: tuple[str, ...]
    env: Mapping[str, str]
    cwd: Path
    ready_url: str | None = None
    ready_timeout: float = 60.0


@dataclass
class RestartBudget:
    """At most ``limit`` restarts inside any ``window_seconds``."""

    limit: int = RESTART_LIMIT
    window_seconds: float = RESTART_WINDOW_SECONDS
    _events: deque[float] = field(default_factory=deque)

    def spend(self, now: float) -> bool:
        while self._events and self._events[0] < now - self.window_seconds:
            self._events.popleft()
        if len(self._events) >= self.limit:
            return False
        self._events.append(now)
        return True


Spawner = Callable[[ServiceSpec], ProcessLike]
ReadyProbe = Callable[[ServiceSpec], bool]


class Supervisor:
    """Starts services in order, restarts them when they exit, and stops them on the way out.

    Deterministic on purpose: :meth:`check_once` does one pass and returns what it restarted,
    so the restart policy is testable without threads, and :meth:`run` merely calls it in a loop.
    """

    def __init__(
        self,
        specs: Sequence[ServiceSpec],
        *,
        spawn: Spawner,
        ready: ReadyProbe,
        notify: Callable[[str], None] = lambda _message: None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._order = [spec.name for spec in specs]
        self._specs = {spec.name: spec for spec in specs}
        self._spawn = spawn
        self._ready = ready
        self._notify = notify
        self._clock = clock
        self._processes: dict[str, ProcessLike] = {}
        self._budgets = {spec.name: RestartBudget() for spec in specs}
        self._failed: set[str] = set()
        self._lock = threading.Lock()
        self._stopping = threading.Event()

    @property
    def failed(self) -> frozenset[str]:
        return frozenset(self._failed)

    def running(self, name: str) -> bool:
        process = self._processes.get(name)
        return process is not None and process.poll() is None

    def start_all(self) -> None:
        for name in self._order:
            self._start(name)

    def _start(self, name: str) -> None:
        spec = self._specs[name]
        logger.info("starting %s", name)
        self._processes[name] = self._spawn(spec)
        if spec.ready_url is not None and not self._ready(spec):
            raise DesktopError(
                f"The {name} service did not become ready within {int(spec.ready_timeout)}s. "
                f"Its log is {name}.log in the Helios logs folder."
            )

    def _stop(self, name: str, *, timeout: float = 10.0) -> None:
        process = self._processes.pop(name, None)
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=timeout)

    def check_once(self) -> list[str]:
        """Restart anything that has exited. Returns the services restarted, in order."""

        with self._lock:
            if self._stopping.is_set():
                return []
            exited = [
                name
                for name in self._order
                if name not in self._failed
                and name in self._processes
                and self._processes[name].poll() is not None
            ]
            if not exited:
                return []

            to_restart: list[str] = []
            for name in exited:
                for candidate in (name, *DEPENDENTS.get(name, ())):
                    if candidate not in to_restart and candidate not in self._failed:
                        to_restart.append(candidate)
            to_restart.sort(key=self._order.index)

            restarted: list[str] = []
            for name in to_restart:
                if not self._budgets[name].spend(self._clock()):
                    self._failed.add(name)
                    self._stop(name)
                    self._notify(
                        f"The {name} service keeps stopping, so Helios has left it down. "
                        f"Its log is {name}.log in the Helios logs folder."
                    )
                    continue
                self._stop(name)
                try:
                    self._start(name)
                except DesktopError as exc:
                    self._notify(str(exc))
                    continue
                restarted.append(name)
            return restarted

    def run(self, stop: threading.Event, interval: float = 1.0) -> None:
        while not stop.wait(interval):
            try:
                self.check_once()
            except Exception:  # the supervisor itself must never die silently
                logger.exception("supervisor pass failed")

    def stop_all(self) -> None:
        with self._lock:
            self._stopping.set()
            for name in reversed(self._order):
                self._stop(name)


def _no_window_flags() -> int:
    return CREATE_NO_WINDOW if sys.platform == "win32" else 0


class KillOnCloseJob:
    """A Windows job object that takes its processes down with the launcher.

    Without it, killing the launcher from Task Manager would leave three orphaned servers
    holding ports 8001 and 3001, and the next launch would report them as "already in use".
    A no-op on other platforms, and best-effort everywhere: failing to create one is logged,
    not fatal.
    """

    def __init__(self) -> None:
        self._handle: int | None = None
        if sys.platform != "win32":
            return
        try:
            self._handle = _create_kill_on_close_job()
        except OSError:
            logger.warning("could not create a job object; children may outlive the launcher")

    def adopt(self, process: subprocess.Popen[bytes]) -> None:
        if self._handle is None or sys.platform != "win32":
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        process_handle = int(process._handle)  # type: ignore[attr-defined,unused-ignore]
        if not kernel32.AssignProcessToJobObject(self._handle, process_handle):
            logger.warning("could not assign %s to the job object", process.pid)


def _create_kill_on_close_job() -> int:
    if sys.platform != "win32":
        raise OSError("job objects exist only on Windows")
    from ctypes import wintypes

    class IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimits),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    job_object_limit_kill_on_job_close = 0x2000
    job_object_extended_limit_information = 9

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    limits = ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = job_object_limit_kill_on_job_close
    if not kernel32.SetInformationJobObject(
        handle, job_object_extended_limit_information, ctypes.byref(limits), ctypes.sizeof(limits)
    ):
        raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
    return int(handle)


def child_python() -> str:
    """A console-subsystem interpreter for the child processes.

    The shortcut launches this module under ``pythonw.exe``; its children are started from
    ``python.exe`` beside it (with no console window) so their output reaches the log files.
    """

    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        sibling = executable.with_name("python.exe")
        if sibling.is_file():
            return str(sibling)
    return str(executable)


def build_service_specs(
    paths: DesktopPaths, ports: Ports, *, python: str, node: str
) -> list[ServiceSpec]:
    base_env = {
        key: value for key, value in os.environ.items() if not key.startswith("HELIOS_SETTINGS_")
    }
    backend_env = {
        **base_env,
        # One `.env`, whatever directory a shortcut happens to start in.
        "HELIOS_ENV_FILE": str(paths.env_file),
        # The launcher restarts the API when it exits; see the module docstring.
        "HELIOS_RESTART_MODE": "exit",
        "PYTHONUNBUFFERED": "1",
    }
    return [
        ServiceSpec(
            name="api",
            command=(
                python,
                "-m",
                "uvicorn",
                "helios.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(ports.api),
            ),
            env=backend_env,
            cwd=paths.root,
            ready_url=f"{ports.api_url}/health",
            # First start runs every migration; a long history can take a while.
            ready_timeout=180.0,
        ),
        ServiceSpec(
            name="worker",
            command=(python, "-m", "helios.worker"),
            env=backend_env,
            cwd=paths.root,
        ),
        ServiceSpec(
            name="web",
            command=(node, str(paths.web_server / "server.js")),
            env={
                **base_env,
                "HELIOS_API_URL": ports.api_url,
                "PORT": str(ports.web),
                "HOSTNAME": "127.0.0.1",
                "NODE_ENV": "production",
                "NEXT_TELEMETRY_DISABLED": "1",
            },
            cwd=paths.web_server,
            ready_url=ports.web_url,
            ready_timeout=60.0,
        ),
    ]


def make_spawner(paths: DesktopPaths, job: KillOnCloseJob) -> Spawner:
    def spawn(spec: ServiceSpec) -> ProcessLike:
        log = open_log_handle(paths.logs, spec.name)
        try:
            process = subprocess.Popen(
                list(spec.command),
                cwd=spec.cwd,
                env=dict(spec.env),
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=_no_window_flags(),
            )
        finally:
            # The child holds its own handle now; the launcher's copy is not needed.
            log.close()
        job.adopt(process)
        return process

    return spawn


def probe_ready(spec: ServiceSpec) -> bool:
    if spec.ready_url is None:
        return True
    url = spec.ready_url
    return wait_until(lambda: http_ok(url), timeout=spec.ready_timeout)


# ---------------------------------------------------------------------------
# The window, the tray, the icon
# ---------------------------------------------------------------------------


def browser_candidates() -> list[Path]:
    candidates: list[Path] = []
    for variable in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        base = os.environ.get(variable)
        if base:
            candidates.append(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
            candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    for name in ("msedge", "microsoft-edge", "google-chrome", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    return candidates


def find_app_browser(candidates: Sequence[Path] | None = None) -> Path | None:
    """The first Chromium browser present, which is all app mode needs."""

    for candidate in candidates if candidates is not None else browser_candidates():
        if candidate.is_file():
            return candidate
    return None


def app_window_command(browser: Path, url: str, profile: Path) -> list[str]:
    return [
        str(browser),
        f"--app={url}",
        # A profile of its own: the window never sees your normal browsing profile, and a
        # second launch finds this instance rather than your everyday browser.
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        # Maximised rather than a fixed size: a fixed 1440x920 overflowed a 1536x960 laptop
        # screen once the taskbar was subtracted, cutting the nav off at the right edge.
        "--start-maximized",
        # The page opts out of translation too; this stops Edge offering it at all.
        "--disable-features=Translate,msEdgeTranslate",
    ]


def open_app_window(paths: DesktopPaths, url: str) -> None:
    browser = find_app_browser()
    if browser is None:
        webbrowser.open(url)
        return
    paths.browser_profile.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        app_window_command(browser, url, paths.browser_profile),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def draw_icon(size: int) -> Any:
    """The Helios mark from `web/app/icon.svg`, drawn with Pillow at any size."""

    from PIL import Image, ImageDraw

    scale = size / 64
    image = Image.new("RGBA", (size, size), (9, 9, 11, 255))
    draw = ImageDraw.Draw(image)
    lime = (204, 255, 0, 255)
    amber = (255, 176, 0, 255)
    rays = [
        (32, 5, 32, 16),
        (32, 48, 32, 59),
        (5, 32, 16, 32),
        (48, 32, 59, 32),
        (13, 13, 21, 21),
        (43, 43, 51, 51),
        (51, 13, 43, 21),
        (21, 43, 13, 51),
    ]
    for x1, y1, x2, y2 in rays:
        draw.line(
            [(x1 * scale, y1 * scale), (x2 * scale, y2 * scale)],
            fill=lime,
            width=max(1, round(3 * scale)),
        )
    radius = 12 * scale
    centre = 32 * scale
    draw.ellipse(
        [centre - radius, centre - radius, centre + radius, centre + radius],
        outline=amber,
        width=max(1, round(4 * scale)),
    )
    return image


def write_icon(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    draw_icon(256).save(
        path,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    return path


def alert(message: str, *, error: bool = True) -> None:
    """Tell the operator something that needs their attention.

    A dialog only when there is no console at all -- the shortcut runs under ``pythonw``, where
    ``sys.stderr`` is ``None``. Anywhere else, including a redirected or backgrounded console,
    the message is printed: a modal dialog nobody is looking at would block startup forever.
    """

    (logger.error if error else logger.info)(message)
    if sys.stderr is not None:
        print(message, file=sys.stderr)
        return
    if sys.platform == "win32":
        flags = 0x10 if error else 0x40  # MB_ICONERROR / MB_ICONINFORMATION
        ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, flags)


def console_notice(message: str) -> None:
    """A progress notice in headless mode: logged and printed, never a dialog."""

    logger.info(message)
    if sys.stdout is not None:
        print(message, flush=True)


# ---------------------------------------------------------------------------
# Single instance
# ---------------------------------------------------------------------------


class InstanceLock:
    """Held for the launcher's lifetime, so a second double-click opens a window instead."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle: IO[bytes] | None = None

    def acquire(self) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("a+b")
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


# ---------------------------------------------------------------------------
# Shortcuts
# ---------------------------------------------------------------------------


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


#: Special folders the shortcuts go in, by their `[Environment]::GetFolderPath` names. Resolved
#: by Windows rather than built from %APPDATA%, because a OneDrive-redirected Desktop lives
#: somewhere else entirely.
LAUNCH_FOLDERS: Final = ("Desktop", "Programs")
STARTUP_FOLDER: Final = "Startup"

#: At login Helios starts in the tray without opening a window: the worker begins syncing, and
#: the window is one click away rather than in your face every time you sign in.
AUTOSTART_ARGUMENTS: Final = "-m helios.desktop --no-window"


def shortcut_script(
    *,
    target: str,
    arguments: str,
    working_dir: str,
    icon: str,
    folders: Sequence[str] = LAUNCH_FOLDERS,
) -> str:
    """PowerShell that writes a `Helios.lnk` into each of the given special folders.

    Every value is single-quoted with PowerShell's own escaping, so a path containing a quote
    or a space -- this project lives under `Portolio Tracker` -- cannot break out of it. The
    folder names are this module's constants, never input.
    """

    fields = "\n".join(
        [
            f"  $s.TargetPath = {_powershell_quote(target)}",
            f"  $s.Arguments = {_powershell_quote(arguments)}",
            f"  $s.WorkingDirectory = {_powershell_quote(working_dir)}",
            f"  $s.IconLocation = {_powershell_quote(icon)}",
            f"  $s.Description = {_powershell_quote('Helios portfolio intelligence')}",
        ]
    )
    folder_list = ", ".join(
        f"[Environment]::GetFolderPath({_powershell_quote(folder)})" for folder in folders
    )
    return (
        "$shell = New-Object -ComObject WScript.Shell\n"
        f"foreach ($folder in @({folder_list})) {{\n"
        "  $s = $shell.CreateShortcut((Join-Path $folder 'Helios.lnk'))\n"
        f"{fields}\n"
        "  $s.Save()\n"
        "  Write-Output (Join-Path $folder 'Helios.lnk')\n"
        "}\n"
    )


def _write_shortcuts(paths: DesktopPaths, *, arguments: str, folders: Sequence[str]) -> list[str]:
    if sys.platform != "win32":
        raise DesktopError("Shortcuts are only implemented for Windows.")
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        raise DesktopError(f"pythonw.exe was not found next to {sys.executable}.")
    icon = write_icon(paths.icon)
    script = shortcut_script(
        target=str(pythonw),
        arguments=arguments,
        working_dir=str(paths.root),
        icon=str(icon),
        folders=folders,
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
        creationflags=_no_window_flags(),
    )
    if completed.returncode != 0:
        raise DesktopError(f"Could not create the shortcut: {completed.stderr.strip()}")
    return [line for line in completed.stdout.splitlines() if line.strip()]


def install_shortcuts(paths: DesktopPaths) -> list[str]:
    return _write_shortcuts(paths, arguments="-m helios.desktop", folders=LAUNCH_FOLDERS)


def startup_shortcut(environ: Mapping[str, str] | None = None) -> Path | None:
    """Where the login shortcut lives, or None off Windows."""

    appdata = (environ if environ is not None else os.environ).get("APPDATA", "").strip()
    if sys.platform != "win32" or not appdata:
        return None
    return (
        Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    ) / "Helios.lnk"


def autostart_enabled() -> bool:
    link = startup_shortcut()
    return link is not None and link.is_file()


def set_autostart(paths: DesktopPaths, enabled: bool) -> str:
    """Add or remove the login shortcut. Returns a sentence saying what changed."""

    link = startup_shortcut()
    if link is None:
        raise DesktopError("Starting with Windows is only available on Windows.")
    if enabled:
        _write_shortcuts(paths, arguments=AUTOSTART_ARGUMENTS, folders=(STARTUP_FOLDER,))
        return "Helios will start in the tray when you sign in."
    link.unlink(missing_ok=True)
    return "Helios will no longer start when you sign in."


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------


_PATHS: DesktopPaths | None = None


def _active_paths() -> DesktopPaths:
    if _PATHS is None:
        raise RuntimeError("desktop paths used before the launcher configured them")
    return _PATHS


class DesktopApp:
    """Ties it together: build if needed, start the services, open the window, supervise."""

    def __init__(self, paths: DesktopPaths, ports: Ports, *, open_window: bool = True) -> None:
        self.paths = paths
        self.ports = ports
        self.open_window = open_window
        self.stop_event = threading.Event()
        self.supervisor: Supervisor | None = None
        self._notify: Callable[[str], None] = console_notice
        self._status: Callable[[str], None] = lambda _status: None

    def bind_tray(self, notify: Callable[[str], None], status: Callable[[str], None]) -> None:
        self._notify = notify
        self._status = status

    def start(self) -> None:
        if not web_build_is_current(self.paths):
            self._status("building the dashboard")
            self._notify("Building the dashboard. The first launch after an update takes a minute.")
            build_web(self.paths)
        node = find_executable("node")
        self._status("starting")
        specs = build_service_specs(self.paths, self.ports, python=child_python(), node=node)
        self.supervisor = Supervisor(
            specs,
            spawn=make_spawner(self.paths, KillOnCloseJob()),
            ready=probe_ready,
            notify=self._notify,
        )
        self.supervisor.start_all()
        threading.Thread(
            target=self.supervisor.run,
            args=(self.stop_event,),
            name="helios-supervisor",
            daemon=True,
        ).start()
        self._status("running")
        if self.open_window:
            self.show()

    def show(self) -> None:
        open_app_window(self.paths, self.ports.web_url)

    def open_logs(self) -> None:
        self.paths.logs.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(self.paths.logs)
        else:
            webbrowser.open(self.paths.logs.as_uri())

    def stop(self) -> None:
        self.stop_event.set()
        if self.supervisor is not None:
            self.supervisor.stop_all()


def run_with_tray(app: DesktopApp) -> None:
    import pystray

    def status(text: str) -> None:
        icon.title = f"{APP_NAME} — {text}"

    def notify(message: str) -> None:
        logger.info(message)
        try:
            icon.notify(message, APP_NAME)
        except Exception:  # a notification is a courtesy; never let it break startup
            logger.warning("tray notification failed: %s", message)

    def on_open(_icon: Any, _item: Any) -> None:
        app.show()

    def on_logs(_icon: Any, _item: Any) -> None:
        app.open_logs()

    def on_autostart(_icon: Any, _item: Any) -> None:
        try:
            notify(set_autostart(app.paths, not autostart_enabled()))
        except DesktopError as exc:
            notify(str(exc))

    def on_quit(_icon: Any, _item: Any) -> None:
        status("stopping")
        app.stop()
        icon.stop()

    icon = pystray.Icon(
        "helios",
        draw_icon(64),
        f"{APP_NAME} — starting",
        menu=pystray.Menu(
            pystray.MenuItem("Open Helios", on_open, default=True),
            pystray.MenuItem("Open logs folder", on_logs),
            pystray.MenuItem(
                "Start with Windows",
                on_autostart,
                checked=lambda _item: autostart_enabled(),
                visible=sys.platform == "win32",
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Helios", on_quit),
        ),
    )
    app.bind_tray(notify, status)

    def setup(tray: Any) -> None:
        tray.visible = True
        try:
            app.start()
        except Exception as exc:
            logger.exception("startup failed")
            app.stop()
            alert(str(exc) if isinstance(exc, DesktopError) else f"Helios could not start: {exc}")
            tray.stop()

    icon.run(setup=setup)


def run_headless(app: DesktopApp) -> None:
    app.start()
    print(f"Helios is running at {app.ports.web_url}. Press Ctrl+C to stop.", flush=True)
    try:
        while not app.stop_event.wait(1.0):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()


def _configure_logging(paths: DesktopPaths) -> None:
    paths.logs.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(paths.logs / "desktop.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="helios-desktop", description="Run Helios as a desktop app."
    )
    parser.add_argument(
        "--install-shortcut",
        action="store_true",
        help="create Desktop and Start Menu shortcuts, then exit",
    )
    parser.add_argument(
        "--autostart",
        choices=("on", "off"),
        help="start Helios in the tray when you sign in to Windows (on), or stop doing so (off)",
    )
    parser.add_argument("--build", action="store_true", help="rebuild the dashboard, then exit")
    parser.add_argument(
        "--no-window", action="store_true", help="start the services without opening a window"
    )
    parser.add_argument(
        "--no-tray", action="store_true", help="run in this console instead of the system tray"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    global _PATHS
    args = build_parser().parse_args(argv)
    try:
        paths = DesktopPaths(root=find_project_root(), runtime=default_runtime_dir())
        _PATHS = paths
        _configure_logging(paths)
        # Relative settings (data/, config/) resolve against the checkout, whatever directory
        # the shortcut or terminal started in.
        os.chdir(paths.root)

        if args.install_shortcut:
            for created in install_shortcuts(paths):
                print(f"created {created}")
            return 0
        if args.autostart is not None:
            print(set_autostart(paths, args.autostart == "on"))
            return 0
        if args.build:
            build_web(paths)
            print(f"dashboard built into {paths.web_server}")
            return 0

        ports = resolve_ports(os.environ, read_env_file(paths.env_file))
        lock = InstanceLock(paths.lock_file)
        if not lock.acquire():
            # Another launcher owns the services; all this double-click needs is a window.
            open_app_window(paths, ports.web_url)
            return 0
        try:
            for port, label in ((ports.api, "HELIOS_API_PORT"), (ports.web, "HELIOS_WEB_PORT")):
                if port_in_use(port):
                    if port == ports.api and is_helios_api(ports.api_url):
                        raise DesktopError(
                            f"Helios is already running on port {port}, probably under Docker. "
                            "Stop it with `docker compose down` first, or set HELIOS_API_PORT "
                            "and HELIOS_WEB_PORT in .env to different ports."
                        )
                    raise DesktopError(
                        f"Port {port} is already in use by another program. Set {label} in "
                        f"{paths.env_file} to a free port."
                    )

            app = DesktopApp(paths, ports, open_window=not args.no_window)
            if args.no_tray:
                run_headless(app)
                return 0
            try:
                import pystray  # noqa: F401
            except ImportError:
                logger.warning("pystray is not installed; running without a tray icon")
                run_headless(app)
                return 0
            run_with_tray(app)
            return 0
        finally:
            lock.release()
    except DesktopError as exc:
        alert(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
