from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


def get_config_dir() -> Path:
    """Return platform standard configuration directory."""
    xdg = os.getenv("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "agy-pilot"
    return Path.home() / ".config" / "agy-pilot"


def get_data_dir() -> Path:
    """Return platform standard data directory."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "agy-pilot"
    xdg = os.getenv("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "agy-pilot"
    return Path.home() / ".local" / "share" / "agy-pilot"


def get_log_dir() -> Path:
    """Return platform standard log directory."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "agy-pilot"
    xdg = os.getenv("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "agy-pilot" / "logs"


DEFAULT_CONFIG_DIR = get_config_dir()
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.env"
DEFAULT_DATA_DIR = get_data_dir()
DEFAULT_LOG_DIR = get_log_dir()


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse key-value pairs from a simple dotenv/config file."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip().strip("'\"")
    except Exception:
        pass
    return values


@dataclass
class Config:
    adapter: str
    bot_token: str
    allowed_user_ids: list[int] = field(default_factory=list)
    allowed_user_id: int = 0
    downloads_dir: Path = field(default_factory=lambda: DEFAULT_DATA_DIR / "downloads")
    db_path: Path = field(default_factory=lambda: DEFAULT_DATA_DIR / "agy_session.db")
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        if self.allowed_user_id > 0 and self.allowed_user_id not in self.allowed_user_ids:
            self.allowed_user_ids.append(self.allowed_user_id)
        elif self.allowed_user_ids and self.allowed_user_id == 0:
            self.allowed_user_id = self.allowed_user_ids[0]

    @classmethod
    def from_env(cls) -> Config:
        file_env: dict[str, str] = {}
        cwd_env = Path(".env")
        if cwd_env.is_file():
            file_env.update(_parse_env_file(cwd_env))
        elif DEFAULT_CONFIG_FILE.is_file():
            file_env.update(_parse_env_file(DEFAULT_CONFIG_FILE))

        for k, v in file_env.items():
            if k not in os.environ:
                os.environ[k] = v

        adapter = os.getenv("AGY_ADAPTER", "telegram").lower()
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

        # Parse user IDs from TELEGRAM_ALLOWED_USER_IDS
        raw_ids_str = os.getenv("TELEGRAM_ALLOWED_USER_IDS") or os.getenv("TELEGRAM_ALLOWED_USER_ID", "")
        allowed_ids: list[int] = []
        if raw_ids_str:
            for item in raw_ids_str.replace(";", ",").split(","):
                item = item.strip()
                if item.isdigit():
                    val = int(item)
                    if val not in allowed_ids:
                        allowed_ids.append(val)

        primary_user_id = allowed_ids[0] if allowed_ids else 0
        downloads_dir = DEFAULT_DATA_DIR / "downloads"
        db_path = DEFAULT_DATA_DIR / "agy_session.db"
        log_level = os.getenv("LOG_LEVEL", "INFO").upper()

        return cls(
            adapter=adapter,
            bot_token=bot_token,
            allowed_user_id=primary_user_id,
            allowed_user_ids=allowed_ids,
            downloads_dir=downloads_dir,
            db_path=db_path,
            log_level=log_level,
        )
