"""Команди: створення, картка, склад, вступ, скарги."""

import logging

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import keyboards as kb
from app import texts
from app.config import ADMIN_ID
from app.db import repo
from app.services import invites, limits, validators
from app.states import AddMember, CreateTeam, ReportReason

log = logging.getLogger(__name__)
router = Router(name="teams")


# ------------------------------------------------------------------ хелпери

async def _owned_team_or_none(cb: CallbackQuery, team_id: int):
    """Команда, якщо той, хто натиснув, — її власник (або адмін). Інакше None + відповідь."""
    team = await repo.get_team(team_id)
    if team is None or (
        team["owner_id"] != cb.from_user.id
        and not await repo.is_admin(cb.from_user.id, ADMIN_ID)
    ):
        await cb.answer("Ця дія недоступна", show_alert=True)
        return None
    return team


async def _team_card_payload(team) -> tuple[str, object]:
    game = await repo.active_game(team["id"])
    members = await repo.member_count(team["id"])
    forms_done = 0
    status = game["status"] if game else None
    if game:
        players = await repo.game_players_list(game["id"])
        forms_done = sum(1 for p in players if p["full_name"])
    text = texts.team_card(team["name"], members, status, forms_done, bool(team["is_temporary"]))
    return text, kb.team_card_kb(team["id"], status)


async def show_team_card(cb: CallbackQuery, team) -> None:
    text, markup = await _team_card_payload(team)
    try:
        await cb.message.edit_text(text, reply_markup=markup)
    except Exception:
        await cb.message.answer(text, reply_markup=markup)
    await cb.answer()


async def build_invite_link(bot: Bot, code: str) -> str:
    me = await bot.me()
    return f"https://t.me/{me.username}?start={code}"


async def _generate_unique_code() -> str:
    for _ in range(10):
        code = invites.generate_code()
        if await repo.get_team_by_code(code) is None:
            return code
    raise RuntimeError("Не вдалося згенерувати унікальний інвайт-код")


# ------------------------------------------------------------------ мої команди

@router.message(F.text == kb.BTN_MY_TEAMS)
async def my_teams(message: Message) -> None:
    own = await repo.owned_teams(message.from_user.id)
    member = await repo.member_teams(message.from_user.id)
    if not own and not member:
        await message.answer(texts.NO_TEAMS)
        return
    await message.answer(texts.MY_TEAMS, reply_markup=kb.teams_list(own, member))


@router.callback_query(kb.TeamCb.filter(F.act == "card"))
async def team_card(cb: CallbackQuery, callback_data: kb.TeamCb, state: FSMContext) -> None:
    await state.clear()
    team = await _owned_team_or_none(cb, callback_data.team_id)
    if team:
        await show_team_card(cb, team)


@router.callback_query(kb.TeamCb.filter(F.act == "mcard"))
async def member_card(cb: CallbackQuery, callback_data: kb.TeamCb) -> None:
    team = await repo.get_team(callback_data.team_id)
    member = team and await repo.get_member(team["id"], cb.from_user.id)
    if not team or not member or member["is_blocked"]:
        await cb.answer("Ця дія недоступна", show_alert=True)
        return
    game = await repo.active_game(team["id"])
    in_game = False
    drawn = False
    if game:
        players = await repo.game_players_list(game["id"])
        in_game = any(p["user_id"] == cb.from_user.id for p in players)
        drawn = game["status"] == "drawn"
    members = await repo.member_count(team["id"])
    text = texts.team_card(team["name"], members, game["status"] if game else None, 0, bool(team["is_temporary"]))
    # для учасника лічильник анкет не показуємо — рядок статусу спрощений
    text = text.split("\n")[0] + ("\nГра: жеребкування проведено 🎲" if drawn else "")
    await cb.message.edit_text(text, reply_markup=kb.member_card_kb(team["id"], in_game, drawn))
    await cb.answer()


# ------------------------------------------------------------------ створення

