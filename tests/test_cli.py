from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
import pytest
from agy_pilot.cli import (
    main,
    update_application,
)
from agy_pilot.service import SERVICE_LABEL


def test_update_application_git_repo(tmp_path, monkeypatch):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    monkeypatch.chdir(tmp_path)

    with patch("os.system", return_value=0) as mock_sys:
        update_application()
        assert mock_sys.called
        calls = [c[0][0] for c in mock_sys.call_args_list]
        assert any("git pull" in call for call in calls)

def test_update_application_standalone(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # No .git dir

    with patch("os.system", return_value=0) as mock_sys:
        update_application()
        assert mock_sys.called

def test_main_normal_run(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:mock_token_for_test")
    with patch("sys.argv", ["agy-pilot"]), \
         patch("agy_pilot.cli.Antigravity2Controller.discover") as mock_disc, \
         patch("agy_pilot.cli.SessionStore") as mock_store, \
         patch("agy_pilot.cli.get_adapter") as mock_get_adapter, \
         patch("asyncio.run") as mock_run:
        mock_adapter = MagicMock()
        mock_get_adapter.return_value = mock_adapter
        main()
        mock_disc.assert_called_once()
        mock_get_adapter.assert_called_once()
        mock_run.assert_called_once()


def test_main_clean_arg(monkeypatch):
    with patch("sys.argv", ["agy-pilot", "clean", "--all"]), \
         patch("agy_pilot.cli.clean_resources") as mock_clean:
        main()
        mock_clean.assert_called_once_with(all_data=True, force=False)


def test_clean_resources(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)

    fake_log_dir = tmp_path / "Logs" / "agy-pilot"
    fake_log_dir.mkdir(parents=True, exist_ok=True)
    out_log = fake_log_dir / "agy-pilot.stdout.log"
    out_log.write_text("logs")

    monkeypatch.setattr("agy_pilot.cli.DEFAULT_LOG_DIR", fake_log_dir)

    launch_agents = tmp_path / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True, exist_ok=True)
    plist = launch_agents / f"{SERVICE_LABEL}.plist"
    plist.write_text("dummy")

    db_dir = tmp_path / "app_data"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_file = db_dir / "agy_session.db"
    db_file.write_text("db")
    monkeypatch.setattr("agy_pilot.cli.DEFAULT_DATA_DIR", db_dir)

    fake_config = tmp_path / "config.env"
    fake_config.write_text("dummy")
    monkeypatch.setattr("agy_pilot.cli.DEFAULT_CONFIG_FILE", fake_config)

    from agy_pilot.cli import clean_resources
    with patch("os.system", return_value=0):
        clean_resources(all_data=True, force=True)

    assert not plist.exists()
    assert not out_log.exists()
    assert not db_file.exists()


def test_init_configuration(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.env"
    monkeypatch.setattr("agy_pilot.cli.DEFAULT_CONFIG_DIR", tmp_path)
    monkeypatch.setattr("agy_pilot.cli.DEFAULT_CONFIG_FILE", cfg_file)

    inputs = iter(["123456:valid_token", "111, 222", "n"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))

    from agy_pilot.cli import init_configuration
    init_configuration()

    assert cfg_file.is_file()
    content = cfg_file.read_text()
    assert "123456:valid_token" in content
    assert "111,222" in content


def test_main_service_subcommands(monkeypatch):
    from agy_pilot.service import ServiceStatus

    mock_mgr = MagicMock()
    mock_mgr.status.return_value = ServiceStatus(installed=True, running=True, pid=1234, exit_code=0, service_type="launchd")
    monkeypatch.setattr("agy_pilot.cli.get_service_manager", lambda: mock_mgr)

    with patch("sys.argv", ["agy-pilot", "service", "install"]):
        main()
        mock_mgr.install.assert_called_once_with(auto_start=True)

    with patch("sys.argv", ["agy-pilot", "service", "status"]):
        main()
        mock_mgr.status.assert_called_once()

    with patch("sys.argv", ["agy-pilot", "service", "restart"]):
        main()
        mock_mgr.restart.assert_called_once()

    with patch("sys.argv", ["agy-pilot", "service", "uninstall"]):
        main()
        mock_mgr.uninstall.assert_called_once()
