"""Анти-флуд: token bucket на користувача, без звернень до БД на гарячому шляху."""

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app import texts

log = logging.getLogger(__name__)

BURST = 5           # скільки дій можна зробити одразу
REFILL_PER_SEC = 1  # відновлення токенів
MUTE_THRESHOLD = 30  # дропів за хвилину до м'юта
MUTE_SECONDS = 3600
MAX_TRACKED = 10_000  # межа пам'яті


class _Bucket:
    __slots__ = ("tokens", "updated", "muted_until", "warned_at", "dropped", "dropped_since")

    def __init__(self) -> None:
        self.tokens = float(BURST)
        self.updated = time.monotonic()
        self.muted_until = 0.0
        self.warned_at = 0.0
        self.dropped = 0
        self.dropped_since = time.monotonic()


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, notify_admin: Callable[[str], Awaitable[None]] | None = None) -> None:
        self._buckets: dict[int, _Bucket] = {}
        self._notify_admin = notify_admin

    def _prune(self) -> None:
        if len(self._buckets) <= MAX_TRACKED:
            return
        cutoff = sorted(b.updated for b in self._buckets.values())[len(self._buckets) // 2]
        self._buckets = {
            uid: b for uid, b in self._buckets.items() if b.updated > cutoff
        }

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        now = time.monotonic()
        bucket = self._buckets.get(user.id)
        if bucket is None:
            self._prune()
            bucket = self._buckets[user.id] = _Bucket()

        if now < bucket.muted_until:
            return None

        bucket.tokens = min(BURST, bucket.tokens + (now - bucket.updated) * REFILL_PER_SEC)
        bucket.updated = now

        if bucket.tokens >= 1:
            bucket.tokens -= 1
            return await handler(event, data)

        # перевищення ліміту
        if now - bucket.dropped_since > 60:
            bucket.dropped = 0
            bucket.dropped_since = now
        bucket.dropped += 1

        if bucket.dropped >= MUTE_THRESHOLD:
            bucket.muted_until = now + MUTE_SECONDS
            log.warning("Користувач %s зам'ючений на годину за флуд", user.id)
            if self._notify_admin:
                try:
                    await self._notify_admin(
                        f"⚠️ Анти-флуд: користувач {user.id} (@{user.username}) "
                        f"зам'ючений на годину."
                    )
                except Exception:
                    log.exception("Не вдалося сповістити адміна про м'ют")
            return None

        # одне попередження на хвилину, далі мовчазний дроп
        if now - bucket.warned_at > 60:
            bucket.warned_at = now
            try:
                if isinstance(event, Message):
                    await event.answer(texts.THROTTLED)
                elif isinstance(event, CallbackQuery):
                    await event.answer(texts.THROTTLED, show_alert=False)
            except Exception:
                pass
        return None
