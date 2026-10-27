from unittest.mock import AsyncMock, MagicMock

import pytest

from agy_pilot.bot import (
    QueueItem,
    TelegramBridge,
    build_conversations_keyboard,
    build_project_actions_keyboard,
    build_projects_keyboard,
    split_message_chunks,
)
from agy_pilot.config import Config
from agy_pilot.controller import AntigravityProject, ConversationItem
from agy_pilot.transcript import ToolCall, TranscriptStep


def test_split_short_message():
    msg = "Hello, world!"
    chunks = split_message_chunks(msg, 100)
    assert chunks == ["Hello, world!"]


def test_split_long_multiline_message():
    line1 = "a" * 60
    line2 = "b" * 60
    msg = f"{line1}\n{line2}"
    chunks = split_message_chunks(msg, 80)
    assert len(chunks) == 2
    assert line1 in chunks[0]
    assert line2 in chunks[1]


def test_split_giant_line():
    giant = "x" * 250
    chunks = split_message_chunks(giant, 100)
    assert len(chunks) == 3
    assert len(chunks[0]) == 100
    assert len(chunks[1]) == 100
    assert len(chunks[2]) == 50


@pytest.mark.asyncio
async def test_process_item_existing_conversation(tmp_path):
    config = Config(
        adapter="telegram",
        bot_token="123456789:AAGh_dummy_token_for_unit_tests",
        allowed_user_id=123,
        downloads_dir=tmp_path / "downloads",
        db_path=tmp_path / "test.db",
    )
    mock_controller = MagicMock()
    mock_controller.get_last_step_index.return_value = 0
    mock_controller.send_message.return_value = None

    async def fake_wait(
        conversation_id,
        after_step_index,
        timeout_secs,
        progress_callback,
        step_callback,
        *args,
        **kwargs,
    ):
        # Simulate step callback with tool calls
        step = TranscriptStep(
            step_index=1,
            source="MODEL",
            step_type="PLANNER_RESPONSE",
            tool_calls=[ToolCall(name="read_file")],
        )
        step_callback(step)
        return "Model answer with overflow: " + ("x" * 4500)

    mock_controller.wait_for_model_reply = AsyncMock(side_effect=fake_wait)

    mock_store = AsyncMock()
    mock_store.get_conversation_id.return_value = "conv-123"
    mock_store.log_message.return_value = None

    bridge = TelegramBridge(config, mock_controller, mock_store)
    bridge.bot = AsyncMock()

    item = QueueItem(
        chat_id=123,
        user_id=123,
        user_display_text="ping",
        prompt_for_agent="ping",
        status_message_id=999,
    )

    await bridge._process_item(item)
    mock_controller.send_message.assert_called_with("conv-123", "ping", "outside-of-project")
    # Verify overflow was split and sent
    assert bridge.bot.send_message.called
    assert "conv-123:0" in bridge._traces
    markup = bridge.bot.send_message.call_args[1].get("reply_markup")
    assert markup is not None
    assert "View Execution Steps" in markup.inline_keyboard[0][0].text
    assert markup.inline_keyboard[0][0].callback_data == "steps:conv-123:0"


@pytest.mark.asyncio
async def test_process_item_new_conversation(tmp_path):
    config = Config(
        adapter="telegram",
        bot_token="123456789:AAGh_dummy_token_for_unit_tests",
        allowed_user_id=123,
        downloads_dir=tmp_path / "downloads",
        db_path=tmp_path / "test.db",
    )
    mock_controller = MagicMock()
    mock_controller.new_conversation.return_value = "brand-new-conv"
    mock_controller.wait_for_model_reply = AsyncMock(return_value="Answer for new conversation")

    mock_store = AsyncMock()
    mock_store.get_conversation_id.return_value = None  # Forces new_conversation path
    mock_store.set_conversation_id.return_value = None

    bridge = TelegramBridge(config, mock_controller, mock_store)
    bridge.bot = AsyncMock()

    item = QueueItem(
        chat_id=123,
        user_id=123,
        user_display_text="first prompt",
        prompt_for_agent="first prompt",
        status_message_id=None,  # No status message id, tests send_message directly
    )

    await bridge._process_item(item)
    mock_controller.new_conversation.assert_called()
    mock_store.set_conversation_id.assert_called_with("brand-new-conv")
    bridge.bot.send_message.assert_called_with(123, "Answer for new conversation")


@pytest.mark.asyncio
async def test_process_item_error_handling(tmp_path):
    config = Config(
        adapter="telegram",
        bot_token="123456789:AAGh_dummy_token_for_unit_tests",
        allowed_user_id=123,
        downloads_dir=tmp_path / "downloads",
        db_path=tmp_path / "test.db",
    )
    mock_controller = MagicMock()
    mock_controller.new_conversation.side_effect = RuntimeError("Failed to connect")

    mock_store = AsyncMock()
    mock_store.get_conversation_id.return_value = None

    bridge = TelegramBridge(config, mock_controller, mock_store)
    bridge.bot = AsyncMock()

    item = QueueItem(
        chat_id=123,
        user_id=123,
        user_display_text="error test",
        prompt_for_agent="error test",
        status_message_id=888,
    )

    await bridge._process_item(item)
    bridge.bot.edit_message_text.assert_called()
    args, kwargs = bridge.bot.edit_message_text.call_args
    assert "Task launch failed" in kwargs["text"]


def test_build_projects_keyboard():
    projects = [
        AntigravityProject(f"proj{i}", [f"file:///repo{i}"], "2026-10-23", i) for i in range(12)
    ]
    # Page 0 (items 0..4)
    text0, kb0 = build_projects_keyboard(projects, page=0, page_size=5)
    assert "Page 1/3" in text0
    assert len(kb0.inline_keyboard) == 7  # 5 item rows + 1 nav row + 1 close row
    assert kb0.inline_keyboard[0][0].callback_data == "proj_sel:proj0"

    # Page 2 (items 10..11)
    text2, kb2 = build_projects_keyboard(projects, page=2, page_size=5)
    assert "Page 3/3" in text2
    assert len(kb2.inline_keyboard) == 4  # 2 items + nav + close


def test_build_project_actions_keyboard():
    project = AntigravityProject("proj-test", ["file:///my-app"], "2026-10-23", 5)
    text, kb = build_project_actions_keyboard(project)
    assert "my-app" in text
    assert "5" in text
    assert len(kb.inline_keyboard) == 3
    assert kb.inline_keyboard[0][0].callback_data == "proj_new:proj-test"
    assert kb.inline_keyboard[1][0].callback_data == "conv_page:proj-test:0"
    assert kb.inline_keyboard[2][0].callback_data == "proj_page:0"


def test_build_conversations_keyboard():
    project = AntigravityProject("proj-test", ["file:///my-app"], "2026-10-23", 10)
    items = [
        ConversationItem(f"c{i}", "proj-test", "2026-10-23 10:00:00", f"Topic {i}")
        for i in range(5)
    ]
    text, kb = build_conversations_keyboard(project, items, page=0, total_count=10, page_size=5)
    assert "Page 1/2" in text
    assert len(kb.inline_keyboard) == 7  # 5 items + nav + back
    assert kb.inline_keyboard[0][0].callback_data == "conv_sel:c0"
    assert kb.inline_keyboard[0][0].text == "💬 Topic 0"
    assert kb.inline_keyboard[5][1].callback_data == "conv_page:proj-test:1"  # Next page
