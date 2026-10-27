import pytest

from agy_pilot.config import Config


@pytest.fixture(autouse=True)
def isolate_config(tmp_path, monkeypatch):
    """Ensure tests run isolated from the real ~/.config/agy-pilot."""
    isolated_config = tmp_path / "fake_config.env"
    monkeypatch.setattr("agy_pilot.config.DEFAULT_CONFIG_FILE", isolated_config)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_IDS", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)


def test_config_defaults(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_ID", raising=False)
    monkeypatch.delenv("AGY_ADAPTER", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    cfg = Config.from_env()
    assert cfg.adapter == "telegram"
    assert cfg.bot_token == ""
    assert cfg.allowed_user_id == 0
    assert cfg.log_level == "INFO"
    assert cfg.downloads_dir.name == "downloads"
    assert cfg.db_path.name == "agy_session.db"


def test_config_env_override(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGY_ADAPTER", "telegram")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "custom:token123")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "999888777")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    cfg = Config.from_env()
    assert cfg.adapter == "telegram"
    assert cfg.bot_token == "custom:token123"
    assert cfg.allowed_user_id == 999888777
    assert cfg.log_level == "DEBUG"


def test_config_reads_dotenv_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_ID", raising=False)
    monkeypatch.delenv("AGY_ADAPTER", raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "AGY_ADAPTER=telegram\n"
        "TELEGRAM_BOT_TOKEN=dotenv_token\n"
        "TELEGRAM_ALLOWED_USER_ID=555666\n"
        "# Comment line\n"
        "INVALID_LINE\n"
    )

    cfg = Config.from_env()
    assert cfg.adapter == "telegram"
    assert cfg.bot_token == "dotenv_token"
    assert cfg.allowed_user_id == 555666
    assert cfg.allowed_user_ids == [555666]


def test_config_multiple_allowed_users(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111, 222; 333")
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_ID", raising=False)

    cfg = Config.from_env()
    assert cfg.allowed_user_ids == [111, 222, 333]
    assert cfg.allowed_user_id == 111


def test_config_global_config_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_IDS", raising=False)

    global_cfg_dir = tmp_path / ".config" / "agy-pilot"
    global_cfg_dir.mkdir(parents=True)
    global_file = global_cfg_dir / "config.env"
    global_file.write_text("TELEGRAM_BOT_TOKEN=global_tok\nTELEGRAM_ALLOWED_USER_IDS=999,888\n")

    monkeypatch.setattr("agy_pilot.config.DEFAULT_CONFIG_FILE", global_file)

    cfg = Config.from_env()
    assert cfg.bot_token == "global_tok"
    assert cfg.allowed_user_ids == [999, 888]
    assert cfg.allowed_user_id == 999
