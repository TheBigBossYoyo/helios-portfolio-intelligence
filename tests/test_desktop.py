"""The desktop launcher: supervision policy, build staleness, ports, window and shortcut plumbing.

The launcher's value is in its failure behaviour -- a crash-looping service left down with a
notice rather than restarted forever, a settings restart taking the worker with it, a rebuild
only when the dashboard actually changed -- so that is what these pin. Everything that would
touch a real process, browser, or the user's Desktop is injected or faked.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from helios.desktop import (
    DesktopError,
    DesktopPaths,
    Ports,
    RestartBudget,
    ServiceSpec,
    Supervisor,
    app_window_command,
    build_service_specs,
    find_app_browser,
    find_project_root,
    read_env_file,
    resolve_ports,
    shortcut_script,
    web_build_is_current,
    web_source_fingerprint,
)


class FakeProcess:
    """Stands in for `subprocess.Popen`: alive until told to exit."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.returncode: int | None = None
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if self.returncode is None:
            raise subprocess.TimeoutExpired(self.name, 0)
        return self.returncode


class Harness:
    """A supervisor over three fake services, with a controllable clock and a spawn log."""

    def __init__(self, *, ready: bool = True) -> None:
        self.now = 0.0
        self.spawned: list[str] = []
        self.current: dict[str, FakeProcess] = {}
        self.notices: list[str] = []
        self.ready = ready
        specs = [
            ServiceSpec("api", ("api",), {}, Path("."), ready_url="http://api/health"),
            ServiceSpec("worker", ("worker",), {}, Path(".")),
            ServiceSpec("web", ("web",), {}, Path("."), ready_url="http://web/"),
        ]
        self.supervisor = Supervisor(
            specs,
            spawn=self._spawn,
            ready=lambda _spec: self.ready,
            notify=self.notices.append,
            clock=lambda: self.now,
        )

    def _spawn(self, spec: ServiceSpec) -> FakeProcess:
        process = FakeProcess(spec.name)
        self.spawned.append(spec.name)
        self.current[spec.name] = process
        return process

    def exit(self, name: str, code: int = 0) -> None:
        self.current[name].returncode = code


def test_services_start_in_dependency_order() -> None:
    harness = Harness()
    harness.supervisor.start_all()
    assert harness.spawned == ["api", "worker", "web"]


def test_startup_fails_loudly_when_a_service_never_becomes_ready() -> None:
    harness = Harness(ready=False)
    with pytest.raises(DesktopError, match="did not become ready"):
        harness.supervisor.start_all()


def test_an_api_restart_takes_the_worker_with_it() -> None:
    """The Restart button exits the API to apply settings; the worker read them at startup too.

    Restarting only the API would leave the worker syncing with the old configuration -- the
    exact gap the README used to tell you to close by hand.
    """
    harness = Harness()
    harness.supervisor.start_all()
    old_worker = harness.current["worker"]

    harness.exit("api", code=15)
    restarted = harness.supervisor.check_once()

    assert restarted == ["api", "worker"]
    assert old_worker.terminated
    assert harness.supervisor.running("api")
    assert harness.supervisor.running("worker")
    # The dashboard never depended on the API's process, only its address.
    assert harness.spawned.count("web") == 1


def test_a_worker_exit_restarts_only_the_worker() -> None:
    harness = Harness()
    harness.supervisor.start_all()

    harness.exit("worker", code=1)

    assert harness.supervisor.check_once() == ["worker"]


def test_nothing_is_restarted_while_everything_runs() -> None:
    harness = Harness()
    harness.supervisor.start_all()
    assert harness.supervisor.check_once() == []


def test_a_crash_looping_service_is_left_down_with_a_notice() -> None:
    """Restarting a broken service forever would hide the failure behind a spinning icon."""
    harness = Harness()
    harness.supervisor.start_all()

    for _ in range(RestartBudget().limit):
        harness.exit("web", code=1)
        assert harness.supervisor.check_once() == ["web"]
        harness.now += 1

    harness.exit("web", code=1)
    assert harness.supervisor.check_once() == []
    assert "web" in harness.supervisor.failed
    assert any("keeps stopping" in notice for notice in harness.notices)
    # Once given up on, it stays down rather than being retried on every pass.
    assert harness.supervisor.check_once() == []


def test_restart_budget_recovers_after_the_window() -> None:
    budget = RestartBudget(limit=2, window_seconds=10)
    assert budget.spend(0)
    assert budget.spend(1)
    assert not budget.spend(2)
    assert budget.spend(12)


def test_stop_all_terminates_in_reverse_order_and_ends_supervision() -> None:
    harness = Harness()
    harness.supervisor.start_all()
    processes = dict(harness.current)

    harness.supervisor.stop_all()

    assert all(process.terminated for process in processes.values())
    # A stop must not be undone by the next supervisor pass mistaking it for a crash.
    assert harness.supervisor.check_once() == []


# ---------------------------------------------------------------------------
# Ports and configuration
# ---------------------------------------------------------------------------


