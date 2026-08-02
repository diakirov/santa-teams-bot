"""Адмінка: статистика, скарги, ролі, ліміти, бан, вимикач реєстрації, /health."""

import logging
import os

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.types import CallbackQuery, Message

from app import keyboards as kb
from app import texts
from app.config import ADMIN_ID, DB_PATH
from app.db import repo
from app import runtime

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

@router.message(Command("admin"), admin_only)
async def admin_panel(message: Message) -> None:
    await message.answer(
        "Панель адміністратора 🛠",
        reply_markup=kb.admin_menu_kb(await repo.registration_open()),
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


@router.callback_query(kb.AdminCb.filter(F.act == "reports"))
async def admin_reports(cb: CallbackQuery) -> None:
    if not await _is_admin_cb(cb):
        await cb.answer("Ця дія недоступна", show_alert=True)
        return
    reports = await repo.open_reports()
    if not reports:
        await cb.answer("Скарг немає 👌", show_alert=True)
        return
    for r in reports[:10]:
        username = f"@{r['reported_username']}" if r["reported_username"] else ""
        await cb.message.answer(
            f"Скарга #{r['id']} від {r['created_at']}\n"
            f"На: {username} (id {r['reported_user_id']})\n"
            f"Причина: {r['reason']}",
            reply_markup=kb.report_kb(r["id"]),
        )
    await cb.answer()


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
        outcome = "відхилено ✖️"
    await cb.message.edit_text(f"Скарга #{report['id']}: {outcome}")
    await notify_admins(
        bot,
        f"ℹ️ Скаргу #{report['id']} розглянув @{cb.from_user.username or cb.from_user.id}: {outcome}",
        exclude_id=cb.from_user.id,
    )
    try:
        await bot.send_message(
            report["reporter_id"],
            f"Твою скаргу #{report['id']} розглянуто. Дякую, що допомагаєш тримати гру чесною 🙌",
        )
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
        "Бан: /ban @username причина · Розбан: /unban @username\n"
        "Видалити дані людини на її запит: /forget @username\n"
        "Адміни (лише головний): /make_admin @username · /remove_admin @username"
    )
    await cb.answer()


@router.callback_query(kb.AdminCb.filter(F.act == "toggle_reg"))
async def admin_toggle_registration(cb: CallbackQuery) -> None:
    if not await _is_admin_cb(cb):
        await cb.answer("Ця дія недоступна", show_alert=True)
        return
    is_open = await repo.registration_open()
    await repo.set_setting("registration_open", "0" if is_open else "1")
    state = "закрито ⏸" if is_open else "відкрито ▶️"
    log.info("Створення нових команд: %s", state)
    await cb.message.edit_text(
        f"Створення нових команд: {state}",
        reply_markup=kb.admin_menu_kb(not is_open),
    )
    await cb.answer()


# ------------------------------------------------------------------ текстові адмін-команди

async def _resolve_user(arg: str):
    arg = arg.strip()
    if arg.isdigit():
        return await repo.get_user(int(arg))
    return await repo.find_user_by_username(arg)


