"""Фонові задачі: heartbeat-файл, пінг healthchecks.io, щоденний бекап і ретенція.

Моніторинг навмисно незалежний від будь-чого зовнішнього на тому ж сервері:
свій чек healthchecks.io, свій heartbeat, жодних спільних компонентів.
"""

import asyncio
import logging
import os
import ssl
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
import certifi
from aiogram import Bot

from app.config import DB_PATH, HEALTHCHECK_URL
from app.db import core, repo

log = logging.getLogger(__name__)

KYIV = ZoneInfo("Europe/Kyiv")
HEARTBEAT_PATH = Path(os.path.dirname(os.path.abspath(DB_PATH))) / "heartbeat"


async def heartbeat_task() -> None:
    """Раз на хвилину торкається файлу — Docker HEALTHCHECK дивиться на його вік."""
    HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
    while True:
        HEARTBEAT_PATH.touch()
        await asyncio.sleep(60)


async def healthcheck_task(bot: Bot) -> None:
    """Кожні 5 хв: якщо Telegram відповідає — пінгуємо healthchecks.io.

    Процес мертвий або з'єднання з Telegram зависло → пінги зникають → алерт.
    """
    if not HEALTHCHECK_URL:
        log.info("HEALTHCHECK_URL не задано — пінги вимкнені")
        return
    # системні сертифікати на маку можуть бути відсутні — беремо certifi (як робить aiogram)
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)
    async with aiohttp.ClientSession(connector=connector) as session:
        while True:
            try:
                await bot.get_me()
                await session.get(HEALTHCHECK_URL, timeout=aiohttp.ClientTimeout(total=10))
            except Exception as e:
                log.warning("Healthcheck-пінг пропущено: %s", e)
            await asyncio.sleep(300)


async def daily_task() -> None:
    """О 03:00 за Києвом: бекап БД + чистка архіву анкет (ретенція 365 днів)."""
    while True:
        now = datetime.now(KYIV)
        target = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            await core.backup(DB_PATH)
            purged = await repo.purge_archive(365)
            if purged:
                log.info("Ретенція: видалено %s архівних анкет", purged)
        except Exception:
            log.exception("Помилка щоденної задачі")
