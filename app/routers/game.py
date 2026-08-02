"""Гра: жеребкування, доставка, /myreceiver, скидання, завершення, пари."""

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app import keyboards as kb
from app import texts
from app.config import ADMIN_ID
from app.db import repo
from app.routers.teams import _owned_team_or_none, show_team_card
from app.services import dates
from app.services import draw as draw_service
from app.services import limits, validators
from app.services.delivery import deliver_pairs

log = logging.getLogger(__name__)
router = Router(name="game")

STALE_DAYS = 300


async def _eligible_players(game_id: int) -> tuple[list[int], list[str]]:
    """(гравці з анкетами, підписи тих, хто без анкети)."""
    players = await repo.game_players_list(game_id)
    with_form = [p["user_id"] for p in players if p["full_name"]]
    without = [
        f"@{p['username']}" if p["username"] else f"id {p['user_id']}"
        for p in players if not p["full_name"]
    ]
    return with_form, without


# ------------------------------------------------------------------ жеребкування

@router.callback_query(kb.TeamCb.filter(F.act == "draw"))
async def draw_entry(cb: CallbackQuery, callback_data: kb.TeamCb) -> None:
    team = await _owned_team_or_none(cb, callback_data.team_id)
    if not team:
        return
    game = await repo.active_game(team["id"])
    if game is None or game["status"] != "registration":
        await cb.answer(texts.DRAW_ALREADY, show_alert=True)
        return
    days = dates.days_since(await repo.last_drawn_at(team["id"]))
    if days is not None and days > STALE_DAYS:
        await cb.message.edit_text(
            texts.staleness_warning(days),
            reply_markup=kb.confirm_kb(
                kb.TeamCb(act="drawcheck", team_id=team["id"]),
                kb.TeamCb(act="card", team_id=team["id"]),
                yes_text="✅ Склад актуальний",
            ),
        )
        await cb.answer()
        return
    await _draw_precheck(cb, team)


@router.callback_query(kb.TeamCb.filter(F.act == "drawcheck"))
async def draw_precheck_cb(cb: CallbackQuery, callback_data: kb.TeamCb) -> None:
    team = await _owned_team_or_none(cb, callback_data.team_id)
    if team:
        await _draw_precheck(cb, team)


async def _draw_precheck(cb: CallbackQuery, team) -> None:
    game = await repo.active_game(team["id"])
    if game is None or game["status"] != "registration":
        await cb.answer(texts.DRAW_ALREADY, show_alert=True)
        return
    eligible, without = await _eligible_players(game["id"])
    if len(eligible) < 2:
        await cb.answer(texts.DRAW_NOT_ENOUGH, show_alert=True)
        return
    await cb.message.edit_text(
        texts.draw_confirm(len(eligible), without),
        reply_markup=kb.confirm_kb(
            kb.TeamCb(act="drawgo", team_id=team["id"]),
            kb.TeamCb(act="card", team_id=team["id"]),
            yes_text="🎲 Провести",
        ),
    )
    await cb.answer()


@router.callback_query(kb.TeamCb.filter(F.act == "drawgo"))
async def draw_go(cb: CallbackQuery, callback_data: kb.TeamCb, bot: Bot) -> None:
    team = await _owned_team_or_none(cb, callback_data.team_id)
    if not team:
        return
    game = await repo.active_game(team["id"])
    if game is None or game["status"] != "registration":
        await cb.answer(texts.DRAW_ALREADY, show_alert=True)
        return
    eligible, _ = await _eligible_players(game["id"])
    if len(eligible) < 2:
        await cb.answer(texts.DRAW_NOT_ENOUGH, show_alert=True)
        return

    pairs = draw_service.make_pairs(eligible)
    await repo.commit_draw(game["id"], pairs)
    log.info("Жеребкування гри %s: %s пар", game["id"], len(pairs))
    await cb.message.edit_text("Жеребкування проведено, розсилаю отримувачів… 🎲")
    await cb.answer()

    delivered, total, failed = await deliver_pairs(bot, game["id"], team["name"])
    await cb.message.answer(texts.draw_done(delivered, total, failed))
    from app.routers.teams import _team_card_payload
    text, markup = await _team_card_payload(await repo.get_team(team["id"]))
    await cb.message.answer(text, reply_markup=markup)