@router.message(F.text == kb.BTN_CREATE_TEAM)
async def create_team_start(message: Message, state: FSMContext) -> None:
    user = await repo.get_user(message.from_user.id)
    role = await repo.effective_role(message.from_user.id, ADMIN_ID)
    if not await repo.registration_open() and role == "user":
        await message.answer(texts.REGISTRATION_CLOSED)
        return
    defaults = await repo.get_limit_defaults()
    limit = limits.max_teams(role, user["max_teams_override"] if user else None, defaults)
    if len(await repo.owned_teams(message.from_user.id)) >= limit:
        await message.answer(texts.team_limit_reached(limit))
        return
    await state.set_state(CreateTeam.name)
    await message.answer(texts.CREATE_ASK_NAME)


@router.message(CreateTeam.name, F.text)
async def create_team_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if name.startswith("/") or name in kb.MENU_BUTTONS:
        await state.clear()
        raise SkipHandler  # хай спрацює звичайний хендлер команди/кнопки
    if len(name) > validators.MAX_TEAM_NAME:
        await message.answer(texts.CREATE_NAME_TOO_LONG)
        return
    await state.update_data(team_name=name)
    await state.set_state(CreateTeam.kind)
    await message.answer(texts.CREATE_ASK_TYPE, reply_markup=kb.team_type_kb())


@router.callback_query(CreateTeam.kind, kb.FormCb.filter(F.act == "temp"))
async def create_team_temp_ask(cb: CallbackQuery) -> None:
    await cb.message.edit_text(texts.CREATE_TEMP_CONFIRM, reply_markup=kb.temp_confirm_kb())
    await cb.answer()