def test_ports_come_from_the_environment_then_env_file_then_defaults() -> None:
    assert resolve_ports({}, {}) == Ports(api=8001, web=3001)
    assert resolve_ports({}, {"HELIOS_WEB_PORT": "3100"}) == Ports(api=8001, web=3100)
    assert resolve_ports({"HELIOS_WEB_PORT": "3200"}, {"HELIOS_WEB_PORT": "3100"}).web == 3200


@pytest.mark.parametrize(
    ("environ", "message"),
    [
        ({"HELIOS_API_PORT": "abc"}, "not a port number"),
        ({"HELIOS_API_PORT": "70000"}, "outside the valid port range"),
        ({"HELIOS_API_PORT": "3001"}, "must differ"),
    ],
)
def test_bad_ports_are_refused_with_a_reason(environ: dict[str, str], message: str) -> None:
    with pytest.raises(DesktopError, match=message):
        resolve_ports(environ, {})


def test_env_file_reader_ignores_comments_and_quotes(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        '# comment\nHELIOS_WEB_PORT="3100"\n\nnot a pair\nHELIOS_API_PORT=8100\n',
        encoding="utf-8",
    )
    assert read_env_file(env) == {"HELIOS_WEB_PORT": "3100", "HELIOS_API_PORT": "8100"}
    assert read_env_file(tmp_path / "missing.env") == {}


def test_service_specs_run_the_api_under_the_launcher_and_on_loopback(tmp_path: Path) -> None:
    paths = DesktopPaths(root=tmp_path / "repo", runtime=tmp_path / "runtime")
    specs = {
        spec.name: spec
        for spec in build_service_specs(paths, Ports(api=8101, web=3101), python="py", node="node")
    }

    api = specs["api"]
    assert api.command[-4:] == ("--host", "127.0.0.1", "--port", "8101")
    # The launcher is the supervisor, so the Restart button must exit rather than re-exec.
    assert api.env["HELIOS_RESTART_MODE"] == "exit"
    # One `.env` for reader and writer, independent of where the shortcut started.
    assert api.env["HELIOS_ENV_FILE"] == str(paths.env_file)
    assert specs["worker"].env["HELIOS_ENV_FILE"] == str(paths.env_file)

    web = specs["web"]
    assert web.env["HELIOS_API_URL"] == "http://127.0.0.1:8101"
    assert web.env["HOSTNAME"] == "127.0.0.1"
    assert web.env["PORT"] == "3101"


def test_compose_only_settings_do_not_leak_into_desktop_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shell that once ran Compose must not make the desktop settings page read-only."""
    monkeypatch.setenv("HELIOS_SETTINGS_WRITABLE", "false")
    monkeypatch.setenv("HELIOS_SETTINGS_TRUSTED_PEERS", "web")
    paths = DesktopPaths(root=tmp_path, runtime=tmp_path / "runtime")

    for spec in build_service_specs(paths, Ports(api=8101, web=3101), python="py", node="node"):
        assert "HELIOS_SETTINGS_WRITABLE" not in spec.env
        assert "HELIOS_SETTINGS_TRUSTED_PEERS" not in spec.env


def test_project_root_is_this_checkout() -> None:
    root = find_project_root()
    assert (root / "alembic.ini").is_file()
    assert (root / "web").is_dir()


def test_project_root_refuses_a_directory_that_is_not_helios(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HELIOS_HOME", str(tmp_path))
    monkeypatch.setattr("helios.desktop.__file__", str(tmp_path / "a" / "b" / "desktop.py"))
    with pytest.raises(DesktopError, match="Could not find the Helios folder"):
        find_project_root(start=tmp_path)


# ---------------------------------------------------------------------------
# The dashboard build
# ---------------------------------------------------------------------------


def _web_tree(root: Path) -> Path:
    web = root / "web"
    (web / "app").mkdir(parents=True)
    (web / "app" / "page.tsx").write_text("export default 1", encoding="utf-8")
    (web / "package.json").write_text("{}", encoding="utf-8")
    (web / "tests").mkdir()
    (web / "tests" / "a.test.ts").write_text("test", encoding="utf-8")
    return web


def test_fingerprint_follows_content_not_tests(tmp_path: Path) -> None:
    web = _web_tree(tmp_path)
    original = web_source_fingerprint(web)

    (web / "tests" / "a.test.ts").write_text("changed", encoding="utf-8")
    assert web_source_fingerprint(web) == original, "editing a test must not force a rebuild"

    (web / "app" / "page.tsx").write_text("export default 2", encoding="utf-8")
    assert web_source_fingerprint(web) != original


def test_build_is_current_only_with_a_server_and_a_matching_stamp(tmp_path: Path) -> None:
    web = _web_tree(tmp_path / "repo")
    paths = DesktopPaths(root=web.parent, runtime=tmp_path / "runtime")
    assert not web_build_is_current(paths)

    paths.web_server.mkdir(parents=True)
    (paths.web_server / "server.js").write_text("", encoding="utf-8")
    (paths.web_server / ".helios-build").write_text(web_source_fingerprint(web), encoding="utf-8")
    assert web_build_is_current(paths)

    (web / "app" / "page.tsx").write_text("edited", encoding="utf-8")
    assert not web_build_is_current(paths)


# ---------------------------------------------------------------------------
# Window and shortcuts
# ---------------------------------------------------------------------------


def test_app_window_uses_its_own_profile(tmp_path: Path) -> None:
    command = app_window_command(Path("msedge.exe"), "http://127.0.0.1:3001", tmp_path / "p")
    assert "--app=http://127.0.0.1:3001" in command
    assert f"--user-data-dir={tmp_path / 'p'}" in command
    assert "--start-maximized" in command


def test_first_present_browser_wins(tmp_path: Path) -> None:
    missing = tmp_path / "missing.exe"
    present = tmp_path / "chrome.exe"
    present.write_text("", encoding="utf-8")
    assert find_app_browser([missing, present]) == present
    assert find_app_browser([missing]) is None


def test_shortcut_script_quotes_paths_with_spaces_and_apostrophes() -> None:
    script = shortcut_script(
        target=r"C:\Users\O'Neil\Portolio Tracker\.venv\Scripts\pythonw.exe",
        arguments="-m helios.desktop",
        working_dir=r"C:\Users\O'Neil\Portolio Tracker",
        icon=r"C:\icons\helios.ico",
    )
    expected_target = r"'C:\Users\O''Neil\Portolio Tracker\.venv\Scripts\pythonw.exe'"
    assert f"$s.TargetPath = {expected_target}" in script
    assert "$s.Arguments = '-m helios.desktop'" in script
    assert "GetFolderPath('Desktop')" in script
    assert "GetFolderPath('Programs')" in script


def test_icon_renders_at_every_shortcut_size(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    from helios.desktop import draw_icon, write_icon

    assert draw_icon(64).size == (64, 64)
    icon = write_icon(tmp_path / "helios.ico")
    assert icon.stat().st_size > 0
    assert icon.read_bytes()[:4] == b"\x00\x00\x01\x00"  # ICO header


def test_alert_prints_instead_of_blocking_on_a_dialog_when_a_console_exists(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A modal dialog nobody is watching blocks startup forever.

    Found by running the launcher from a backgrounded shell: stderr was redirected rather than
    a TTY, the old check chose a MessageBox, and the build notice sat waiting for a click.
    """
    from types import SimpleNamespace

    from helios import desktop

    def no_dialog(*_args: object) -> None:
        raise AssertionError("a dialog was shown although a console exists")

    fake_ctypes = SimpleNamespace(
        windll=SimpleNamespace(user32=SimpleNamespace(MessageBoxW=no_dialog))
    )
    monkeypatch.setattr(desktop, "ctypes", fake_ctypes)
    monkeypatch.setattr(sys, "platform", "win32")

    desktop.alert("port busy")
    desktop.console_notice("building")

    captured = capsys.readouterr()
    assert "port busy" in captured.err
    assert "building" in captured.out


