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

from app.config import ADMIN_ID, DB_PATH, HEALTHCHECK_URL
from app.db import core, repo
from app.services import resources

log = logging.getLogger(__name__)

ALERT_COOLDOWN = 6 * 3600  # не повторювати той самий тип сигналу частіше

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


async def resources_task(bot: Bot) -> None:
    """Раз на 5 хв: cgroup (троттлінг CPU, памʼять, OOM) і диск.

    Сигнали йдуть лише головному адміну, кожен тип — не частіше разу на 6 год.
    """
    import time

    watch = resources.ResourceWatch()
    last_sent: dict[str, float] = {}
    if not resources.cgroup_available():
        log.info("cgroup недоступний — стежу лише за диском")
    while True:
        await asyncio.sleep(300)
        alerts: list[tuple[str, str]] = []
        try:
            if resources.cgroup_available():
                throttled = resources.read_cpu_stat().get("throttled_usec")
                if throttled is not None:
                    if text := watch.check_cpu(throttled):
                        alerts.append(("cpu", text))
                current, limit = resources.read_memory()
                if current is not None:
                    if text := watch.check_memory(current, limit):
                        alerts.append(("mem", text))
                oom = resources.read_oom_kills()
                if oom is not None:
                    if text := watch.check_oom(oom):
                        alerts.append(("oom", text))
            free, db_bytes = resources.disk_and_db(DB_PATH)
            if text := resources.ResourceWatch.check_disk(free, db_bytes):
                alerts.append(("disk", text))
        except Exception:
            log.exception("Помилка перевірки ресурсів")
            continue
        now = time.monotonic()
        for key, text in alerts:
            if now - last_sent.get(key, -ALERT_COOLDOWN) < ALERT_COOLDOWN:
                continue
            last_sent[key] = now
            log.warning("Сигнал про ресурси (%s): %s", key, text)
            try:
                await bot.send_message(ADMIN_ID, text)
            except Exception:
                log.exception("Не вдалося надіслати сигнал про ресурси")


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
