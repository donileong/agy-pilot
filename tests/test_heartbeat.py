import asyncio
from unittest.mock import AsyncMock
import pytest
from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramBadRequest
from agy_pilot.heartbeat import StatusHeartbeat

@pytest.mark.asyncio
async def test_heartbeat_loop_and_throttling():
    mock_bot = AsyncMock()
    mock_bot.send_chat_action.return_value = True
    mock_bot.edit_message_text.return_value = True

    hb = StatusHeartbeat(
        bot=mock_bot,
        chat_id=12345,
        message_id=67890,
        initial_status="Initial Status",
    )
    hb.start()

    # Let the loop tick
    await asyncio.sleep(0.6)
    mock_bot.send_chat_action.assert_called_with(12345, ChatAction.TYPING)

    # Update status to trigger edit_message_text
    hb.update_status("Updated Status")
    await asyncio.sleep(1.6)
    mock_bot.edit_message_text.assert_called()

    await hb.stop()
    assert hb._task is None

@pytest.mark.asyncio
async def test_heartbeat_markdown_fallback():
    mock_bot = AsyncMock()
    # First call with MARKDOWN raises TelegramBadRequest, fallback call succeeds
    mock_bot.edit_message_text.side_effect = [
        TelegramBadRequest(method="editMessageText", message="Can't parse entities"),
        True,
    ]

    hb = StatusHeartbeat(
        bot=mock_bot,
        chat_id=12345,
        message_id=67890,
        initial_status="Initial",
    )
    hb.start()
    hb.update_status("Invalid *Markdown text")
    await asyncio.sleep(1.6)

    # Should have attempted with MARKDOWN, then fallen back without parse_mode
    assert mock_bot.edit_message_text.call_count >= 2
    await hb.stop()