@router.callback_query(kb.TeamCb.filter(F.act == "redeliver"))
async def redeliver(cb: CallbackQuery, callback_data: kb.TeamCb, bot: Bot) -> None:
    team = await _owned_team_or_none(cb, callback_data.team_id)
    if not team:
        return
    game = await repo.active_game(team["id"])
    if game is None or game["status"] != "drawn":
        await cb.answer("Немає гри з проведеним жеребкуванням", show_alert=True)
        return
    if not await repo.undelivered_pairs(game["id"]):
        await cb.answer("Всі повідомлення вже доставлені 👌", show_alert=True)
        return
    await cb.answer("Повторюю розсилку…")
    delivered, total, failed = await deliver_pairs(bot, game["id"], team["name"])
    await cb.message.answer(texts.draw_done(delivered, total, failed))


# ------------------------------------------------------------------ пари (зі спойлер-захистом)

@router.callback_query(kb.TeamCb.filter(F.act == "pairs"))
async def pairs_warn(cb: CallbackQuery, callback_data: kb.TeamCb) -> None:
    team = await _owned_team_or_none(cb, callback_data.team_id)
    if not team:
        return
    await cb.message.edit_text(
        texts.PAIRS_WARNING,
        reply_markup=kb.confirm_kb(
            kb.TeamCb(act="pairs2", team_id=team["id"]),
            kb.TeamCb(act="card", team_id=team["id"]),
            yes_text="👀 Так, розумію",
        ),
    )
    await cb.answer()


@router.callback_query(kb.TeamCb.filter(F.act == "pairs2"))
async def pairs_24h(cb: CallbackQuery, callback_data: kb.TeamCb) -> None:
    team = await _owned_team_or_none(cb, callback_data.team_id)
    if not team:
        return
    game = await repo.active_game(team["id"])
    if game is None or game["status"] != "drawn":
        await cb.answer("Пар ще немає", show_alert=True)
        return
    days = dates.days_since(game["drawn_at"])
    hours_fresh = days is not None and days < 1
    if hours_fresh:
        await cb.message.edit_text(
            texts.PAIRS_24H,
            reply_markup=kb.confirm_kb(
                kb.TeamCb(act="pairs3", team_id=team["id"]),
                kb.TeamCb(act="card", team_id=team["id"]),
                yes_text="Все одно показати",
            ),
        )
        await cb.answer()
        return
    await _show_pairs(cb, team, game)


@router.callback_query(kb.TeamCb.filter(F.act == "pairs3"))
async def pairs_show_final(cb: CallbackQuery, callback_data: kb.TeamCb) -> None:
    team = await _owned_team_or_none(cb, callback_data.team_id)
    if not team:
        return
    game = await repo.active_game(team["id"])
    if game is None or game["status"] != "drawn":
        await cb.answer("Пар ще немає", show_alert=True)
        return
    await _show_pairs(cb, team, game)


async def _show_pairs(cb: CallbackQuery, team, game) -> None:
    pairs = await repo.pairs_of_game(game["id"])
    lines = []
    for p in pairs:
        giver = await repo.get_form(game["id"], p["giver_id"])
        receiver = await repo.get_form(game["id"], p["receiver_id"])
        g = validators.format_short_name(giver["full_name"]) if giver else f"id {p['giver_id']}"
        r = validators.format_short_name(receiver["full_name"]) if receiver else f"id {p['receiver_id']}"
        lines.append(f"{g} → {r}")
    log.info("Організатор %s відкрив пари гри %s", cb.from_user.id, game["id"])
    await cb.message.edit_text(
        "Пари 🎁\n\n" + "\n".join(lines),
        reply_markup=kb.back_to_card_kb(team["id"]),
    )
    await cb.answer()


# ------------------------------------------------------------------ скидання / завершення / нова гра

@router.callback_query(kb.TeamCb.filter(F.act == "reset"))
async def reset_warn(cb: CallbackQuery, callback_data: kb.TeamCb) -> None:
    team = await _owned_team_or_none(cb, callback_data.team_id)
    if not team:
        return
    await cb.message.edit_text(
        texts.RESET_CONFIRM,
        reply_markup=kb.confirm_kb(
            kb.TeamCb(act="resetgo", team_id=team["id"]),
            kb.TeamCb(act="card", team_id=team["id"]),
            yes_text="🔄 Так, скинути",
        ),
    )
    await cb.answer()


