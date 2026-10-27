import sys
from pathlib import Path
from unittest.mock import patch

from agy_pilot.service import (
    LaunchdManager,
    SystemdManager,
    get_executable_command,
    get_service_manager,
)


def test_get_executable_command(monkeypatch):
    monkeypatch.setattr(
        "shutil.which", lambda cmd: "/usr/local/bin/agy-pilot" if cmd == "agy-pilot" else None
    )
    cmd = get_executable_command()
    assert cmd == ["/usr/local/bin/agy-pilot"]

    monkeypatch.setattr("shutil.which", lambda cmd: None)
    fallback = get_executable_command()
    assert fallback == [sys.executable, "-m", "agy_pilot.cli"]


def test_launchd_manager(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    mgr = LaunchdManager()

    with patch("os.system", return_value=0):
        assert mgr.install(auto_start=True) is True
        assert mgr.plist_path.is_file()
        content = mgr.plist_path.read_text()
        assert "com.ii2d.agy-pilot" in content

        assert mgr.restart() is True
        assert mgr.stop() is True
        assert mgr.uninstall() is True
        assert not mgr.plist_path.exists()


def test_systemd_manager(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    mgr = SystemdManager()

    with patch("os.system", return_value=0):
        assert mgr.install(auto_start=True) is True
        assert mgr.unit_path.is_file()
        content = mgr.unit_path.read_text()
        assert "Antigravity Pilot" in content

        assert mgr.restart() is True
        assert mgr.stop() is True
        assert mgr.uninstall() is True
        assert not mgr.unit_path.exists()


def test_get_service_manager():
    mgr = get_service_manager()
    if sys.platform == "darwin":
        assert isinstance(mgr, LaunchdManager)
    else:
        assert isinstance(mgr, SystemdManager)
