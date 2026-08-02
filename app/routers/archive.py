"""Архів анкет завершених одноразових ігор.

Строк зберігання залежить від ролі власника команди: звичайний користувач —
14 днів, керівник — 30, адміністратор бачить усе (до року, далі щоденна
задача видаляє остаточно).
"""

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app import keyboards as kb
from app import texts
from app.config import ADMIN_ID
from app.db import repo
from app.services import dates, limits

log = logging.getLogger(__name__)
router = Router(name="archive")

MAX_FORMS_SHOWN = 30


async def _scope(user_id: int) -> tuple[int | None, int]:
    """(owner_id для фільтра, скільки днів видно). Адмін бачить усе."""
    role = await repo.effective_role(user_id, ADMIN_ID)
    days = limits.retention_days(role)
    return (None if role == "admin" else user_id), days


@router.message(Command("archive"))
async def archive_list(message: Message) -> None:
    owner_filter, days = await _scope(message.from_user.id)
    games = await repo.archive_games(owner_filter, days)
    if not games:
        await message.answer(texts.ARCHIVE_EMPTY)
        return
    items = []
    for g in games:
        left, _ = dates.expires(g["archived_at"], days) or (0, "")
        owner = None
        if owner_filter is None and g["owner_id"] != message.from_user.id:
            owner_user = await repo.get_user(g["owner_id"])
            owner = (
                f"@{owner_user['username']}"
                if owner_user and owner_user["username"]
                else f"id {g['owner_id']}"
            )
        items.append(
            (g["game_id"], texts.archive_item(g["team_name"], left, g["n"], owner))
        )
    await message.answer(
        texts.archive_list(days, owner_filter is None),
        reply_markup=kb.archive_list_kb(items),
    )


@router.callback_query(kb.ArchiveCb.filter())
async def archive_show(cb: CallbackQuery, callback_data: kb.ArchiveCb) -> None:
    owner_filter, days = await _scope(cb.from_user.id)
    forms = await repo.archive_forms(callback_data.game_id, owner_filter, days)
    if not forms:
        await cb.answer("Ці дані вже видалено або вони недоступні", show_alert=True)
        return
    left, deadline = dates.expires(forms[0]["archived_at"], days) or (0, "—")
    header = texts.archive_header(forms[0]["team_name"], len(forms), left, deadline)
    if len(forms) > MAX_FORMS_SHOWN:
        header += f"\nПоказую перші {MAX_FORMS_SHOWN}."
    await cb.message.answer(header)
    for form in forms[:MAX_FORMS_SHOWN]:
        await cb.message.answer(texts.archive_form(dict(form)))
    log.info(
        "Користувач %s переглянув архів гри %s", cb.from_user.id, callback_data.game_id
    )
    await cb.answer()