def test_autostart_shortcut_opens_no_window_and_targets_the_startup_folder() -> None:
    """At sign-in Helios should start syncing in the tray, not throw a window at you."""
    from helios.desktop import AUTOSTART_ARGUMENTS, STARTUP_FOLDER

    script = shortcut_script(
        target="pythonw.exe",
        arguments=AUTOSTART_ARGUMENTS,
        working_dir="C:/helios",
        icon="C:/helios.ico",
        folders=(STARTUP_FOLDER,),
    )
    assert "--no-window" in AUTOSTART_ARGUMENTS
    assert "GetFolderPath('Startup')" in script
    assert "GetFolderPath('Desktop')" not in script


def test_startup_shortcut_path_comes_from_appdata(monkeypatch: pytest.MonkeyPatch) -> None:
    from helios.desktop import startup_shortcut

    monkeypatch.setattr(sys, "platform", "win32")
    link = startup_shortcut({"APPDATA": r"C:\Users\x\AppData\Roaming"})
    assert link is not None
    assert link.parts[-3:] == ("Programs", "Startup", "Helios.lnk")
    assert startup_shortcut({}) is None


def test_notification_relay_shows_each_pending_item_once_and_acknowledges_it() -> None:
    from helios.desktop import NotificationRelay

    pending = [{"id": index, "title": f"T{index}", "body": "x" * 400} for index in range(1, 6)]
    shown: list[tuple[str, str]] = []
    acked: list[int] = []
    relay = NotificationRelay(
        "http://api",
        lambda title, body: shown.append((title, body)),
        fetch=lambda _url: pending,
        ack=lambda _url, identifier: acked.append(identifier),
    )

    assert relay.poll_once() == 3

    # The newest three, clipped to what Windows shows, then one line for the rest.
    assert [title for title, _ in shown] == ["T1", "T2", "T3", "Helios"]
    assert len(shown[0][1]) == 250
    assert shown[-1][1] == "2 more notification(s) are waiting in Helios."
    assert acked == [1, 2, 3, 4, 5]


def test_notification_relay_waits_quietly_while_the_api_is_down() -> None:
    import urllib.error

    from helios.desktop import NotificationRelay

    def down(_url: str) -> list[dict[str, object]]:
        raise urllib.error.URLError("refused")

    relay = NotificationRelay("http://api", lambda *_: None, fetch=down, ack=lambda *_: None)

    assert relay.poll_once() == 0