@router.message(Command("ban"), admin_only)
async def cmd_ban(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split(maxsplit=1)
    if not parts:
        await message.answer("Формат: /ban @username причина")
        return
    user = await _resolve_user(parts[0])
    if user is None:
        await message.answer("Не знайшов такого користувача.")
        return
    if user["id"] == ADMIN_ID:
        await message.answer("Головного адміна забанити не можна 🙂")
        return
    if await repo.is_admin(user["id"], ADMIN_ID) and message.from_user.id != ADMIN_ID:
        await message.answer("Іншого адміністратора може банити лише головний адмін.")
        return
    reason = parts[1] if len(parts) > 1 else "без причини"
    await repo.set_ban(user["id"], True, reason, banned_by=message.from_user.id)
    runtime.access_middleware.invalidate_ban_cache(user["id"])
    log.info("Бан користувача %s від %s: %s", user["id"], message.from_user.id, reason)
    await message.answer(f"Користувача id {user['id']} забанено 🚫")


@router.message(Command("unban"), admin_only)
async def cmd_unban(message: Message, command: CommandObject) -> None:
    user = await _resolve_user(command.args or "")
    if user is None:
        await message.answer("Не знайшов такого користувача.")
        return
    if (
        user["is_banned"]
        and user["banned_by"] == ADMIN_ID
        and message.from_user.id != ADMIN_ID
    ):
        await message.answer(
            "Цього користувача забанив головний адмін — зняти бан може лише він."
        )
        return
    await repo.set_ban(user["id"], False, None)
    runtime.access_middleware.invalidate_ban_cache(user["id"])
    log.info("Розбан користувача %s від %s", user["id"], message.from_user.id)
    await message.answer(f"Користувача id {user['id']} розбанено ✅")


@router.message(Command("make_admin"), F.from_user.id == ADMIN_ID)
async def cmd_make_admin(message: Message, command: CommandObject, bot: Bot) -> None:
    user = await _resolve_user(command.args or "")
    if user is None:
        await message.answer("Не знайшов такого користувача (він має хоч раз запустити бота).")
        return
    if user["id"] == ADMIN_ID or user["role"] == "admin":
        await message.answer("Ця людина вже адміністратор 🙂")
        return
    await repo.set_role(user["id"], "admin")
    log.info("Користувача %s призначено адміністратором", user["id"])
    await message.answer(f"Готово ✅ id {user['id']} тепер адміністратор.")
    try:
        await bot.send_message(
            user["id"],
            "Тобі надано роль адміністратора 🛠 Панель: /admin, довідка по командах — "
            "кнопка «⚙️ Ліміти» в панелі.",
        )
    except Exception:
        pass


@router.message(Command("remove_admin"), F.from_user.id == ADMIN_ID)
async def cmd_remove_admin(message: Message, command: CommandObject) -> None:
    user = await _resolve_user(command.args or "")
    if user is None:
        await message.answer("Не знайшов такого користувача.")
        return
    if user["id"] == ADMIN_ID:
        await message.answer("Головного адміна зняти не можна 🙂")
        return
    if user["role"] != "admin":
        await message.answer("Ця людина й так не адміністратор.")
        return
    await repo.set_role(user["id"], "user")
    log.info("Користувача %s знято з адміністраторів", user["id"])
    await message.answer(
        f"Готово ✅ id {user['id']} більше не адміністратор (роль — звичайний користувач; "
        "якщо треба керівник — нехай подасть /role)."
    )


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


@router.message(Command("forget"), admin_only)
async def cmd_forget(message: Message, command: CommandObject, bot: Bot) -> None:
    """Точкове видалення даних людини на її запит, не чекаючи ретенції."""
    user = await _resolve_user(command.args or "")
    if user is None:
        await message.answer("Формат: /forget @username (або числовий id)")
        return
    counts = await repo.delete_user_data(user["id"])
    from app.db.core import now
    stamp = now()
    log.info(
        "FORGET: адмін %s видалив дані користувача %s (%s анкет, %s архівних) о %s",
        message.from_user.id, user["id"], counts["forms"], counts["archive"], stamp,
    )
    receipt = (
        "🧾 Квитанція про видалення даних\n\n"
        f"Користувач: id {user['id']}"
        + (f" (@{user['username']})" if user["username"] else "") + "\n"
        f"Видалено анкет: {counts['forms']}, архівних копій: {counts['archive']}\n"
        f"Час (UTC): {stamp}\n"
        f"Виконав: адмін id {message.from_user.id}\n\n"
        "Це повідомлення можна переслати людині як підтвердження."
    )
    await message.answer(receipt)
    await notify_admins(bot, receipt, exclude_id=message.from_user.id)


@router.message(Command("health"), admin_only)
async def cmd_health(message: Message) -> None:
    s = await repo.stats()
    try:
        db_size = os.path.getsize(DB_PATH) / 1024 / 1024
    except OSError:
        db_size = 0
    await message.answer(
        "🩺 Стан бота\n\n"
        f"Аптайм: {runtime.uptime_hours():.1f} год\n"
        f"БД: {db_size:.1f} МБ\n"
        f"Користувачів: {s['users']}, команд: {s['teams']}, активних ігор: {s['active_games']}\n"
        f"Недоставлених пар: {s['undelivered']}\n"
        f"Скарг у черзі: {s['open_reports']}, заявок на ролі: {s['pending_roles']}"
    )
