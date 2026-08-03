"""Адмінка: статистика, скарги, ролі, ліміти, бан, вимикач реєстрації, /health."""

import logging
import os

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import keyboards as kb
from app import texts
from app.config import ADMIN_ID, DB_PATH
from app.db import repo
from app import runtime
from app.services import resources, validators
from app.states import BanReason

log = logging.getLogger(__name__)
router = Router(name="admin")


class AdminFilter(BaseFilter):
    """Головний адмін (з .env) або користувач із роллю admin у БД."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return await repo.is_admin(event.from_user.id, ADMIN_ID)


admin_only = AdminFilter()


async def notify_admins(
    bot: Bot, text: str, reply_markup=None, exclude_id: int | None = None
) -> None:
    """Розсилка службового повідомлення всім адмінам (крім exclude_id)."""
    for admin_id in await repo.admin_ids(ADMIN_ID):
        if admin_id == exclude_id:
            continue
        try:
            await bot.send_message(admin_id, text, reply_markup=reply_markup)
        except Exception:
            log.warning("Не вдалося сповістити адміна %s", admin_id)


# ------------------------------------------------------------------ заявка на роль (доступна всім)

@router.message(Command("role"))
async def role_request(message: Message, bot: Bot) -> None:
    role = await repo.effective_role(message.from_user.id, ADMIN_ID)
    if role in ("kerivnyk", "admin"):
        await message.answer("У тебе вже є ця роль 🙂")
        return
    if not await repo.create_role_request(message.from_user.id):
        await message.answer(texts.ROLE_REQUEST_DUP)
        return
    await message.answer(texts.ROLE_REQUEST_SENT)
    requests = await repo.pending_role_requests()
    request_id = next(
        (r["id"] for r in requests if r["user_id"] == message.from_user.id), None
    )
    if request_id:
        await notify_admins(
            bot,
            f"👑 Заявка на роль керівника від "
            f"@{message.from_user.username or message.from_user.id} "
            f"(id {message.from_user.id})",
            reply_markup=kb.role_request_kb(request_id),
        )


# ------------------------------------------------------------------ панель

def _panel_text(registration_open: bool) -> str:
    text = "Панель адміністратора 🛠"
    if not registration_open:
        text += "\n\n🔴 Реєстрацію закрито — нові команди зараз не створюються."
    return text


@router.message(Command("admin"), admin_only)
async def admin_panel(message: Message) -> None:
    is_open = await repo.registration_open()
    await message.answer(
        _panel_text(is_open),
        reply_markup=kb.admin_menu_kb(is_open, message.from_user.id == ADMIN_ID),
    )


async def _is_admin_cb(cb: CallbackQuery) -> bool:
    return await repo.is_admin(cb.from_user.id, ADMIN_ID)


@router.callback_query(kb.AdminCb.filter(F.act == "stats"))
async def admin_stats(cb: CallbackQuery) -> None:
    if not await _is_admin_cb(cb):
        await cb.answer("Ця дія недоступна", show_alert=True)
        return
    s = await repo.stats()
    await cb.message.answer(
        "📊 Статистика\n\n"
        f"Користувачів: {s['users']} (забанених: {s['banned']})\n"
        f"Команд: {s['teams']}\n"
        f"Активних ігор: {s['active_games']}, завершених: {s['finished_games']}\n"
        f"Анкет: {s['forms']}\n"
        f"Недоставлених пар: {s['undelivered']}\n"
        f"Відкритих скарг: {s['open_reports']}, заявок на ролі: {s['pending_roles']}"
    )
    await cb.answer()


async def _list_reports(cb: CallbackQuery, kind: str | None) -> None:
    reports = await repo.open_reports(kind)
    if not reports:
        await cb.answer("Тут порожньо 👌", show_alert=True)
        return
    for r in reports[:10]:
        if r["type"] == "user":
            username = f"@{r['reported_username']}" if r["reported_username"] else ""
            await cb.message.answer(
                f"Скарга #{r['id']} від {r['created_at']}\n"
                f"На: {username} (id {r['reported_user_id']})\n"
                f"Причина: {r['reason']}",
                reply_markup=kb.report_kb(r["id"]),
            )
        else:
            label = "🐞 Баг-репорт" if r["type"] == "bug" else "💡 Пропозиція"
            await cb.message.answer(
                f"{label} #{r['id']} від {r['created_at']}\n"
                f"Від: id {r['reporter_id']}\n\n{r['reason']}",
                reply_markup=kb.feedback_done_kb(r["id"]),
            )
    if len(reports) > 10:
        await cb.message.answer(f"…і ще {len(reports) - 10} у черзі.")
    await cb.message.answer("Показати:", reply_markup=kb.reports_filter_kb())
    await cb.answer()


@router.callback_query(kb.AdminCb.filter(F.act.in_({"reports", "rep_f_user", "rep_f_fb"})))
async def admin_reports(cb: CallbackQuery, callback_data: kb.AdminCb) -> None:
    if not await _is_admin_cb(cb):
        await cb.answer("Ця дія недоступна", show_alert=True)
        return
    kind = {"reports": None, "rep_f_user": "user", "rep_f_fb": "feedback"}[callback_data.act]
    await _list_reports(cb, kind)


@router.callback_query(kb.AdminCb.filter(F.act.in_({"rep_ban", "rep_dismiss"})))
async def admin_report_decide(cb: CallbackQuery, callback_data: kb.AdminCb, bot: Bot) -> None:
    if not await _is_admin_cb(cb):
        await cb.answer("Ця дія недоступна", show_alert=True)
        return
    report = await repo.get_report(callback_data.arg)
    if report is None:
        await cb.answer("Скаргу не знайдено", show_alert=True)
        return
    ban = callback_data.act == "rep_ban"
    if ban and report["type"] != "user":
        await cb.answer("Це фідбек на бота — тут нема кого банити 🙂", show_alert=True)
        return
    if ban and await repo.is_admin(report["reported_user_id"], ADMIN_ID) and cb.from_user.id != ADMIN_ID:
        await cb.answer("Адміністратора може банити лише головний адмін", show_alert=True)
        return
    # атомарний «клейм»: якщо інший адмін встиг першим — просто повідомляємо
    if not await repo.resolve_report(report["id"], "banned" if ban else "dismissed"):
        await cb.answer("Цю скаргу вже розглянув інший адмін", show_alert=True)
        return
    if ban:
        await repo.set_ban(
            report["reported_user_id"], True, f"скарга #{report['id']}",
            banned_by=cb.from_user.id,
        )
        runtime.access_middleware.invalidate_ban_cache(report["reported_user_id"])
        log.info("Користувач %s забанений за скаргою #%s адміном %s",
                 report["reported_user_id"], report["id"], cb.from_user.id)
        outcome = "користувача забанено 🚫"
    else:
        outcome = "опрацьовано ✔️" if report["type"] != "user" else "відхилено ✖️"
    what = "Скаргу" if report["type"] == "user" else "Фідбек"
    await cb.message.edit_text(f"{what} #{report['id']}: {outcome}")
    await notify_admins(
        bot,
        f"ℹ️ {what} #{report['id']} розглянув @{cb.from_user.username or cb.from_user.id}: {outcome}",
        exclude_id=cb.from_user.id,
    )
    thanks = (
        f"Твою скаргу #{report['id']} розглянуто. Дякую, що допомагаєш тримати гру чесною 🙌"
        if report["type"] == "user"
        else f"Твій фідбек #{report['id']} опрацьовано. Дякую, що допомагаєш робити бота кращим 🙌"
    )
    try:
        await bot.send_message(report["reporter_id"], thanks)
    except Exception:
        pass
    await cb.answer()


@router.callback_query(kb.AdminCb.filter(F.act == "roles"))
async def admin_roles(cb: CallbackQuery) -> None:
    if not await _is_admin_cb(cb):
        await cb.answer("Ця дія недоступна", show_alert=True)
        return
    requests = await repo.pending_role_requests()
    if not requests:
        await cb.answer("Заявок немає 👌", show_alert=True)
        return
    for r in requests[:10]:
        username = f"@{r['username']}" if r["username"] else ""
        await cb.message.answer(
            f"Заявка #{r['id']} на роль керівника\nВід: {username} (id {r['user_id']})",
            reply_markup=kb.role_request_kb(r["id"]),
        )
    await cb.answer()


@router.callback_query(kb.AdminCb.filter(F.act.in_({"role_yes", "role_no"})))
async def admin_role_decide(cb: CallbackQuery, callback_data: kb.AdminCb, bot: Bot) -> None:
    if not await _is_admin_cb(cb):
        await cb.answer("Ця дія недоступна", show_alert=True)
        return
    approved = callback_data.act == "role_yes"
    req = await repo.decide_role_request(callback_data.arg, approved)
    if req is None:
        await cb.answer("Цю заявку вже розглянув інший адмін", show_alert=True)
        return
    outcome = "схвалено ✅" if approved else "відхилено ✖️"
    await cb.message.edit_text(f"Заявка #{req['id']}: {outcome}")
    await notify_admins(
        bot,
        f"ℹ️ Заявку #{req['id']} на роль керівника (id {req['user_id']}) розглянув "
        f"@{cb.from_user.username or cb.from_user.id}: {outcome}",
        exclude_id=cb.from_user.id,
    )
    try:
        await bot.send_message(
            req["user_id"], texts.ROLE_APPROVED if approved else texts.ROLE_DECLINED
        )
    except Exception:
        pass
    await cb.answer()


@router.callback_query(kb.AdminCb.filter(F.act == "limits"))
async def admin_limits(cb: CallbackQuery) -> None:
    if not await _is_admin_cb(cb):
        await cb.answer("Ця дія недоступна", show_alert=True)
        return
    defaults = await repo.get_limit_defaults()
    lines = [f"{k} = {v}" for k, v in sorted(defaults.items())]
    await cb.message.answer(
        "⚙️ Глобальні ліміти (діють на всіх без персональних винятків):\n\n"
        + "\n".join(lines)
        + "\n\nЗмінити глобальний: /set_default limit.user.max_teams 7\n"
        "Персональний виняток: /set_limit @username 10 200\n"
        "(перше число — команди, друге — учасники; «-» = скинути виняток)\n"
        "Разовий на команду: /set_team_limit <team_id> <число|->\n"
        "Бан: /ban @username <причина від 10 символів> · Розбан: /unban @username\n"
        "Видалити дані людини на її запит: /forget @username\n"
        "Ролі: /setrole @username user|kerivnyk|admin (admin — лише головний)"
    )
    await cb.answer()


@router.callback_query(kb.AdminCb.filter(F.act == "toggle_reg"))
async def admin_toggle_registration(cb: CallbackQuery) -> None:
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Вимикач реєстрації доступний лише головному адміну", show_alert=True)
        return
    is_open = await repo.registration_open()
    question = (
        "Закрити створення нових команд? Люди не зможуть починати нові ігри, "
        "поки реєстрацію не відкриють знову."
        if is_open
        else "Відкрити створення нових команд для всіх?"
    )
    await cb.message.answer(
        question,
        reply_markup=kb.confirm_kb(
            kb.AdminCb(act="reg_yes"), kb.AdminCb(act="reg_no"),
            yes_text="⏸ Так, закрити" if is_open else "▶️ Так, відкрити",
        ),
    )
    await cb.answer()


@router.callback_query(kb.AdminCb.filter(F.act.in_({"reg_yes", "reg_no"})))
async def admin_toggle_registration_decide(cb: CallbackQuery, callback_data: kb.AdminCb) -> None:
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Вимикач реєстрації доступний лише головному адміну", show_alert=True)
        return
    if callback_data.act == "reg_no":
        await cb.message.edit_text("Окей, нічого не міняю ✖️")
        await cb.answer()
        return
    is_open = await repo.registration_open()
    await repo.set_setting("registration_open", "0" if is_open else "1")
    state = "закрито ⏸" if is_open else "відкрито ▶️"
    log.info("Створення нових команд: %s", state)
    await cb.message.edit_text(
        f"Створення нових команд: {state}\n\n{_panel_text(not is_open)}",
        reply_markup=kb.admin_menu_kb(not is_open, True),
    )
    await cb.answer()


# ------------------------------------------------------------------ люди з ролями

_ROLE_LABELS = {"admin": "адміністратор 🛠", "kerivnyk": "керівник 👑", "user": "користувач"}


def _person_card_text(user) -> str:
    lines = [
        f"Людина: id {user['id']}" + (f" (@{user['username']})" if user["username"] else ""),
        f"Роль: {_ROLE_LABELS.get(user['role'], user['role'])}",
        f"Вперше в боті: {user['first_seen_at']} (UTC)",
        f"Востаннє: {user['last_seen_at']} (UTC)",
    ]
    if user["is_banned"]:
        by = "головний адмін" if user["banned_by"] == ADMIN_ID else f"адмін id {user['banned_by']}"
        lines.append(
            f"\nБан 🚫 від: {by}, {user['banned_at'] or 'дата невідома'}\n"
            f"Причина: {user['ban_reason'] or 'не вказана'}"
        )
    return "\n".join(lines)


@router.callback_query(kb.AdminCb.filter(F.act == "people"))
async def admin_people(cb: CallbackQuery) -> None:
    if not await _is_admin_cb(cb):
        await cb.answer("Ця дія недоступна", show_alert=True)
        return
    admins = await repo.users_by_role("admin")
    kerivnyky = await repo.users_by_role("kerivnyk")
    if not admins and not kerivnyky:
        await cb.answer("Поки що ролей ні в кого немає 🙂", show_alert=True)
        return
    text = (
        f"👥 Люди з ролями: адміністраторів {len(admins)}, керівників {len(kerivnyky)}.\n"
        "Тап по людині — картка з діями.\n"
        "(Головний адмін живе в конфігурації і в списку не показується.)"
    )
    markup = kb.people_list_kb(admins, kerivnyky)
    try:
        await cb.message.edit_text(text, reply_markup=markup)
    except Exception:
        await cb.message.answer(text, reply_markup=markup)
    await cb.answer()


async def _render_person_card(cb: CallbackQuery, user_id: int) -> None:
    user = await repo.get_user(user_id)
    if user is None:
        await cb.answer("Користувача не знайдено", show_alert=True)
        return
    markup = kb.person_card_kb(user, cb.from_user.id == ADMIN_ID)
    try:
        await cb.message.edit_text(_person_card_text(user), reply_markup=markup)
    except Exception:
        await cb.message.answer(_person_card_text(user), reply_markup=markup)


@router.callback_query(kb.AdminCb.filter(F.act == "person"))
async def admin_person(cb: CallbackQuery, callback_data: kb.AdminCb) -> None:
    if not await _is_admin_cb(cb):
        await cb.answer("Ця дія недоступна", show_alert=True)
        return
    await _render_person_card(cb, callback_data.arg)
    await cb.answer()


_CARD_ROLE_ACTIONS = {
    "mk_kerivnyk": "kerivnyk",
    "rm_kerivnyk": "user",
    "mk_admin": "admin",
    "rm_admin": "user",
}


@router.callback_query(kb.AdminCb.filter(F.act.in_(set(_CARD_ROLE_ACTIONS))))
async def admin_person_role(cb: CallbackQuery, callback_data: kb.AdminCb, bot: Bot) -> None:
    if not await _is_admin_cb(cb):
        await cb.answer("Ця дія недоступна", show_alert=True)
        return
    user = await repo.get_user(callback_data.arg)
    if user is None:
        await cb.answer("Користувача не знайдено", show_alert=True)
        return
    result = await _apply_role(cb.from_user.id, user, _CARD_ROLE_ACTIONS[callback_data.act], bot)
    await cb.answer(result, show_alert=True)
    await _render_person_card(cb, user["id"])


@router.callback_query(kb.AdminCb.filter(F.act == "unban_ask"))
async def admin_unban_ask(cb: CallbackQuery, callback_data: kb.AdminCb) -> None:
    if not await _is_admin_cb(cb):
        await cb.answer("Ця дія недоступна", show_alert=True)
        return
    user = await repo.get_user(callback_data.arg)
    if user is None or not user["is_banned"]:
        await cb.answer("Користувач і так не забанений 🙂", show_alert=True)
        return
    refusal = _unban_refusal(cb.from_user.id, user)
    if refusal:
        await cb.answer(refusal, show_alert=True)
        return
    await _unban_preview(user, cb.message.answer)
    await cb.answer()


@router.callback_query(kb.AdminCb.filter(F.act == "ban_ask"))
async def admin_ban_ask(cb: CallbackQuery, callback_data: kb.AdminCb, state: FSMContext) -> None:
    if not await _is_admin_cb(cb):
        await cb.answer("Ця дія недоступна", show_alert=True)
        return
    user = await repo.get_user(callback_data.arg)
    if user is None:
        await cb.answer("Користувача не знайдено", show_alert=True)
        return
    refusal = await _ban_checks(cb.from_user.id, user)
    if refusal:
        await cb.answer(refusal, show_alert=True)
        return
    await state.set_state(BanReason.reason)
    await state.update_data(target_id=user["id"])
    await cb.message.answer(texts.BAN_ASK_REASON)
    await cb.answer()


@router.message(BanReason.reason, F.text)
async def ban_reason_input(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw.startswith("/") or raw in kb.MENU_BUTTONS:
        await state.clear()
        raise SkipHandler
    reason = validators.ban_reason(raw)
    if reason is None:
        await message.answer(texts.BAN_REASON_INVALID)
        return
    data = await state.get_data()
    await state.clear()
    user = await repo.get_user(data["target_id"])
    if user is None:
        await message.answer("Користувача не знайдено.")
        return
    refusal = await _ban_checks(message.from_user.id, user)
    if refusal:
        await message.answer(refusal)
        return
    await _apply_ban(message.from_user.id, user, reason, message.answer)


@router.callback_query(kb.AdminCb.filter(F.act == "forget_ask"))
async def admin_forget_ask(cb: CallbackQuery, callback_data: kb.AdminCb) -> None:
    if not await _is_admin_cb(cb):
        await cb.answer("Ця дія недоступна", show_alert=True)
        return
    user = await repo.get_user(callback_data.arg)
    if user is None:
        await cb.answer("Користувача не знайдено", show_alert=True)
        return
    await cb.message.answer(
        f"Точно видалити всі анкети й архівні копії людини id {user['id']}"
        + (f" (@{user['username']})" if user["username"] else "") + "?\n"
        "Це незворотно — як команда /forget, з квитанцією всім адмінам.",
        reply_markup=kb.confirm_kb(
            kb.AdminCb(act="forget_yes", arg=user["id"]),
            kb.AdminCb(act="forget_no", arg=user["id"]),
            yes_text="🗑 Так, видалити",
        ),
    )
    await cb.answer()


@router.callback_query(kb.AdminCb.filter(F.act.in_({"forget_yes", "forget_no"})))
async def admin_forget_decide(cb: CallbackQuery, callback_data: kb.AdminCb, bot: Bot) -> None:
    if not await _is_admin_cb(cb):
        await cb.answer("Ця дія недоступна", show_alert=True)
        return
    if callback_data.act == "forget_no":
        await cb.message.edit_text("Окей, нічого не видаляю ✖️")
        await cb.answer()
        return
    user = await repo.get_user(callback_data.arg)
    if user is None:
        await cb.answer("Користувача не знайдено", show_alert=True)
        return
    receipt = await _do_forget(user, cb.from_user.id)
    await cb.message.edit_text(receipt)
    await notify_admins(bot, receipt, exclude_id=cb.from_user.id)
    await cb.answer()


# ------------------------------------------------------------------ текстові адмін-команди

async def _resolve_user(arg: str):
    arg = arg.strip()
    if arg.isdigit():
        return await repo.get_user(int(arg))
    return await repo.find_user_by_username(arg)


# --- бан: причина обовʼязкова і змістовна ---

async def _ban_checks(actor_id: int, user) -> str | None:
    """Перевірки перед баном. Повертає текст відмови або None, якщо можна."""
    if user["id"] == ADMIN_ID:
        return "Головного адміна забанити не можна 🙂"
    if await repo.is_admin(user["id"], ADMIN_ID) and actor_id != ADMIN_ID:
        return "Іншого адміністратора може банити лише головний адмін."
    return None


async def _apply_ban(actor_id: int, user, reason: str, answer) -> None:
    await repo.set_ban(user["id"], True, reason, banned_by=actor_id)
    runtime.access_middleware.invalidate_ban_cache(user["id"])
    log.info("Бан користувача %s від %s: %s", user["id"], actor_id, reason)
    await answer(f"Користувача id {user['id']} забанено 🚫")


@router.message(Command("ban"), admin_only)
async def cmd_ban(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split(maxsplit=1)
    if not parts:
        await message.answer(texts.BAN_FORMAT)
        return
    user = await _resolve_user(parts[0])
    if user is None:
        await message.answer("Не знайшов такого користувача.")
        return
    refusal = await _ban_checks(message.from_user.id, user)
    if refusal:
        await message.answer(refusal)
        return
    reason = validators.ban_reason(parts[1] if len(parts) > 1 else "")
    if reason is None:
        await message.answer(texts.BAN_REASON_INVALID)
        return
    await _apply_ban(message.from_user.id, user, reason, message.answer)


# --- розбан: спершу показати, хто/коли/чому банив, потім підтвердження ---

def _unban_refusal(actor_id: int, user) -> str | None:
    if user["is_banned"] and user["banned_by"] == ADMIN_ID and actor_id != ADMIN_ID:
        return "Цього користувача забанив головний адмін — зняти бан може лише він."
    return None


async def _unban_preview(user, answer) -> None:
    banned_by = user["banned_by"]
    by = "головний адмін" if banned_by == ADMIN_ID else f"адмін id {banned_by}"
    await answer(
        f"Користувач id {user['id']}"
        + (f" (@{user['username']})" if user["username"] else "") + "\n"
        f"Забанений: {by}, {user['banned_at'] or 'дата невідома'} (UTC)\n"
        f"Причина: {user['ban_reason'] or 'не вказана'}\n\n"
        "Зняти бан?",
        reply_markup=kb.confirm_kb(
            kb.AdminCb(act="unban_yes", arg=user["id"]),
            kb.AdminCb(act="unban_no", arg=user["id"]),
            yes_text="✅ Розбанити",
        ),
    )


@router.message(Command("unban"), admin_only)
async def cmd_unban(message: Message, command: CommandObject) -> None:
    user = await _resolve_user(command.args or "")
    if user is None:
        await message.answer("Не знайшов такого користувача.")
        return
    if not user["is_banned"]:
        await message.answer(f"Користувач id {user['id']} і так не забанений 🙂")
        return
    refusal = _unban_refusal(message.from_user.id, user)
    if refusal:
        await message.answer(refusal)
        return
    await _unban_preview(user, message.answer)


@router.callback_query(kb.AdminCb.filter(F.act.in_({"unban_yes", "unban_no"})))
async def admin_unban_decide(cb: CallbackQuery, callback_data: kb.AdminCb) -> None:
    if not await _is_admin_cb(cb):
        await cb.answer("Ця дія недоступна", show_alert=True)
        return
    user = await repo.get_user(callback_data.arg)
    if user is None:
        await cb.answer("Користувача не знайдено", show_alert=True)
        return
    if callback_data.act == "unban_no":
        await cb.message.edit_text("Окей, бан лишається ✖️")
        await cb.answer()
        return
    if not user["is_banned"]:
        await cb.message.edit_text(f"Користувач id {user['id']} вже не забанений 🙂")
        await cb.answer()
        return
    refusal = _unban_refusal(cb.from_user.id, user)
    if refusal:
        await cb.answer(refusal, show_alert=True)
        return
    await repo.set_ban(user["id"], False, None)
    runtime.access_middleware.invalidate_ban_cache(user["id"])
    log.info("Розбан користувача %s від %s", user["id"], cb.from_user.id)
    await cb.message.edit_text(f"Користувача id {user['id']} розбанено ✅")
    await cb.answer()


# --- одна команда керування ролями ---

async def _apply_role(actor_id: int, user, new_role: str, bot: Bot) -> str:
    """Змінити роль з усіма перевірками. Повертає текст відповіді для адміна."""
    if user["id"] == ADMIN_ID:
        return "Роль головного адміна змінити не можна 🙂"
    if (new_role == "admin" or user["role"] == "admin") and actor_id != ADMIN_ID:
        return "Призначати і знімати адміністраторів може лише головний адмін."
    if user["role"] == new_role:
        return f"У людини id {user['id']} вже роль {new_role} 🙂"
    await repo.set_role(user["id"], new_role)
    if new_role == "admin":
        runtime.throttling_middleware.add_admin(user["id"])
    else:
        runtime.throttling_middleware.discard_admin(user["id"])
    log.info("Роль користувача %s: %s → %s (від %s)", user["id"], user["role"], new_role, actor_id)
    notice = {
        "admin": "Тобі надано роль адміністратора 🛠 Панель: /admin, довідка — /help.",
        "kerivnyk": texts.ROLE_APPROVED,
        "user": "Твою роль змінено на звичайного користувача. Якщо потрібна роль "
                "керівника — подай заявку через /role.",
    }[new_role]
    try:
        await bot.send_message(user["id"], notice)
    except Exception:
        pass
    return f"Готово ✅ id {user['id']}: роль тепер {new_role}."


@router.message(Command("setrole"), admin_only)
async def cmd_setrole(message: Message, command: CommandObject, bot: Bot) -> None:
    parts = (command.args or "").split()
    if len(parts) != 2 or parts[1] not in ("user", "kerivnyk", "admin"):
        await message.answer(
            "Формат: /setrole @username user|kerivnyk|admin\n"
            "(admin — призначає і знімає лише головний адмін)"
        )
        return
    user = await _resolve_user(parts[0])
    if user is None:
        await message.answer("Не знайшов такого користувача (він має хоч раз запустити бота).")
        return
    await message.answer(await _apply_role(message.from_user.id, user, parts[1], bot))


@router.message(Command("set_default"), admin_only)
async def cmd_set_default(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    valid_keys = {
        "limit.user.max_teams", "limit.user.max_members",
        "limit.kerivnyk.max_teams", "limit.kerivnyk.max_members",
    }
    if len(parts) != 2 or parts[0] not in valid_keys or not parts[1].isdigit():
        await message.answer(
            "Формат: /set_default <ключ> <число>\nКлючі:\n" + "\n".join(sorted(valid_keys))
        )
        return
    await repo.set_setting(parts[0], parts[1])
    log.info("Глобальний ліміт %s = %s", parts[0], parts[1])
    await message.answer(f"Готово: {parts[0]} = {parts[1]} ✅\nДіє одразу на всі нові дії.")


@router.message(Command("set_limit"), admin_only)
async def cmd_set_limit(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    if len(parts) != 3:
        await message.answer(
            "Формат: /set_limit @username <команди|-> <учасники|->\n"
            "Наприклад: /set_limit @kyrylo 10 200 або /set_limit @kyrylo - -"
        )
        return
    user = await _resolve_user(parts[0])
    if user is None:
        await message.answer("Не знайшов такого користувача.")
        return

    def parse(v: str) -> int | None:
        return None if v == "-" else int(v)

    try:
        teams_limit, members_limit = parse(parts[1]), parse(parts[2])
    except ValueError:
        await message.answer("Числа або «-», будь ласка.")
        return
    await repo.set_user_limits(user["id"], teams_limit, members_limit)
    await message.answer(
        f"Готово ✅ id {user['id']}: команд — {teams_limit or 'за роллю'}, "
        f"учасників — {members_limit or 'за роллю'}"
    )


@router.message(Command("set_team_limit"), admin_only)
async def cmd_set_team_limit(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    if len(parts) != 2 or not parts[0].isdigit():
        await message.answer("Формат: /set_team_limit <team_id> <число|->")
        return
    team = await repo.get_team(int(parts[0]))
    if team is None:
        await message.answer("Команду не знайдено.")
        return
    value = None if parts[1] == "-" else int(parts[1]) if parts[1].isdigit() else ...
    if value is ...:
        await message.answer("Число або «-», будь ласка.")
        return
    from app.db.core import db
    await db().execute(
        "UPDATE teams SET member_limit_override=? WHERE id=?", (value, team["id"])
    )
    await db().commit()
    await message.answer(
        f"Готово ✅ Команда «{team['name']}»: разовий ліміт учасників — {value or 'скинуто'}"
    )


async def _do_forget(user, actor_id: int) -> str:
    """Точкове видалення даних людини на її запит. Повертає квитанцію."""
    counts = await repo.delete_user_data(user["id"])
    from app.db.core import now
    stamp = now()
    log.info(
        "FORGET: адмін %s видалив дані користувача %s (%s анкет, %s архівних) о %s",
        actor_id, user["id"], counts["forms"], counts["archive"], stamp,
    )
    return (
        "🧾 Квитанція про видалення даних\n\n"
        f"Користувач: id {user['id']}"
        + (f" (@{user['username']})" if user["username"] else "") + "\n"
        f"Видалено анкет: {counts['forms']}, архівних копій: {counts['archive']}\n"
        f"Час (UTC): {stamp}\n"
        f"Виконав: адмін id {actor_id}\n\n"
        "Це повідомлення можна переслати людині як підтвердження."
    )


@router.message(Command("forget"), admin_only)
async def cmd_forget(message: Message, command: CommandObject, bot: Bot) -> None:
    user = await _resolve_user(command.args or "")
    if user is None:
        await message.answer("Формат: /forget @username (або числовий id)")
        return
    receipt = await _do_forget(user, message.from_user.id)
    await message.answer(receipt)
    await notify_admins(bot, receipt, exclude_id=message.from_user.id)


@router.message(Command("health"), admin_only)
async def cmd_health(message: Message) -> None:
    s = await repo.stats()
    try:
        db_size = os.path.getsize(DB_PATH) / 1024 / 1024
    except OSError:
        db_size = 0
    text = (
        "🩺 Стан бота\n\n"
        f"Аптайм: {runtime.uptime_hours():.1f} год\n"
        f"БД: {db_size:.1f} МБ\n"
        f"Користувачів: {s['users']}, команд: {s['teams']}, активних ігор: {s['active_games']}\n"
        f"Недоставлених пар: {s['undelivered']}\n"
        f"Скарг у черзі: {s['open_reports']}, заявок на ролі: {s['pending_roles']}"
    )
    # блок ресурсів із порогами — лише головному адміну
    if message.from_user.id == ADMIN_ID:
        text += "\n\n" + resources.health_block(DB_PATH)
    await message.answer(text)
