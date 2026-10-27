from pathlib import Path
from unittest.mock import MagicMock, patch

from agy_pilot.discovery import Discovery

SAMPLE_PS_OUTPUT_BRIDGE = """
 1234 /Applications/Antigravity 2.app/Contents/Resources/bin/language_server --subclient_type hub --host_bridge_url=http://127.0.0.1:51234 --csrf_token a1b2c3d4-e5f6-7890-abcd-ef1234567890
 5678 /usr/bin/some_other_process
"""

SAMPLE_PS_OUTPUT_PORT = """
 9999 /Applications/Antigravity 2.app/Contents/Resources/bin/language_server --subclient_type hub --port 54321 --csrf_token 11223344-5566-7788-99aa-bbccddeeff00
"""


def test_extract_hub_info_with_bridge_url():
    addr, token = Discovery.extract_hub_info(SAMPLE_PS_OUTPUT_BRIDGE)
    assert addr == "127.0.0.1:51236"  # 51234 + 2
    assert token == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


def test_extract_hub_info_with_port():
    addr, token = Discovery.extract_hub_info(SAMPLE_PS_OUTPUT_PORT)
    assert addr == "127.0.0.1:54321"
    assert token == "11223344-5566-7788-99aa-bbccddeeff00"


def test_extract_hub_info_none():
    addr, token = Discovery.extract_hub_info("1234 /bin/bash\n5678 /bin/zsh")
    assert addr is None
    assert token is None


def test_extract_csrf_token():
    line = "language_server subclient_type hub --csrf_token fedcba98-7654-3210"
    assert Discovery.extract_csrf_token(line) == "fedcba98-7654-3210"
    assert Discovery.extract_csrf_token("nothing here") is None


def test_extract_hub_address():
    line = "language_server ANTIGRAVITY_LS_ADDRESS=127.0.0.1:50051"
    assert Discovery.extract_hub_address(line) == "127.0.0.1:50051"
    assert Discovery.extract_hub_address("nothing here") is None


def test_discover_executable_via_env(tmp_path, monkeypatch):
    dummy_exe = tmp_path / "custom_ls"
    dummy_exe.write_text("#!/bin/sh\n")
    monkeypatch.setenv("ANTIGRAVITY_AGENTAPI_EXE", str(dummy_exe))

    found = Discovery.discover_executable()
    assert found == dummy_exe


def test_discover_executable_fallback_or_none(monkeypatch):
    monkeypatch.delenv("ANTIGRAVITY_AGENTAPI_EXE", raising=False)
    monkeypatch.setattr(Discovery, "DEFAULT_EXECUTABLE_PATHS", ["/non/existent/path/ls"])
    assert Discovery.discover_executable() is None


def test_discover_db_found(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    db_file = tmp_path / ".gemini" / "antigravity" / "conversation_summaries.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db_file.write_text("sqlite")

    found = Discovery.discover_db()
    assert found == db_file


def test_discover_db_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert Discovery.discover_db() is None


def test_discover_hub_process_mock():
    mock_res = MagicMock()
    mock_res.returncode = 0
    mock_res.stdout = SAMPLE_PS_OUTPUT_BRIDGE

    with (
        patch("subprocess.run", return_value=mock_res),
        patch.object(Discovery, "is_port_open", return_value=True),
    ):
        addr, token = Discovery.discover_hub()
        assert addr == "127.0.0.1:51236"
        assert token == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


def test_discover_hub_process_env():
    res = MagicMock(
        returncode=0,
        stdout="1234 language_server ANTIGRAVITY_LS_ADDRESS=127.0.0.1:60000 ANTIGRAVITY_CSRF_TOKEN=tok123\n",
    )
    with (
        patch("subprocess.run", return_value=res),
        patch.object(Discovery, "is_port_open", return_value=True),
    ):
        addr, token = Discovery.discover_hub()
        assert addr == "127.0.0.1:60000"
        assert token == "tok123"


def test_discover_hub_process_fallback():
    # Primary ps -wwE returns empty, secondary ps -eo pid,command matches bridge
    res1 = MagicMock(returncode=0, stdout="1234 /bin/ls\n")
    res2 = MagicMock(returncode=0, stdout=SAMPLE_PS_OUTPUT_BRIDGE)

    with (
        patch("subprocess.run", side_effect=[res1, res2]),
        patch.object(Discovery, "is_port_open", return_value=True),
    ):
        addr, token = Discovery.discover_hub()
        assert addr == "127.0.0.1:51236"
        assert token == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
