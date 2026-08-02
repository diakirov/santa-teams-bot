"""Розсилка отримувачів після жеребкування з per-pair статусом і повтором."""

import asyncio
import logging

from aiogram import Bot

from app import texts
from app.db import repo

log = logging.getLogger(__name__)


async def deliver_pairs(bot: Bot, game_id: int, team_name: str) -> tuple[int, int, list[str]]:
    """Надсилає недоставлені пари. Повертає (доставлено_всього, всього, хто_не_отримав)."""
    pending = await repo.undelivered_pairs(game_id)
    failed: list[str] = []

    for pair in pending:
        giver_id = pair["giver_id"]
        receiver_form = await repo.get_form(game_id, pair["receiver_id"])
        if receiver_form is None:
            await repo.mark_delivery_error(game_id, giver_id, "анкета отримувача зникла")
            failed.append(await _label(game_id, giver_id))
            continue
        try:
            await bot.send_message(
                giver_id, texts.receiver_message(team_name, dict(receiver_form))
            )
            await repo.mark_delivered(game_id, giver_id)
        except Exception as e:
            log.warning("Не доставлено гравцю %s (гра %s): %s", giver_id, game_id, e)
            await repo.mark_delivery_error(game_id, giver_id, str(e))
            failed.append(await _label(game_id, giver_id))
        await asyncio.sleep(0.05)

    all_pairs = await repo.pairs_of_game(game_id)
    delivered = sum(1 for p in all_pairs if p["delivered_at"])
    return delivered, len(all_pairs), failed


async def _label(game_id: int, user_id: int) -> str:
    form = await repo.get_form(game_id, user_id)
    if form:
        return form["full_name"]
    user = await repo.get_user(user_id)
    if user and user["username"]:
        return f"@{user['username']}"
    return f"id {user_id}"