@router.callback_query(kb.TeamCb.filter(F.act == "resetgo"))
async def reset_go(cb: CallbackQuery, callback_data: kb.TeamCb, bot: Bot) -> None:
    team = await _owned_team_or_none(cb, callback_data.team_id)
    if not team:
        return
    game = await repo.active_game(team["id"])
    if game is None or game["status"] != "drawn":
        await cb.answer("Скидати нічого — жеребкування не проводилось", show_alert=True)
        return
    players = await repo.game_players_list(game["id"])
    await repo.reset_draw(game["id"])
    log.info("Гру %s скинуто організатором %s", game["id"], cb.from_user.id)
    for p in players:
        try:
            await bot.send_message(p["user_id"], texts.RESET_NOTIFY)
        except Exception:
            pass
    await cb.message.edit_text(texts.RESET_DONE, reply_markup=kb.back_to_card_kb(team["id"]))
    await cb.answer()


@router.callback_query(kb.TeamCb.filter(F.act == "finish"))
async def finish_warn(cb: CallbackQuery, callback_data: kb.TeamCb) -> None:
    team = await _owned_team_or_none(cb, callback_data.team_id)
    if not team:
        return
    if team["is_temporary"]:
        owner_role = await repo.effective_role(team["owner_id"], ADMIN_ID)
        text = texts.finish_confirm_temp(limits.retention_days(owner_role))
    else:
        text = texts.FINISH_CONFIRM
    await cb.message.edit_text(
        text,
        reply_markup=kb.confirm_kb(
            kb.TeamCb(act="finishgo", team_id=team["id"]),
            kb.TeamCb(act="card", team_id=team["id"]),
            yes_text="🏁 Завершити",
        ),
    )
    await cb.answer()


@router.callback_query(kb.TeamCb.filter(F.act == "finishgo"))
async def finish_go(cb: CallbackQuery, callback_data: kb.TeamCb) -> None:
    team = await _owned_team_or_none(cb, callback_data.team_id)
    if not team:
        return
    game = await repo.active_game(team["id"])
    if game is None:
        await cb.answer("Активної гри немає", show_alert=True)
        return
    await repo.finish_game(game["id"])
    if team["is_temporary"]:
        await repo.archive_and_purge_temp_team(team["id"], game["id"])
        log.info("Одноразову команду %s заархівовано", team["id"])
        owner_role = await repo.effective_role(team["owner_id"], ADMIN_ID)
        await cb.message.edit_text(
            texts.finish_done_temp(limits.retention_days(owner_role))
        )
    else:
        await cb.message.edit_text(texts.FINISH_DONE)
    await cb.answer()


@router.callback_query(kb.TeamCb.filter(F.act == "newgame"))
async def new_game(cb: CallbackQuery, callback_data: kb.TeamCb) -> None:
    team = await _owned_team_or_none(cb, callback_data.team_id)
    if not team:
        return
    if team["is_temporary"] or team["is_archived"]:
        await cb.answer(texts.TEAM_ARCHIVED, show_alert=True)
        return
    if await repo.active_game(team["id"]):
        await cb.answer("Активна гра вже є 🙂", show_alert=True)
        return
    days = dates.days_since(await repo.last_drawn_at(team["id"]))
    if days is not None and days > STALE_DAYS:
        await cb.message.edit_text(
            texts.staleness_warning(days),
            reply_markup=kb.confirm_kb(
                kb.TeamCb(act="newgamego", team_id=team["id"]),
                kb.TeamCb(act="card", team_id=team["id"]),
                yes_text="✅ Склад актуальний",
            ),
        )
        await cb.answer()
        return
    await _create_new_game(cb, team)


@router.callback_query(kb.TeamCb.filter(F.act == "newgamego"))
async def new_game_go(cb: CallbackQuery, callback_data: kb.TeamCb) -> None:
    team = await _owned_team_or_none(cb, callback_data.team_id)
    if not team:
        return
    if await repo.active_game(team["id"]):
        await cb.answer("Активна гра вже є 🙂", show_alert=True)
        return
    await _create_new_game(cb, team)


async def _create_new_game(cb: CallbackQuery, team) -> None:
    await repo.create_game(team["id"])
    log.info("Нова гра для команди %s", team["id"])
    await cb.message.edit_text(texts.NEW_GAME_STARTED)
    await show_team_card(cb, team)


# ------------------------------------------------------------------ мій отримувач

@router.message(F.text == kb.BTN_MY_RECEIVER)
@router.message(Command("myreceiver"))
async def my_receiver(message: Message) -> None:
    games = await repo.drawn_games_of_user(message.from_user.id)
    if not games:
        await message.answer(texts.NO_RECEIVER)
        return
    for g in games:
        form = await repo.get_form(g["game_id"], g["receiver_id"])
        if form:
            await message.answer(texts.receiver_message(g["team_name"], dict(form)))
        else:
            await message.answer(texts.NO_RECEIVER)
