from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agy_pilot.adapters.base import BaseIMAdapter
from agy_pilot.adapters.registry import get_adapter, register_adapter
from agy_pilot.adapters.telegram import TelegramAdapter


class DummyAdapter(BaseIMAdapter):
    def __init__(self, config, controller, store):
        super().__init__(config, controller, store)
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


def test_adapter_registry():
    register_adapter("dummy", DummyAdapter)

    cfg = MagicMock()
    ctrl = MagicMock()
    store = MagicMock()

    adapter = get_adapter("dummy", cfg, ctrl, store)
    assert isinstance(adapter, DummyAdapter)

    with pytest.raises(ValueError, match="Unsupported IM adapter 'unknown'"):
        get_adapter("unknown", cfg, ctrl, store)


def test_telegram_adapter_registered():
    cfg = MagicMock()
    cfg.bot_token = "123:test_token"
    cfg.allowed_user_id = 12345
    ctrl = MagicMock()
    store = MagicMock()

    adapter = get_adapter("telegram", cfg, ctrl, store)
    assert isinstance(adapter, TelegramAdapter)
