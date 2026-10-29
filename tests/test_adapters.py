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


@pytest.mark.asyncio
async def test_telegram_adapter_dispatches_recap_above_steps_button(tmp_path):
    from unittest.mock import AsyncMock

    from agy_pilot.adapters.telegram.adapter import QueueItem
    from agy_pilot.transcript import ToolCall, TranscriptStep

    cfg = MagicMock()
    cfg.allowed_user_ids = [123]
    cfg.allowed_user_id = 123
    cfg.downloads_dir = tmp_path / "downloads"
    cfg.bot_token = "123:test_token"

    ctrl = MagicMock()
    ctrl.get_last_step_index.return_value = 0
    ctrl.get_conversation_metadata.return_value = MagicMock(workspace_uris=[f"file://{tmp_path}"])

    async def fake_wait(
        conversation_id,
        after_step_index,
        timeout_secs,
        progress_callback,
        step_callback,
        *args,
        **kwargs,
    ):
        step = TranscriptStep(
            step_index=1,
            source="MODEL",
            step_type="PLANNER_RESPONSE",
            tool_calls=[ToolCall(name="write_file", args={"Path": str(tmp_path / "result.py")})],
        )
        step_callback(step)
        return "Task completed successfully."

    ctrl.wait_for_model_reply = AsyncMock(side_effect=fake_wait)

    store = AsyncMock()
    store.get_conversation_id.return_value = "conv-xyz"

    adapter = TelegramAdapter(cfg, ctrl, store)
    adapter.bot = AsyncMock()
    dispatched_args = []

    async def fake_dispatch(chat_id, status_message_id, reply, reply_markup=None):
        dispatched_args.append((chat_id, status_message_id, reply, reply_markup))

    adapter._dispatch_reply_chunks = AsyncMock(side_effect=fake_dispatch)

    item = QueueItem(
        chat_id=123,
        user_id=123,
        user_display_text="write file",
        prompt_for_agent="write file",
        status_message_id=999,
    )

    await adapter._process_item(item)

    assert len(dispatched_args) == 1
    chat_id, status_id, reply, markup = dispatched_args[0]
    assert chat_id == 123
    assert status_id == 999
    assert "Task completed successfully." in reply
    assert "⚡️ *Run Recap* · 1 file touched · 1 step" in reply
    assert "📝 *Touched Files (1)*" in reply
    assert "`result.py`" in reply
    assert markup is not None
    assert "View Execution Steps (1 steps)" in markup.inline_keyboard[0][0].text
