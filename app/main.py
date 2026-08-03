"""Точка входу: збірка бота, фонові задачі, long polling."""

import asyncio
import logging
import time

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import BotCommand, ErrorEvent

from app import runtime, texts
from app.config import ADMIN_ID, BOT_TOKEN, DB_PATH, LOG_LEVEL
from app.db import core, repo
from app.db.fsm_storage import SQLiteStorage
from app.routers import routers
from app.services.monitor import (
    daily_task,
    healthcheck_task,
    heartbeat_task,
    resources_task,
)

log = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="start", description="Почати / приєднатися за посиланням"),
    BotCommand(command="menu", description="Головне меню"),
    BotCommand(command="mydata", description="Моя анкета"),
    BotCommand(command="myreceiver", description="Кому я дарую"),
    BotCommand(command="archive", description="Архів завершених одноразових ігор"),
    BotCommand(command="cancel", description="Скасувати поточну дію"),
    BotCommand(command="help", description="Допомога"),
]

_last_admin_alert: dict[str, float] = {}


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=SQLiteStorage())

    # порядок: спершу анти-флуд (без БД), потім доступ (бан, приватність)
    for observer in (dp.message, dp.callback_query):
        observer.outer_middleware(runtime.throttling_middleware)
        observer.outer_middleware(runtime.access_middleware)

    dp.include_routers(*routers)

    @dp.errors()
    async def on_error(event: ErrorEvent, bot: Bot) -> None:
        # людина заблокувала бота — це нормальний стан життя, а не аварія
        if isinstance(event.exception, TelegramForbiddenError):
            log.warning("Користувач недоступний: %s", event.exception)
            return
        # повторний тап по тій самій кнопці не змінює текст — теж не аварія
        if isinstance(event.exception, TelegramBadRequest) and (
            "message is not modified" in str(event.exception)
        ):
            return

        log.exception("Помилка обробки апдейта: %s", event.exception)
        message = None
        if event.update.message:
            message = event.update.message
        elif event.update.callback_query and event.update.callback_query.message:
            message = event.update.callback_query.message
        if message is not None:
            try:
                await message.answer(texts.ERROR)
            except Exception:
                pass
        # адміну — не частіше разу на 10 хв на тип помилки
        err_type = type(event.exception).__name__
        now = time.monotonic()
        if now - _last_admin_alert.get(err_type, 0) > 600:
            _last_admin_alert[err_type] = now
            try:
                await bot.send_message(
                    ADMIN_ID, f"🔥 Помилка в боті: {err_type}: {event.exception}"
                )
            except Exception:
                pass

    return dp


async def main() -> None:
    logging.basicConfig(
        level=LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    await core.connect(DB_PATH)
    runtime.throttling_middleware.set_admins(await repo.admin_ids(ADMIN_ID), ADMIN_ID)

    bot = Bot(token=BOT_TOKEN)  # parse_mode за замовчуванням вимкнений — дані юзерів завжди plain text
    dp = build_dispatcher()

    await bot.set_my_commands(COMMANDS)
    me = await bot.me()
    log.info("Стартую як @%s", me.username)

    background = [
        asyncio.create_task(heartbeat_task()),
        asyncio.create_task(healthcheck_task(bot)),
        asyncio.create_task(daily_task()),
        asyncio.create_task(resources_task(bot)),
    ]
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query", "my_chat_member"])
    finally:
        for task in background:
            task.cancel()
        await core.close()


if __name__ == "__main__":
    asyncio.run(main())