@router.callback_query(CreateTeam.kind, kb.FormCb.filter(F.act.in_({"perm", "temp_yes"})))
async def create_team_final(cb: CallbackQuery, callback_data: kb.FormCb, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    name = data.get("team_name")
    if not name:
        await state.clear()
        await cb.answer(texts.ERROR, show_alert=True)
        return
    is_temporary = callback_data.act == "temp_yes"
    code = await _generate_unique_code()
    team_id = await repo.create_team(cb.from_user.id, name, code, is_temporary)
    await state.clear()
    link = await build_invite_link(bot, code)
    log.info("Команда %s створена користувачем %s (temp=%s)", team_id, cb.from_user.id, is_temporary)
    await cb.message.edit_text(texts.team_created(name, link, code))
    await cb.answer()


# ------------------------------------------------------------------ дії власника

@router.callback_query(kb.TeamCb.filter(F.act == "invite"))
async def team_invite(cb: CallbackQuery, callback_data: kb.TeamCb, bot: Bot) -> None:
    team = await _owned_team_or_none(cb, callback_data.team_id)
    if not team:
        return
    link = await build_invite_link(bot, team["invite_code"])
    await cb.message.answer(texts.invite_text(team["name"], link, team["invite_code"]))
    await cb.answer()


@router.callback_query(kb.TeamCb.filter(F.act == "members"))
async def team_members(cb: CallbackQuery, callback_data: kb.TeamCb) -> None:
    team = await _owned_team_or_none(cb, callback_data.team_id)
    if not team:
        return
    members = await repo.team_members_list(team["id"])
    game = await repo.active_game(team["id"])
    with_form = set()
    if game:
        players = await repo.game_players_list(game["id"])
        with_form = {p["user_id"] for p in players if p["full_name"]}
    lines = []
    for m in members:
        label = f"@{m['username']}" if m["username"] else str(m["user_id"])
        marks = []
        if m["user_id"] == team["owner_id"]:
            marks.append("👑")
        if m["is_blocked"]:
            marks.append("🚫")
        elif m["user_id"] in with_form:
            marks.append("✅ анкета")
        lines.append(f"• {label} {' '.join(marks)}".rstrip())
    await cb.message.answer("Учасники:\n" + "\n".join(lines))
    await cb.answer()


@router.callback_query(kb.TeamCb.filter(F.act == "noform"))
async def team_noform(cb: CallbackQuery, callback_data: kb.TeamCb) -> None:
    team = await _owned_team_or_none(cb, callback_data.team_id)
    if not team:
        return
    game = await repo.active_game(team["id"])
    if not game:
        await cb.answer("Активної гри немає", show_alert=True)
        return
    missing = await repo.users_without_form(game["id"])
    if not missing:
        await cb.answer("Всі анкети заповнені 👌", show_alert=True)
        return
    lines = [f"• @{m['username']}" if m["username"] else f"• id {m['user_id']}" for m in missing]
    await cb.message.answer("Без анкети:\n" + "\n".join(lines))
    await cb.answer()


@router.callback_query(kb.TeamCb.filter(F.act == "remind"))
async def team_remind(cb: CallbackQuery, callback_data: kb.TeamCb, bot: Bot) -> None:
    team = await _owned_team_or_none(cb, callback_data.team_id)
    if not team:
        return
    game = await repo.active_game(team["id"])
    if not game or game["status"] != "registration":
        await cb.answer("Реєстрація зараз не відкрита", show_alert=True)
        return
    missing = await repo.users_without_form(game["id"])
    if not missing:
        await cb.answer(texts.REMIND_NOBODY, show_alert=True)
        return
    sent = 0
    for m in missing:
        try:
            await bot.send_message(m["user_id"], texts.REMIND_TEXT)
            sent += 1
        except Exception:
            pass
    log.info("Команда %s: нагадування надіслано %s/%s", team["id"], sent, len(missing))
    await cb.answer(f"{texts.REMIND_SENT} ({sent}/{len(missing)})", show_alert=True)


@router.callback_query(kb.TeamCb.filter(F.act == "more"))
async def team_more(cb: CallbackQuery, callback_data: kb.TeamCb) -> None:
    team = await _owned_team_or_none(cb, callback_data.team_id)
    if not team:
        return
    game = await repo.active_game(team["id"])
    has_open_game = bool(game and game["status"] == "registration")
    await cb.message.edit_reply_markup(
        reply_markup=kb.team_more_kb(team["id"], has_open_game)
    )
    await cb.answer()


# --- додати вручну

@router.callback_query(kb.TeamCb.filter(F.act == "add"))
async def member_add_ask(cb: CallbackQuery, callback_data: kb.TeamCb, state: FSMContext) -> None:
    team = await _owned_team_or_none(cb, callback_data.team_id)
    if not team:
        return
    await state.set_state(AddMember.username)
    await state.update_data(team_id=team["id"])
    await cb.message.answer(texts.MEMBER_ASK_WHO_ADD)
    await cb.answer()


@router.message(AddMember.username, F.text)
async def member_add(message: Message, state: FSMContext, bot: Bot) -> None:
    """Додавання одного або одразу списку: @username чи id через кому/рядки."""
    text = (message.text or "").strip()
    if text.startswith("/") or text in kb.MENU_BUTTONS:
        await state.clear()
        raise SkipHandler
    data = await state.get_data()
    team = await repo.get_team(data.get("team_id", 0))
    if team is None or team["owner_id"] != message.from_user.id:
        await state.clear()
        await message.answer(texts.ERROR)
        return
    tokens = validators.parse_member_tokens(text)
    if not tokens:
        await message.answer(texts.MEMBER_ASK_WHO_ADD)
        return
    if len(tokens) > validators.MAX_BATCH_ADD:
        await message.answer(
            f"Забагато за один раз: {len(tokens)}. "
            f"Ліміт — {validators.MAX_BATCH_ADD}, надішли список частинами 🙂"
        )
        return

    from app.routers.joining import _capacity
    capacity = await _capacity(team)
    count = await repo.member_count(team["id"])
    added: list[str] = []
    existing: list[str] = []
    not_found: list[str] = []
    dm_failed: list[str] = []
    left_out: list[str] = []

    for token in tokens:
        label = f"id {token}" if token.isdigit() else f"@{token}"
        user = (
            await repo.get_user(int(token))
            if token.isdigit()
            else await repo.find_user_by_username(token)
        )
        if user is None:
            not_found.append(label)
            continue
        member = await repo.get_member(team["id"], user["id"])
        if member is not None and not member["is_blocked"]:
            existing.append(label)
            continue
        if count >= capacity:
            left_out.append(label)
            continue
        await repo.add_member(team["id"], user["id"], added_by=message.from_user.id)
        count += 1
        added.append(label)
        try:
            await bot.send_message(user["id"], texts.joined_team(team["name"]))
        except Exception:
            dm_failed.append(label)

    await state.clear()
    lines: list[str] = []
    if added:
        lines.append(f"✅ Додано ({len(added)}): {', '.join(added)}")
    if dm_failed:
        lines.append(
            f"…але попередити не зміг (бот не запущений?): {', '.join(dm_failed)} — "
            "скажи їм сам 🙂"
        )
    if existing:
        lines.append(f"🙂 Вже в команді ({len(existing)}): {', '.join(existing)}")
    if left_out:
        lines.append(
            f"⚠️ Не влізли в ліміт учасників ({capacity}): {', '.join(left_out)}. "
            "Адміністратор може збільшити ліміт."
        )
    if not_found:
        link = await build_invite_link(bot, team["invite_code"])
        lines.append(
            f"🤔 Не знайшов ({len(not_found)}): {', '.join(not_found)}\n"
            "Ці люди ще не запускали бота, тому я не можу написати їм першим. "
            f"Просто перешли їм запрошення:\n{link}"
        )
    await message.answer("\n\n".join(lines))


# --- видалити / заблокувати

@router.callback_query(kb.TeamCb.filter(F.act.in_({"del", "block", "skip"})))
async def member_pick(cb: CallbackQuery, callback_data: kb.TeamCb) -> None:
    team = await _owned_team_or_none(cb, callback_data.team_id)
    if not team:
        return
    game = await repo.active_game(team["id"])
    names = {}
    players = []
    if game:
        players = await repo.game_players_list(game["id"])
        for p in players:
            names[p["user_id"]] = p["full_name"]

    if callback_data.act == "skip":
        # прибрати лише з поточної гри — вибираємо серед гравців, а не всіх членів
        if not game or game["status"] != "registration":
            await cb.answer(
                "Зараз немає гри з відкритою реєстрацією.", show_alert=True
            )
            return
        candidates = [
            {"user_id": p["user_id"], "username": p["username"], "full_name": p["full_name"]}
            for p in players
            if p["user_id"] != team["owner_id"]
        ]
        if not candidates:
            await cb.answer("У поточній грі, крім тебе, нікого немає 🙂", show_alert=True)
            return
        await cb.message.edit_text(
            "Хто пропускає цю гру? (людина лишиться в команді)",
            reply_markup=kb.pick_member_kb(team["id"], "skipgo", candidates),
        )
        await cb.answer()
        return

    members = await repo.team_members_list(team["id"])
    candidates = [
        {
            "user_id": m["user_id"],
            "username": m["username"],
            "full_name": names.get(m["user_id"]),
        }
        for m in members
        if m["user_id"] != team["owner_id"]
    ]
    if not candidates:
        await cb.answer("Крім тебе, в команді нікого немає 🙂", show_alert=True)
        return
    act = "delgo" if callback_data.act == "del" else "blockgo"
    prompt = "Кого видалити з команди?" if act == "delgo" else "Кого заблокувати в команді?"
    await cb.message.edit_text(prompt, reply_markup=kb.pick_member_kb(team["id"], act, candidates))
    await cb.answer()


@router.callback_query(kb.MemberCb.filter(F.act.in_({"delgo", "blockgo", "skipgo"})))
async def member_action(cb: CallbackQuery, callback_data: kb.MemberCb, bot: Bot) -> None:
    team = await _owned_team_or_none(cb, callback_data.team_id)
    if not team:
        return
    game = await repo.active_game(team["id"])
    if game and game["status"] == "drawn":
        await cb.answer(
            "Жеребкування вже проведено — склад цієї гри змінити не можна. "
            "Спершу скинь гру.", show_alert=True,
        )
        return
    if callback_data.act == "skipgo":
        if not game:
            await cb.answer("Зараз немає гри з відкритою реєстрацією.", show_alert=True)
            return
        await repo.remove_player(game["id"], callback_data.user_id)
        note = texts.MEMBER_SKIPPED
        dm = (
            f"Організатор прибрав тебе з поточної гри в команді «{team['name']}». "
            "Ти лишаєшся в команді й зможеш грати наступного разу."
        )
    elif callback_data.act == "delgo":
        await repo.remove_member(team["id"], callback_data.user_id)
        note = texts.MEMBER_REMOVED
        dm = f"Організатор видалив тебе з команди «{team['name']}»."
    else:
        await repo.block_member(team["id"], callback_data.user_id)
        note = texts.MEMBER_BLOCKED
        dm = f"Організатор обмежив тобі доступ до команди «{team['name']}»."
    log.info("Команда %s: %s для користувача %s", team["id"], callback_data.act, callback_data.user_id)
    try:
        await bot.send_message(callback_data.user_id, dm)
    except Exception:
        pass
    await cb.message.edit_text(note)
    await cb.answer()
    text, markup = await _team_card_payload(team)
    await cb.message.answer(text, reply_markup=markup)


# --- скарга адміну

@router.callback_query(kb.TeamCb.filter(F.act == "report"))
async def report_pick(cb: CallbackQuery, callback_data: kb.TeamCb) -> None:
    team = await _owned_team_or_none(cb, callback_data.team_id)
    if not team:
        return
    members = await repo.team_members_list(team["id"])
    candidates = [
        {"user_id": m["user_id"], "username": m["username"], "full_name": None}
        for m in members if m["user_id"] != team["owner_id"]
    ]
    if not candidates:
        await cb.answer("Крім тебе, в команді нікого немає 🙂", show_alert=True)
        return
    await cb.message.edit_text(
        "На кого скарга?", reply_markup=kb.pick_member_kb(team["id"], "rep", candidates)
    )
    await cb.answer()


@router.callback_query(kb.MemberCb.filter(F.act == "rep"))
async def report_reason_ask(cb: CallbackQuery, callback_data: kb.MemberCb, state: FSMContext) -> None:
    team = await _owned_team_or_none(cb, callback_data.team_id)
    if not team:
        return
    await state.set_state(ReportReason.reason)
    await state.update_data(team_id=team["id"], reported_id=callback_data.user_id)
    await cb.message.answer(texts.REPORT_ASK_REASON)
    await cb.answer()


@router.message(ReportReason.reason, F.text)
async def report_create(message: Message, state: FSMContext, bot: Bot) -> None:
    reason = (message.text or "").strip()
    if reason.startswith("/") or reason in kb.MENU_BUTTONS:
        await state.clear()
        raise SkipHandler
    if len(reason) > validators.MAX_REPORT_REASON:
        await message.answer(texts.form_too_long(validators.MAX_REPORT_REASON))
        return
    data = await state.get_data()
    await state.clear()
    report_id = await repo.create_report(
        message.from_user.id, data["reported_id"], data["team_id"], reason,
        author_msg_id=message.message_id,
    )
    await message.answer(texts.REPORT_SENT)
    reported = await repo.get_user(data["reported_id"])
    reported_ref = texts.person_ref(
        data["reported_id"], reported["username"] if reported else None
    )
    from app.routers.admin import notify_admins
    await notify_admins(
        bot,
        f"⚠️ Нова скарга #{report_id}\n"
        f"На: {reported_ref}\n"
        f"Від: {texts.person_ref(message.from_user.id, message.from_user.username)}\n"
        f"Причина: {reason}",
        reply_markup=kb.report_actions_kb(
            report_id, "user", "open", bool(message.from_user.username)
        ),
    )


# ------------------------------------------------------------------ дії учасника

@router.callback_query(kb.TeamCb.filter(F.act == "leavegame"))
async def leave_game(cb: CallbackQuery, callback_data: kb.TeamCb) -> None:
    team = await repo.get_team(callback_data.team_id)
    if team is None:
        await cb.answer(texts.ERROR, show_alert=True)
        return
    game = await repo.active_game(team["id"])
    if game is None:
        await cb.answer("Активної гри немає", show_alert=True)
        return
    if game["status"] == "drawn":
        await cb.answer(texts.LEAVE_DRAWN, show_alert=True)
        return
    await repo.remove_player(game["id"], cb.from_user.id)
    await cb.message.edit_text(texts.LEFT_GAME)
    await cb.answer()


@router.callback_query(kb.TeamCb.filter(F.act == "leaveteam"))
async def leave_team(cb: CallbackQuery, callback_data: kb.TeamCb) -> None:
    team = await repo.get_team(callback_data.team_id)
    if team is None or team["owner_id"] == cb.from_user.id:
        await cb.answer("Ця дія недоступна", show_alert=True)
        return
    game = await repo.active_game(team["id"])
    if game and game["status"] == "drawn":
        players = await repo.game_players_list(game["id"])
        if any(p["user_id"] == cb.from_user.id for p in players):
            await cb.answer(texts.LEAVE_DRAWN, show_alert=True)
            return
    await repo.remove_member(team["id"], cb.from_user.id)
    await cb.message.edit_text(texts.LEFT_TEAM)
    await cb.answer()
