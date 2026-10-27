from __future__ import annotations

import asyncio
import logging
import time
from aiogram import Bot
from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramBadRequest

logger = logging.getLogger(__name__)


class StatusHeartbeat:
    def __init__(self, bot: Bot, chat_id: int, message_id: int | None, initial_status: str):
        self.bot = bot
        self.chat_id = chat_id
        self.message_id = message_id
        self._current_text = initial_status
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def start(self) -> StatusHeartbeat:
        self._task = asyncio.create_task(self._run_loop())
        return self

    def update_status(self, text: str) -> None:
        self._current_text = text

    async def _run_loop(self) -> None:
        last_typing = time.monotonic() - 10.0
        last_edit = time.monotonic() - 10.0
        last_rendered_text = ""

        try:
            while True:
                now = time.monotonic()

                # 1. Send typing action every 4 seconds
                if (now - last_typing) >= 4.0:
                    try:
                        await self.bot.send_chat_action(self.chat_id, ChatAction.TYPING)
                    except Exception as e:
                        logger.debug("Failed to send typing chat action: %s", e)
                    last_typing = now

                # 2. Edit status message if text changed and throttled (>= 1.5s)
                if self.message_id and self._current_text != last_rendered_text:
                    if (now - last_edit) >= 1.5:
                        text_to_render = self._current_text
                        try:
                            await self.bot.edit_message_text(
                                chat_id=self.chat_id,
                                message_id=self.message_id,
                                text=text_to_render,
                                parse_mode=ParseMode.MARKDOWN,
                            )
                        except TelegramBadRequest as e:
                            # If Markdown fails or text hasn't changed, fallback to plain text
                            if "message is not modified" not in str(e).lower():
                                try:
                                    await self.bot.edit_message_text(
                                        chat_id=self.chat_id,
                                        message_id=self.message_id,
                                        text=text_to_render,
                                    )
                                except Exception:
                                    pass
                        except Exception as e:
                            logger.debug("Error editing status message: %s", e)

                        last_rendered_text = text_to_render
                        last_edit = now

                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
