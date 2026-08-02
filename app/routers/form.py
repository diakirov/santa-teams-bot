"""Анкета учасника: FSM, повторне використання, /mydata, пауза на командах."""

import logging

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import keyboards as kb
from app import texts
from app.db import repo
from app.services import validators
from app.states import FormFill

log = logging.getLogger(__name__)
router = Router(name="form")

_QUESTIONS = {
    FormFill.full_name.state: texts.FORM_START,
    FormFill.phone.state: texts.FORM_ASK_PHONE,
    FormFill.address.state: texts.FORM_ASK_ADDRESS,
    FormFill.allergies.state: texts.FORM_ASK_ALLERGIES,
    FormFill.wishes.state: texts.FORM_ASK_WISHES,
    FormFill.confirm.state: "Підтверди анкету кнопками вище 🙂",
}


# ------------------------------------------------------------------ guard: команди посеред анкети

_FILL_STATES = StateFilter(
    FormFill.full_name, FormFill.phone, FormFill.address,
    FormFill.allergies, FormFill.wishes, FormFill.confirm,
)


@router.message(
    _FILL_STATES,
    F.text.startswith("/") | F.text.in_(kb.MENU_BUTTONS),
)
async def form_interrupted(message: Message, state: FSMContext) -> None:
    if (message.text or "").strip() == "/cancel":
        await state.clear()
        await message.answer(texts.CANCELLED, reply_markup=kb.main_menu())
        return
    current = await state.get_state()
    await state.update_data(paused_from=current)
    await state.set_state(FormFill.paused)
    await message.answer(texts.FORM_PAUSED, reply_markup=kb.form_paused_kb())


