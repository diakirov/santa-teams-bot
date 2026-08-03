"""Анти-флуд: обхід для адмінів, теплі попередження, озвучений мʼют."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

from aiogram.types import CallbackQuery, Message

from app import texts
from app.middlewares.throttling import BURST, MUTE_THRESHOLD, ThrottlingMiddleware


def make_message_event():
    event = MagicMock(spec=Message)  # spec — щоб проходив isinstance
    sent = MagicMock()
    sent.message_id = 111
    sent.chat.id = 42
    event.answer = AsyncMock(return_value=sent)
    event.bot = AsyncMock()
    return event


def make_callback_event():
    event = MagicMock(spec=CallbackQuery)
    event.answer = AsyncMock()
    return event


def make_user(uid, username="someone"):
    user = MagicMock()
    user.id = uid
    user.username = username
    return user


async def flood(mw, handler, event, data, times):
    for _ in range(times):
        await mw(handler, event, data)


def test_admin_passes_without_limits():
    mw = ThrottlingMiddleware()
    mw.set_admins([7], main_admin_id=99)
    handler = AsyncMock()
    event = make_message_event()
    data = {"event_from_user": make_user(7)}
    asyncio.run(flood(mw, handler, event, data, BURST * 3))
    assert handler.await_count == BURST * 3
    event.answer.assert_not_awaited()


def test_main_admin_in_set_after_set_admins():
    mw = ThrottlingMiddleware()
    mw.set_admins([], main_admin_id=99)
    handler = AsyncMock()
    event = make_message_event()
    data = {"event_from_user": make_user(99)}
    asyncio.run(flood(mw, handler, event, data, BURST * 2))
    assert handler.await_count == BURST * 2


def test_add_and_discard_admin():
    mw = ThrottlingMiddleware()
    mw.add_admin(5)
    handler = AsyncMock()
    event = make_message_event()
    data = {"event_from_user": make_user(5)}
    asyncio.run(flood(mw, handler, event, data, BURST + 5))
    assert handler.await_count == BURST + 5
    mw.discard_admin(5)
    handler.reset_mock()
    asyncio.run(flood(mw, handler, event, data, BURST + 5))
    assert handler.await_count == BURST  # решта задропана


def test_message_flood_warns_once_then_edits():
    mw = ThrottlingMiddleware()
    handler = AsyncMock()
    event = make_message_event()
    data = {"event_from_user": make_user(1)}
    asyncio.run(flood(mw, handler, event, data, BURST + 3))
    assert handler.await_count == BURST
    # одне тепле повідомлення, без спаму у відповідь
    assert event.answer.await_count == 1
    assert event.answer.await_args.args[0] == texts.THROTTLED_FIRST
    # редагування не частіше EDIT_MIN_INTERVAL: одразу — ще ні
    event.bot.edit_message_text.assert_not_awaited()
    # а коли інтервал минув — попередження редагується, не шлеться нове
    mw._buckets[1].warned_at -= 10
    asyncio.run(mw(handler, event, data))
    assert event.answer.await_count == 1
    event.bot.edit_message_text.assert_awaited_once()
    kwargs = event.bot.edit_message_text.await_args.kwargs
    assert kwargs["chat_id"] == 42 and kwargs["message_id"] == 111


def test_callback_flood_gets_toast_every_tap():
    mw = ThrottlingMiddleware()
    handler = AsyncMock()
    event = make_callback_event()
    data = {"event_from_user": make_user(2)}
    asyncio.run(flood(mw, handler, event, data, BURST + 4))
    assert event.answer.await_count == 4
    toasts = [c.args[0] for c in event.answer.await_args_list]
    assert toasts[0] == texts.THROTTLED_FIRST
    assert all(t == texts.THROTTLED_AGAIN for t in toasts[1:])


def test_mute_is_announced_to_user_and_main_admin():
    mw = ThrottlingMiddleware()
    mw.set_admins([], main_admin_id=99)
    handler = AsyncMock()
    event = make_message_event()
    bot = AsyncMock()
    data = {"event_from_user": make_user(5), "bot": bot}
    asyncio.run(flood(mw, handler, event, data, BURST + MUTE_THRESHOLD + 5))
    bucket = mw._buckets[5]
    assert bucket.muted_until > time.monotonic()
    sent_to = [c.args[0] for c in bot.send_message.await_args_list]
    assert 5 in sent_to and 99 in sent_to
    muted_text = next(c.args[1] for c in bot.send_message.await_args_list if c.args[0] == 5)
    assert muted_text == texts.MUTED
    # під час мʼюта — повна тиша, без обробки і без відповідей
    handler.reset_mock()
    event.answer.reset_mock()
    asyncio.run(mw(handler, event, data))
    handler.assert_not_awaited()
    event.answer.assert_not_awaited()
