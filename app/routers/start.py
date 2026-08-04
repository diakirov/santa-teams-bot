"""Вхід: /start (зокрема з інвайт-кодом), /menu, /help, /cancel, введення коду."""

import logging

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import keyboards as kb
from app import texts
from app.config import ADMIN_ID
from app.db import repo
from aiogram.types import ReplyParameters

from app.routers.joining import join_team_by_code
from app.services import invites, limits, validators
from app.states import EnterCode, FeedbackText, FormFill, UserReply

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


# ------------------------------------------------------------------ фідбек на бота

@router.message(Command("feedback"))
async def feedback_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.FEEDBACK_ASK_TYPE, reply_markup=kb.feedback_type_kb())


@router.callback_query(kb.FeedbackCb.filter())
async def feedback_type(cb: CallbackQuery, callback_data: kb.FeedbackCb, state: FSMContext) -> None:
    await state.set_state(FeedbackText.text)
    await state.update_data(kind=callback_data.kind)
    await cb.message.answer(texts.FEEDBACK_ASK_TEXT)
    await cb.answer()


@router.message(FeedbackText.text, F.text)
async def feedback_create(message: Message, state: FSMContext, bot: Bot) -> None:
    text = (message.text or "").strip()
    if text.startswith("/") or text in kb.MENU_BUTTONS:
        await state.clear()
        raise SkipHandler
    if len(text) > validators.MAX_FEEDBACK:
        await message.answer(texts.form_too_long(validators.MAX_FEEDBACK))
        return
    data = await state.get_data()
    await state.clear()
    kind = data.get("kind", "bug")
    # фідбек — «скарга на бота»: reported_user_id = сам автор, команда не потрібна
    report_id = await repo.create_report(
        message.from_user.id, message.from_user.id, None, text,
        report_type=kind, author_msg_id=message.message_id,
    )
    await message.answer(texts.FEEDBACK_SENT)
    label = "🐞 Баг-репорт" if kind == "bug" else "💡 Пропозиція"
    from app.routers.admin import notify_admins
    await notify_admins(
        bot,
        f"{label} #{report_id}\n"
        f"Від: {texts.person_ref(message.from_user.id, message.from_user.username)}\n\n{text}",
        reply_markup=kb.report_actions_kb(
            report_id, kind, "open", bool(message.from_user.username)
        ),
    )


@router.callback_query(kb.UserReplyCb.filter())
async def author_reply_ask(
    cb: CallbackQuery, callback_data: kb.UserReplyCb, state: FSMContext
) -> None:
    report = await repo.get_report(callback_data.report_id)
    if report is None or report["reporter_id"] != cb.from_user.id:
        await cb.answer("Це не твоє звернення 🤔", show_alert=True)
        return
    if report["status"] not in ("open", "in_progress"):
        await cb.answer(texts.AUTHOR_REPLY_CLOSED, show_alert=True)
        return
    await state.set_state(UserReply.text)
    await state.update_data(report_id=report["id"])
    await cb.message.answer(texts.AUTHOR_REPLY_ASK)
    await cb.answer()


@router.message(UserReply.text, F.text)
async def author_reply_send(message: Message, state: FSMContext, bot: Bot) -> None:
    text = (message.text or "").strip()
    if text.startswith("/") or text in kb.MENU_BUTTONS:
        await state.clear()
        raise SkipHandler
    if len(text) > validators.MAX_FEEDBACK:
        await message.answer(texts.form_too_long(validators.MAX_FEEDBACK))
        return
    data = await state.get_data()
    await state.clear()
    report = await repo.get_report(data.get("report_id", 0))
    if report is None or report["reporter_id"] != message.from_user.id:
        await message.answer(texts.ERROR)
        return
    if report["status"] not in ("open", "in_progress"):
        await message.answer(texts.AUTHOR_REPLY_CLOSED)
        return
    admin_id = report["last_admin_id"] or report["taken_by"]
    if not admin_id:
        await message.answer(texts.ERROR)
        return
    author = message.from_user
    who = texts.person_ref(author.id, author.username)
    # цитата питання адміна — щоб було видно, на що це відповідь
    reply_params = (
        ReplyParameters(
            message_id=report["admin_msg_id"], allow_sending_without_reply=True
        )
        if report["admin_msg_id"]
        else None
    )
    try:
        await bot.send_message(
            admin_id,
            f"↩️ Автор звернення #{report['id']} ({who}) відповідає:\n\n{text}",
            reply_parameters=reply_params,
            reply_markup=kb.admin_continue_kb(report["id"], bool(author.username)),
        )
    except Exception:
        await message.answer(texts.ERROR)
        return
    await repo.set_report_author_msg(report["id"], message.message_id)
    log.info("Відповідь автора звернення #%s адміну %s", report["id"], admin_id)
    await message.answer(texts.AUTHOR_REPLY_SENT)


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
