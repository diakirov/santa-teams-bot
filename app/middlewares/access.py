"""Доступ: лише приватні чати, реєстрація користувача, глобальний бан."""

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app import texts
from app.db import repo

log = logging.getLogger(__name__)

UPSERT_INTERVAL = 60  # не частіше разу на хвилину пишемо last_seen


class AccessMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        self._last_upsert: dict[int, float] = {}
        self._ban_cache: dict[int, tuple[bool, float]] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None or user.is_bot:
            return None

        # лише приватні чати
        chat = None
        if isinstance(event, Message):
            chat = event.chat
        elif isinstance(event, CallbackQuery) and event.message:
            chat = event.message.chat
        if chat is not None and chat.type != "private":
            bot = data.get("bot")
            if bot is not None and isinstance(event, Message):
                try:
                    await bot.leave_chat(chat.id)
                except Exception:
                    pass
            return None

        now = time.monotonic()

        if now - self._last_upsert.get(user.id, 0) > UPSERT_INTERVAL:
            await repo.upsert_user(user.id, user.username)
            self._last_upsert[user.id] = now
            if len(self._last_upsert) > 20_000:
                self._last_upsert.clear()

        banned, checked_at = self._ban_cache.get(user.id, (False, 0.0))
        if now - checked_at > UPSERT_INTERVAL:
            row = await repo.get_user(user.id)
            banned = bool(row and row["is_banned"])
            self._ban_cache[user.id] = (banned, now)
            if len(self._ban_cache) > 20_000:
                self._ban_cache.clear()

        if banned:
            if isinstance(event, Message):
                try:
                    await event.answer(texts.BANNED)
                except Exception:
                    pass
            elif isinstance(event, CallbackQuery):
                try:
                    await event.answer(texts.BANNED, show_alert=True)
                except Exception:
                    pass
            return None

        return await handler(event, data)

    def invalidate_ban_cache(self, user_id: int) -> None:
        """Викликається після бана/розбана, щоб рішення діяло одразу."""
        self._ban_cache.pop(user_id, None)
