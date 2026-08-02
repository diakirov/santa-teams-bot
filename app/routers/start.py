"""Вхід: /start (зокрема з інвайт-кодом), /menu, /help, /cancel, введення коду."""

import logging

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app import keyboards as kb
from app import texts
from app.config import ADMIN_ID
from app.db import repo
from app.routers.joining import join_team_by_code
from app.services import invites, limits
from app.states import EnterCode, FormFill

log = logging.getLogger(__name__)
router = Router(name="start")


@router.message(CommandStart(deep_link=True))
async def start_with_code(message: Message, command: CommandObject, state: FSMContext) -> None:
    await state.clear()
    code = (command.args or "").strip()
    if invites.looks_like_code(code):
        await join_team_by_code(message, code, state)
    else:
        await message.answer(texts.START, reply_markup=kb.main_menu())


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.START, reply_markup=kb.main_menu())


@router.message(Command("menu"))
async def menu(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is not None and current.startswith(FormFill.__name__):
        return  # анкету обробляє form-роутер
    await state.clear()
    await message.answer(texts.MENU, reply_markup=kb.main_menu())


@router.message(Command("help"))
@router.message(F.text == kb.BTN_HELP)
async def help_cmd(message: Message) -> None:
    """Довідка залежить від ролі: числа й адмінські команди підставляються."""
    role = await repo.effective_role(message.from_user.id, ADMIN_ID)
    user_limits = None
    if role == "kerivnyk":
        user = await repo.get_user(message.from_user.id)
        defaults = await repo.get_limit_defaults()
        user_limits = (
            limits.max_teams(role, user["max_teams_override"], defaults),
            limits.max_members(role, None, user["max_members_override"], defaults),
        )
    await message.answer(
        texts.help_text(role, limits.retention_days(role), user_limits)
    )


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer(texts.NOTHING_TO_CANCEL)
    else:
        await state.clear()
        await message.answer(texts.CANCELLED, reply_markup=kb.main_menu())


@router.message(F.text == kb.BTN_ENTER_CODE)
async def ask_code(message: Message, state: FSMContext) -> None:
    await state.set_state(EnterCode.code)
    await message.answer(texts.ASK_CODE)


@router.message(EnterCode.code, F.text)
async def enter_code(message: Message, state: FSMContext) -> None:
    code = (message.text or "").strip()
    # команда чи кнопка меню — скидаємо очікування коду і пропускаємо далі,
    # щоб спрацював звичайний хендлер (меню не має «застрягати»)
    if code.startswith("/") or code in kb.MENU_BUTTONS:
        await state.clear()
        raise SkipHandler
    if not invites.looks_like_code(code):
        await message.answer(texts.CODE_NOT_FOUND)
        return
    await state.clear()
    await join_team_by_code(message, code, state)
