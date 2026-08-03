"""Анти-флуд: token bucket на користувача, без звернень до БД на гарячому шляху."""

import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app import texts

log = logging.getLogger(__name__)

BURST = 8           # скільки дій можна зробити одразу
REFILL_PER_SEC = 1  # відновлення токенів
MUTE_THRESHOLD = 30  # дропів за хвилину до м'юта
MUTE_SECONDS = 3600
MAX_TRACKED = 10_000  # межа пам'яті
WARN_RESET_SEC = 60      # пауза, після якої попередження знову «тепле» й розгорнуте
EDIT_MIN_INTERVAL = 1.5  # не редагувати попередження частіше, ніж раз на стільки секунд


class _Bucket:
    __slots__ = (
        "tokens", "updated", "muted_until", "dropped", "dropped_since",
        "warned_at", "warn_msg_id", "warn_chat_id", "warn_count",
    )

    def __init__(self) -> None:
        self.tokens = float(BURST)
        self.updated = time.monotonic()
        self.muted_until = 0.0
        self.dropped = 0
        self.dropped_since = time.monotonic()
        # стан «епізоду» попереджень: одне повідомлення, яке редагується
        self.warned_at = 0.0
        self.warn_msg_id: int | None = None
        self.warn_chat_id: int | None = None
        self.warn_count = 0


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        self._buckets: dict[int, _Bucket] = {}
        # адміни проходять без обмежень; множина живе в памʼяті,
        # оновлюється на старті та при зміні ролей (/setrole, картки людей)
        self._admin_ids: set[int] = set()
        self._main_admin_id: int | None = None

    def set_admins(self, ids: Iterable[int], main_admin_id: int | None = None) -> None:
        self._admin_ids = set(ids)
        if main_admin_id is not None:
            self._main_admin_id = main_admin_id
            self._admin_ids.add(main_admin_id)

    def add_admin(self, user_id: int) -> None:
        self._admin_ids.add(user_id)

    def discard_admin(self, user_id: int) -> None:
        self._admin_ids.discard(user_id)

    def _prune(self) -> None:
        if len(self._buckets) <= MAX_TRACKED:
            return
        cutoff = sorted(b.updated for b in self._buckets.values())[len(self._buckets) // 2]
        self._buckets = {
            uid: b for uid, b in self._buckets.items() if b.updated > cutoff
        }

    async def _warn(self, event: TelegramObject, bucket: _Bucket, now: float) -> None:
        """Відповісти на зайвий тап: тепло вперше, коротко далі, без засмічення чату."""
        fresh_episode = now - bucket.warned_at > WARN_RESET_SEC
        if fresh_episode:
            bucket.warn_msg_id = None
            bucket.warn_count = 0
        bucket.warn_count += 1

        if isinstance(event, CallbackQuery):
            # тост не засмічує чат — відповідаємо на кожен тап
            bucket.warned_at = now
            await event.answer(
                texts.THROTTLED_FIRST if fresh_episode else texts.THROTTLED_AGAIN
            )
        elif isinstance(event, Message):
            if bucket.warn_msg_id is None:
                sent = await event.answer(texts.THROTTLED_FIRST)
                bucket.warn_msg_id = sent.message_id
                bucket.warn_chat_id = sent.chat.id
                bucket.warned_at = now
            elif now - bucket.warned_at > EDIT_MIN_INTERVAL:
                # редагуємо те саме повідомлення (лічильник, щоб текст щоразу мінявся)
                bucket.warned_at = now
                await event.bot.edit_message_text(
                    f"{texts.THROTTLED_AGAIN} ({bucket.warn_count})",
                    chat_id=bucket.warn_chat_id,
                    message_id=bucket.warn_msg_id,
                )

    async def _mute(self, event: TelegramObject, data: dict[str, Any], user: Any) -> None:
        log.warning("Користувач %s зам'ючений на годину за флуд", user.id)
        bot = data.get("bot") or getattr(event, "bot", None)
        if bot is None:
            return
        try:
            await bot.send_message(user.id, texts.MUTED)
        except Exception:
            pass
        if self._main_admin_id:
            try:
                await bot.send_message(
                    self._main_admin_id,
                    f"⚠️ Анти-флуд: користувач {user.id} (@{user.username}) "
                    f"зам'ючений на годину.",
                )
            except Exception:
                log.exception("Не вдалося сповістити адміна про м'ют")

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)
        if user.id in self._admin_ids:
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
            await self._mute(event, data, user)
            return None

        try:
            await self._warn(event, bucket, now)
        except Exception:
            pass
        return None