@router.callback_query(FormFill.paused, kb.FormCb.filter(F.act == "resume"))
async def form_resume(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    paused_from = data.get("paused_from") or FormFill.full_name.state
    await state.set_state(paused_from)
    await cb.message.answer(_QUESTIONS.get(paused_from, texts.FORM_START))
    if paused_from == FormFill.confirm.state:
        await cb.message.answer(
            texts.form_summary(data), reply_markup=kb.form_confirm_kb()
        )
    await cb.answer()


@router.callback_query(FormFill.paused, kb.FormCb.filter(F.act == "abort"))
async def form_abort(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.answer(texts.CANCELLED, reply_markup=kb.main_menu())
    await cb.answer()


# ------------------------------------------------------------------ вхідні точки

async def _start_fill(message: Message, state: FSMContext, game_id: int) -> None:
    """Починає заповнення: спершу пропонує минулу анкету, якщо є."""
    latest = await repo.latest_form(message.chat.id)
    await state.set_state(FormFill.full_name)
    await state.update_data(game_id=game_id)
    if latest is not None:
        await state.set_state(FormFill.confirm)  # рішення reuse приймається inline
        await state.update_data(reuse_offer=True)
        await message.answer(
            texts.form_reuse_offer(dict(latest)), reply_markup=kb.form_reuse_kb()
        )
    else:
        await message.answer(texts.FORM_START)


async def _open_form_entry(message: Message, state: FSMContext) -> None:
    """«📝 Моя анкета» / /mydata: показати або почати заповнення."""
    user_id = message.from_user.id
    open_games = await repo.open_games_of_user(user_id)
    if not open_games:
        drawn = await repo.drawn_player_games_of_user(user_id)
        for g in drawn:
            form = await repo.get_form(g["game_id"], user_id)
            if form:
                await message.answer(texts.mydata(dict(form)))
                await message.answer(texts.FORM_LOCKED_AFTER_DRAW)
                return
        await message.answer(texts.NO_ACTIVE_FORM_GAME)
        return
    if len(open_games) > 1:
        await message.answer(
            "У тебе кілька ігор з відкритою реєстрацією. Для якої команди анкета?",
            reply_markup=kb.form_game_pick_kb(open_games),
        )
        return
    game_id = open_games[0]["game_id"]
    form = await repo.get_form(game_id, user_id)
    if form:
        await message.answer(texts.mydata(dict(form)), reply_markup=kb.mydata_kb(game_id))
    else:
        await _start_fill(message, state, game_id)


@router.message(F.text == kb.BTN_MY_FORM)
@router.message(Command("mydata"))
async def my_form(message: Message, state: FSMContext) -> None:
    await _open_form_entry(message, state)


@router.callback_query(kb.FormCb.filter(F.act == "pickgame"))
async def pick_game(cb: CallbackQuery, callback_data: kb.FormCb, state: FSMContext) -> None:
    game = await repo.get_game(callback_data.game_id)
    if game is None or game["status"] != "registration":
        await cb.answer(texts.FORM_LOCKED_AFTER_DRAW, show_alert=True)
        return
    form = await repo.get_form(game["id"], cb.from_user.id)
    if form:
        await cb.message.answer(
            texts.mydata(dict(form)), reply_markup=kb.mydata_kb(game["id"])
        )
    else:
        await _start_fill(cb.message, state, game["id"])
    await cb.answer()


@router.callback_query(kb.FormCb.filter(F.act.in_({"fill", "edit"})))
async def fill_or_edit(cb: CallbackQuery, callback_data: kb.FormCb, state: FSMContext) -> None:
    game = await repo.get_game(callback_data.game_id)
    if game is None or game["status"] != "registration":
        await cb.answer(texts.FORM_LOCKED_AFTER_DRAW, show_alert=True)
        return
    players = await repo.game_players_list(game["id"])
    if not any(p["user_id"] == cb.from_user.id for p in players):
        await cb.answer("Ти не в цій грі 🤔", show_alert=True)
        return
    if callback_data.act == "edit":
        # свідоме редагування — минулу анкету не пропонуємо, одразу майстер
        await state.set_state(FormFill.full_name)
        await state.update_data(game_id=game["id"])
        await cb.message.answer(texts.FORM_START)
    else:
        await _start_fill(cb.message, state, game["id"])
    await cb.answer()


@router.callback_query(FormFill.confirm, kb.FormCb.filter(F.act == "reuse"))
async def form_reuse(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    latest = await repo.latest_form(cb.from_user.id)
    game = await repo.get_game(data.get("game_id", 0))
    if latest is None or game is None or game["status"] != "registration":
        await state.clear()
        await cb.answer(texts.ERROR, show_alert=True)
        return
    await repo.upsert_form(game["id"], cb.from_user.id, dict(latest))
    await state.clear()
    log.info("Анкета користувача %s скопійована в гру %s", cb.from_user.id, game["id"])
    await cb.message.edit_text(texts.FORM_SAVED)
    await cb.answer()


@router.callback_query(FormFill.confirm, kb.FormCb.filter(F.act == "refill"))
async def form_reuse_decline(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(FormFill.full_name)
    await cb.message.edit_text(texts.FORM_START)
    await cb.answer()


# ------------------------------------------------------------------ кроки

def _clean_text(message: Message, limit: int) -> tuple[str | None, str | None]:
    """(значення, текст помилки)."""
    text = (message.text or "").strip()
    if not text:
        return None, texts.FORM_TEXT_ONLY
    if len(text) > limit:
        return None, texts.form_too_long(limit)
    if validators.has_forbidden_scheme(text):
        return None, texts.FORM_BAD_LINK
    return text, None


@router.message(FormFill.full_name, F.text)
async def step_full_name(message: Message, state: FSMContext) -> None:
    value, error = _clean_text(message, validators.MAX_FULL_NAME)
    if error:
        await message.answer(error)
        return
    await state.update_data(full_name=value)
    await state.set_state(FormFill.phone)
    await message.answer(texts.FORM_ASK_PHONE)


@router.message(FormFill.phone, F.text)
async def step_phone(message: Message, state: FSMContext) -> None:
    ok, phone = validators.normalize_phone(message.text or "")
    if not ok:
        await message.answer(texts.FORM_BAD_PHONE)
        return
    await state.update_data(phone=phone)
    await state.set_state(FormFill.address)
    await message.answer(texts.FORM_ASK_ADDRESS)


@router.message(FormFill.address, F.text)
async def step_address(message: Message, state: FSMContext) -> None:
    value, error = _clean_text(message, validators.MAX_ADDRESS)
    if error:
        await message.answer(error)
        return
    await state.update_data(address=value)
    await state.set_state(FormFill.allergies)
    await message.answer(texts.FORM_ASK_ALLERGIES)


@router.message(FormFill.allergies, F.text)
async def step_allergies(message: Message, state: FSMContext) -> None:
    value, error = _clean_text(message, validators.MAX_ALLERGIES)
    if error:
        await message.answer(error)
        return
    await state.update_data(allergies=value)
    await state.set_state(FormFill.wishes)
    await message.answer(texts.FORM_ASK_WISHES)


@router.message(FormFill.wishes, F.text)
async def step_wishes(message: Message, state: FSMContext) -> None:
    value, error = _clean_text(message, validators.MAX_WISHES)
    if error:
        await message.answer(error)
        return
    await state.update_data(wishes=value)
    await state.set_state(FormFill.confirm)
    data = await state.get_data()
    await message.answer(texts.form_summary(data), reply_markup=kb.form_confirm_kb())


@router.callback_query(FormFill.confirm, kb.FormCb.filter(F.act == "save"))
async def form_save(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    required = ("game_id", "full_name", "phone", "address", "allergies", "wishes")
    if any(k not in data for k in required):
        await state.clear()
        await cb.answer(texts.ERROR, show_alert=True)
        return
    game = await repo.get_game(data["game_id"])
    if game is None or game["status"] != "registration":
        await state.clear()
        await cb.answer(texts.FORM_LOCKED_AFTER_DRAW, show_alert=True)
        return
    await repo.upsert_form(game["id"], cb.from_user.id, data)
    await state.clear()
    log.info("Анкета користувача %s збережена для гри %s", cb.from_user.id, game["id"])
    await cb.message.edit_text(texts.FORM_SAVED)
    await cb.answer()


@router.callback_query(FormFill.confirm, kb.FormCb.filter(F.act == "restart"))
async def form_restart(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_data({"game_id": data.get("game_id")})
    await state.set_state(FormFill.full_name)
    await cb.message.edit_text(texts.FORM_RESTART)
    await cb.answer()


# не-текст на будь-якому кроці анкети
@router.message(StateFilter(
    FormFill.full_name, FormFill.phone, FormFill.address,
    FormFill.allergies, FormFill.wishes,
))
async def step_not_text(message: Message) -> None:
    await message.answer(texts.FORM_TEXT_ONLY)
