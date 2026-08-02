"""Спільна логіка вступу в команду (діп-лінк, ручний код, точкове додавання)."""

import logging

from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app import texts
from app.config import ADMIN_ID
from app.db import repo
from app.keyboards import FormCb
from app.services import limits

log = logging.getLogger(__name__)


async def _capacity(team) -> int:
    owner = await repo.get_user(team["owner_id"])
    defaults = await repo.get_limit_defaults()
    return limits.max_members(
        await repo.effective_role(team["owner_id"], ADMIN_ID),
        team["member_limit_override"],
        owner["max_members_override"] if owner else None,
        defaults,
    )


async def join_team_by_code(message: Message, code: str, state: FSMContext) -> None:
    user_id = message.from_user.id
    team = await repo.get_team_by_code(code)
    if team is None:
        await message.answer(texts.CODE_NOT_FOUND)
        return
    if team["is_archived"]:
        await message.answer(texts.TEAM_ARCHIVED)
        return

    member = await repo.get_member(team["id"], user_id)
    if member is not None and member["is_blocked"]:
        await message.answer(texts.TEAM_BLOCKED_YOU)
        return
    if member is not None:
        await message.answer(texts.ALREADY_MEMBER)
        return

    if await repo.member_count(team["id"]) >= await _capacity(team):
        await message.answer(texts.TEAM_FULL)
        return

    await repo.add_member(team["id"], user_id)
    log.info("Користувач %s приєднався до команди %s", user_id, team["id"])
    await message.answer(texts.joined_team(team["name"]))

    game = await repo.active_game(team["id"])
    if game is not None and game["status"] == "registration":
        fill_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="📝 Заповнити анкету",
                callback_data=FormCb(act="fill", game_id=game["id"]).pack(),
            )
        ]])
        await message.answer(texts.JOINED_REGISTRATION, reply_markup=fill_kb)
    elif game is not None and game["status"] == "drawn":
        await message.answer(texts.JOINED_AFTER_DRAW)
