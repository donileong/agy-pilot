from __future__ import annotations

import logging
from collections.abc import Collection
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseMiddleware):
    """Enforces authorization for incoming messages and callbacks against allowed user IDs."""

    def __init__(
        self,
        allowed_user_ids: int | Collection[int] | None = None,
        allowed_user_id: int | None = None,
    ):
        raw_val = allowed_user_ids if allowed_user_ids is not None else allowed_user_id
        if raw_val is None:
            self.allowed_user_ids: set[int] = set()
        elif isinstance(raw_val, int):
            self.allowed_user_ids = {raw_val} if raw_val > 0 else set()
        else:
            self.allowed_user_ids = {uid for uid in raw_val if uid > 0}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        user_id = getattr(user, "id", None)
        if not user or user_id not in self.allowed_user_ids:
            logger.warning(
                "Unauthorized access blocked: user_id=%s, username=%s",
                user_id,
                getattr(user, "username", None),
            )
            if isinstance(event, Message):
                await event.answer("⛔️ Access Restricted: This bot is private and unauthorized.")
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔️ Access Restricted: Unauthorized", show_alert=True)
            return None
        return await handler(event, data)
