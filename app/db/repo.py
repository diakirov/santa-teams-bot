"""Усі запити до БД, згруповані за доменом. Роутери ходять сюди, не в SQL напряму."""

from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

from app.db.core import db, now

Row = aiosqlite.Row


# ------------------------------------------------------------------ users

async def upsert_user(user_id: int, username: str | None) -> None:
    ts = now()
    await db().execute(
        "INSERT INTO users (id, username, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET username=excluded.username, last_seen_at=excluded.last_seen_at",
        (user_id, username, ts, ts),
    )
    await db().commit()


async def get_user(user_id: int) -> Row | None:
    cur = await db().execute("SELECT * FROM users WHERE id=?", (user_id,))
    return await cur.fetchone()


async def find_user_by_username(username: str) -> Row | None:
    cur = await db().execute(
        "SELECT * FROM users WHERE lower(username)=lower(?)", (username.lstrip("@"),)
    )
    return await cur.fetchone()


async def set_role(user_id: int, role: str) -> None:
    await db().execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
    await db().commit()


async def set_ban(
    user_id: int, banned: bool, reason: str | None = None, banned_by: int | None = None
) -> None:
    await db().execute(
        "UPDATE users SET is_banned=?, ban_reason=?, banned_by=?, banned_at=? WHERE id=?",
        (
            1 if banned else 0,
            reason,
            banned_by if banned else None,
            now() if banned else None,
            user_id,
        ),
    )
    await db().commit()


async def is_admin(user_id: int, main_admin_id: int) -> bool:
    """Адмін — це головний (з .env) або користувач із роллю admin у БД."""
    if user_id == main_admin_id:
        return True
    user = await get_user(user_id)
    return bool(user and user["role"] == "admin")


async def effective_role(user_id: int, main_admin_id: int) -> str:
    """Роль з урахуванням головного адміна, чий id живе в .env, а не в БД.

    Все, що залежить від ролі (ліміти, ретенція, тексти), має питати саме це.
    """
    if user_id == main_admin_id:
        return "admin"
    user = await get_user(user_id)
    return user["role"] if user else "user"


async def users_by_role(role: str) -> list[Row]:
    cur = await db().execute(
        "SELECT * FROM users WHERE role=? ORDER BY lower(coalesce(username,'')), id",
        (role,),
    )
    return list(await cur.fetchall())


async def admin_ids(main_admin_id: int) -> list[int]:
    cur = await db().execute("SELECT id FROM users WHERE role='admin'")
    ids = {r["id"] for r in await cur.fetchall()}
    ids.add(main_admin_id)
    return sorted(ids)


async def set_user_limits(
    user_id: int, max_teams: int | None, max_members: int | None
) -> None:
    await db().execute(
        "UPDATE users SET max_teams_override=?, max_members_override=? WHERE id=?",
        (max_teams, max_members, user_id),
    )
    await db().commit()


# ------------------------------------------------------------------ settings

async def get_settings() -> dict[str, str]:
    cur = await db().execute("SELECT key, value FROM settings")
    return {r["key"]: r["value"] for r in await cur.fetchall()}


async def get_limit_defaults() -> dict[str, int]:
    settings = await get_settings()
    return {k: int(v) for k, v in settings.items() if k.startswith("limit.")}


async def set_setting(key: str, value: str) -> None:
    await db().execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    await db().commit()


async def registration_open() -> bool:
    settings = await get_settings()
    return settings.get("registration_open", "1") == "1"


# ------------------------------------------------------------------ teams

async def create_team(
    owner_id: int, name: str, invite_code: str, is_temporary: bool
) -> int:
    cur = await db().execute(
        "INSERT INTO teams (owner_id, name, invite_code, is_temporary, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (owner_id, name, invite_code, 1 if is_temporary else 0, now()),
    )
    team_id = cur.lastrowid
    await db().execute(
        "INSERT INTO team_members (team_id, user_id, joined_at) VALUES (?, ?, ?)",
        (team_id, owner_id, now()),
    )
    await db().execute(
        "INSERT INTO games (team_id, created_at) VALUES (?, ?)", (team_id, now())
    )
    cur = await db().execute(
        "SELECT id FROM games WHERE team_id=? AND status='registration'", (team_id,)
    )
    game = await cur.fetchone()
    await db().execute(
        "INSERT INTO game_players (game_id, user_id, joined_at) VALUES (?, ?, ?)",
        (game["id"], owner_id, now()),
    )
    await db().commit()
    return team_id


async def get_team(team_id: int) -> Row | None:
    cur = await db().execute("SELECT * FROM teams WHERE id=?", (team_id,))
    return await cur.fetchone()


async def get_team_by_code(code: str) -> Row | None:
    cur = await db().execute(
        "SELECT * FROM teams WHERE upper(invite_code)=upper(?)", (code.strip(),)
    )
    return await cur.fetchone()


async def set_invite_code(team_id: int, code: str) -> None:
    await db().execute("UPDATE teams SET invite_code=? WHERE id=?", (code, team_id))
    await db().commit()


async def owned_teams(owner_id: int, include_archived: bool = False) -> list[Row]:
    sql = "SELECT * FROM teams WHERE owner_id=?"
    if not include_archived:
        sql += " AND is_archived=0"
    cur = await db().execute(sql + " ORDER BY created_at DESC", (owner_id,))
    return list(await cur.fetchall())


async def member_teams(user_id: int) -> list[Row]:
    cur = await db().execute(
        "SELECT t.* FROM teams t JOIN team_members m ON m.team_id=t.id "
        "WHERE m.user_id=? AND m.is_blocked=0 AND t.is_archived=0 AND t.owner_id != ? "
        "ORDER BY t.created_at DESC",
        (user_id, user_id),
    )
    return list(await cur.fetchall())


async def archive_team(team_id: int) -> None:
    await db().execute("UPDATE teams SET is_archived=1 WHERE id=?", (team_id,))
    await db().commit()


async def delete_team(team_id: int) -> None:
    await db().execute("DELETE FROM teams WHERE id=?", (team_id,))
    await db().commit()


# ------------------------------------------------------------------ members

async def get_member(team_id: int, user_id: int) -> Row | None:
    cur = await db().execute(
        "SELECT * FROM team_members WHERE team_id=? AND user_id=?", (team_id, user_id)
    )
    return await cur.fetchone()


async def member_count(team_id: int) -> int:
    cur = await db().execute(
        "SELECT COUNT(*) AS n FROM team_members WHERE team_id=? AND is_blocked=0",
        (team_id,),
    )
    return (await cur.fetchone())["n"]


async def add_member(
    team_id: int, user_id: int, added_by: int | None = None
) -> None:
    """Додає в команду і в активну гру, якщо та ще на етапі реєстрації."""
    await db().execute(
        "INSERT INTO team_members (team_id, user_id, added_by, joined_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(team_id, user_id) DO UPDATE SET is_blocked=0",
        (team_id, user_id, added_by, now()),
    )
    game = await active_game(team_id)
    if game and game["status"] == "registration":
        await db().execute(
            "INSERT OR IGNORE INTO game_players (game_id, user_id, joined_at) VALUES (?, ?, ?)",
            (game["id"], user_id, now()),
        )
    await db().commit()


async def remove_member(team_id: int, user_id: int) -> None:
    """Прибирає з команди і з активної гри (якщо реєстрація ще відкрита)."""
    await db().execute(
        "DELETE FROM team_members WHERE team_id=? AND user_id=?", (team_id, user_id)
    )
    game = await active_game(team_id)
    if game and game["status"] == "registration":
        await db().execute(
            "DELETE FROM game_players WHERE game_id=? AND user_id=?",
            (game["id"], user_id),
        )
        await db().execute(
            "DELETE FROM forms WHERE game_id=? AND user_id=?", (game["id"], user_id)
        )
    await db().commit()


async def block_member(team_id: int, user_id: int) -> None:
    await db().execute(
        "UPDATE team_members SET is_blocked=1 WHERE team_id=? AND user_id=?",
        (team_id, user_id),
    )
    game = await active_game(team_id)
    if game and game["status"] == "registration":
        await db().execute(
            "DELETE FROM game_players WHERE game_id=? AND user_id=?",
            (game["id"], user_id),
        )
        await db().execute(
            "DELETE FROM forms WHERE game_id=? AND user_id=?", (game["id"], user_id)
        )
    await db().commit()


async def team_members_list(team_id: int) -> list[Row]:
    cur = await db().execute(
        "SELECT m.*, u.username, u.id AS user_id FROM team_members m "
        "JOIN users u ON u.id=m.user_id WHERE m.team_id=? ORDER BY m.joined_at",
        (team_id,),
    )
    return list(await cur.fetchall())


# ------------------------------------------------------------------ games

async def active_game(team_id: int) -> Row | None:
    cur = await db().execute(
        "SELECT * FROM games WHERE team_id=? AND status IN ('registration','drawn')",
        (team_id,),
    )
    return await cur.fetchone()


async def get_game(game_id: int) -> Row | None:
    cur = await db().execute("SELECT * FROM games WHERE id=?", (game_id,))
    return await cur.fetchone()


async def create_game(team_id: int) -> int:
    """Нова гра для постійної команди: всі поточні учасники стають гравцями."""
    cur = await db().execute(
        "INSERT INTO games (team_id, created_at) VALUES (?, ?)", (team_id, now())
    )
    game_id = cur.lastrowid
    await db().execute(
        "INSERT INTO game_players (game_id, user_id, joined_at) "
        "SELECT ?, user_id, ? FROM team_members WHERE team_id=? AND is_blocked=0",
        (game_id, now(), team_id),
    )
    await db().commit()
    return game_id


async def last_drawn_at(team_id: int) -> str | None:
    cur = await db().execute(
        "SELECT MAX(drawn_at) AS d FROM games WHERE team_id=?", (team_id,)
    )
    row = await cur.fetchone()
    return row["d"] if row else None


async def game_players_list(game_id: int) -> list[Row]:
    cur = await db().execute(
        "SELECT p.user_id, u.username, f.full_name "
        "FROM game_players p "
        "JOIN users u ON u.id=p.user_id "
        "LEFT JOIN forms f ON f.game_id=p.game_id AND f.user_id=p.user_id "
        "WHERE p.game_id=? ORDER BY p.joined_at",
        (game_id,),
    )
    return list(await cur.fetchall())


async def remove_player(game_id: int, user_id: int) -> None:
    await db().execute(
        "DELETE FROM game_players WHERE game_id=? AND user_id=?", (game_id, user_id)
    )
    await db().execute(
        "DELETE FROM forms WHERE game_id=? AND user_id=?", (game_id, user_id)
    )
    await db().commit()


# ------------------------------------------------------------------ forms

async def get_form(game_id: int, user_id: int) -> Row | None:
    cur = await db().execute(
        "SELECT * FROM forms WHERE game_id=? AND user_id=?", (game_id, user_id)
    )
    return await cur.fetchone()


async def latest_form(user_id: int) -> Row | None:
    cur = await db().execute(
        "SELECT * FROM forms WHERE user_id=? ORDER BY updated_at DESC LIMIT 1",
        (user_id,),
    )
    return await cur.fetchone()


async def upsert_form(game_id: int, user_id: int, data: dict[str, Any]) -> None:
    ts = now()
    await db().execute(
        "INSERT INTO forms (game_id, user_id, full_name, phone, address, allergies, wishes, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(game_id, user_id) DO UPDATE SET full_name=excluded.full_name, "
        "phone=excluded.phone, address=excluded.address, allergies=excluded.allergies, "
        "wishes=excluded.wishes, updated_at=excluded.updated_at",
        (
            game_id, user_id, data["full_name"], data["phone"], data["address"],
            data["allergies"], data["wishes"], ts, ts,
        ),
    )
    await db().commit()


async def users_without_form(game_id: int) -> list[Row]:
    cur = await db().execute(
        "SELECT p.user_id, u.username FROM game_players p JOIN users u ON u.id=p.user_id "
        "WHERE p.game_id=? AND NOT EXISTS "
        "(SELECT 1 FROM forms f WHERE f.game_id=p.game_id AND f.user_id=p.user_id)",
        (game_id,),
    )
    return list(await cur.fetchall())


async def open_games_of_user(user_id: int) -> list[Row]:
    """Ігри з відкритою реєстрацією, де користувач — гравець (для анкети)."""
    cur = await db().execute(
        "SELECT g.id AS game_id, t.id AS team_id, t.name AS team_name "
        "FROM game_players p JOIN games g ON g.id=p.game_id JOIN teams t ON t.id=g.team_id "
        "WHERE p.user_id=? AND g.status='registration' ORDER BY g.created_at DESC",
        (user_id,),
    )
    return list(await cur.fetchall())


async def drawn_player_games_of_user(user_id: int) -> list[Row]:
    cur = await db().execute(
        "SELECT g.id AS game_id, t.name AS team_name "
        "FROM game_players p JOIN games g ON g.id=p.game_id JOIN teams t ON t.id=g.team_id "
        "WHERE p.user_id=? AND g.status='drawn'",
        (user_id,),
    )
    return list(await cur.fetchall())


# ------------------------------------------------------------------ pairs / draw

async def commit_draw(game_id: int, pairs: list[tuple[int, int]]) -> None:
    """Одна транзакція: пари + статус drawn. Якщо впадемо після — доставка відновлювана."""
    await db().execute("BEGIN")
    try:
        await db().executemany(
            "INSERT INTO pairs (game_id, giver_id, receiver_id) VALUES (?, ?, ?)",
            [(game_id, g, r) for g, r in pairs],
        )
        await db().execute(
            "UPDATE games SET status='drawn', drawn_at=? WHERE id=?", (now(), game_id)
        )
        await db().commit()
    except Exception:
        await db().rollback()
        raise


async def pairs_of_game(game_id: int) -> list[Row]:
    cur = await db().execute(
        "SELECT * FROM pairs WHERE game_id=? ORDER BY giver_id", (game_id,)
    )
    return list(await cur.fetchall())


async def undelivered_pairs(game_id: int) -> list[Row]:
    cur = await db().execute(
        "SELECT * FROM pairs WHERE game_id=? AND delivered_at IS NULL", (game_id,)
    )
    return list(await cur.fetchall())


async def mark_delivered(game_id: int, giver_id: int) -> None:
    await db().execute(
        "UPDATE pairs SET delivered_at=?, delivery_error=NULL WHERE game_id=? AND giver_id=?",
        (now(), game_id, giver_id),
    )
    await db().commit()


async def mark_delivery_error(game_id: int, giver_id: int, error: str) -> None:
    await db().execute(
        "UPDATE pairs SET delivery_error=? WHERE game_id=? AND giver_id=?",
        (error[:200], game_id, giver_id),
    )
    await db().commit()


async def reset_draw(game_id: int) -> None:
    await db().execute("BEGIN")
    try:
        await db().execute("DELETE FROM pairs WHERE game_id=?", (game_id,))
        await db().execute(
            "UPDATE games SET status='registration', drawn_at=NULL WHERE id=?",
            (game_id,),
        )
        await db().commit()
    except Exception:
        await db().rollback()
        raise


async def drawn_games_of_user(user_id: int) -> list[Row]:
    """Для /myreceiver: усі drawn-ігри, де користувач дарує."""
    cur = await db().execute(
        "SELECT g.id AS game_id, t.name AS team_name, p.receiver_id "
        "FROM pairs p JOIN games g ON g.id=p.game_id JOIN teams t ON t.id=g.team_id "
        "WHERE p.giver_id=? AND g.status='drawn'",
        (user_id,),
    )
    return list(await cur.fetchall())


# ------------------------------------------------------------------ finish / archive / retention

async def finish_game(game_id: int) -> None:
    await db().execute(
        "UPDATE games SET status='finished', finished_at=? WHERE id=?", (now(), game_id)
    )
    await db().commit()


async def archive_and_purge_temp_team(team_id: int, game_id: int) -> None:
    """Одноразова команда: анкети → архів (ретенція), самі анкети видаляються."""
    team = await get_team(team_id)
    await db().execute("BEGIN")
    try:
        await db().execute(
            "INSERT INTO forms_archive (team_id, team_name, game_id, owner_id, user_id, "
            "full_name, phone, address, allergies, wishes, archived_at) "
            "SELECT ?, ?, f.game_id, ?, f.user_id, "
            "f.full_name, f.phone, f.address, f.allergies, f.wishes, ? "
            "FROM forms f WHERE f.game_id=?",
            (team_id, team["name"], team["owner_id"], now(), game_id),
        )
        await db().execute("DELETE FROM forms WHERE game_id=?", (game_id,))
        await db().execute("UPDATE teams SET is_archived=1 WHERE id=?", (team_id,))
        await db().commit()
    except Exception:
        await db().rollback()
        raise


def _threshold(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


async def archive_games(owner_id: int | None, days: int) -> list[Row]:
    """Заархівовані ігри, доступні цьому власнику (owner_id=None → всі, для адміна)."""
    sql = (
        "SELECT game_id, team_name, owner_id, MIN(archived_at) AS archived_at, "
        "COUNT(*) AS n FROM forms_archive WHERE archived_at>=?"
    )
    params: list = [_threshold(days)]
    if owner_id is not None:
        sql += " AND owner_id=?"
        params.append(owner_id)
    sql += " GROUP BY game_id, team_name, owner_id ORDER BY archived_at DESC LIMIT 20"
    cur = await db().execute(sql, params)
    return list(await cur.fetchall())


async def archive_forms(game_id: int, owner_id: int | None, days: int) -> list[Row]:
    sql = "SELECT * FROM forms_archive WHERE game_id=? AND archived_at>=?"
    params: list = [game_id, _threshold(days)]
    if owner_id is not None:
        sql += " AND owner_id=?"
        params.append(owner_id)
    cur = await db().execute(sql + " ORDER BY full_name", params)
    return list(await cur.fetchall())


async def purge_archive(days: int = 365) -> int:
    threshold = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    cur = await db().execute(
        "DELETE FROM forms_archive WHERE archived_at<?", (threshold,)
    )
    await db().commit()
    return cur.rowcount


# ------------------------------------------------------------------ reports / roles

async def create_report(
    reporter_id: int,
    reported_user_id: int,
    team_id: int | None,
    reason: str,
    report_type: str = "user",
) -> int:
    cur = await db().execute(
        "INSERT INTO reports (reporter_id, reported_user_id, team_id, type, reason, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (reporter_id, reported_user_id, team_id, report_type, reason, now()),
    )
    await db().commit()
    return cur.lastrowid


async def reports_list(bucket: str, kind: str | None = None, limit: int = 10) -> list[Row]:
    """bucket: 'open' — нові, 'work' — в роботі, 'done' — закриті (архів).

    kind: None — всі, 'user' — скарги на людей, 'feedback' — bug + idea.
    """
    where = {
        "open": "r.status='open'",
        "work": "r.status='in_progress'",
        "done": "r.status IN ('banned','dismissed','closed')",
    }[bucket]
    if kind == "user":
        where += " AND r.type='user'"
    elif kind == "feedback":
        where += " AND r.type IN ('bug','idea')"
    # архів — свіжі згори; черга — старіші першими, щоб ніхто не висів вічно
    order = "r.resolved_at DESC" if bucket == "done" else "r.created_at"
    cur = await db().execute(
        "SELECT r.*, u.username AS reported_username, ru.username AS reporter_username "
        "FROM reports r "
        "JOIN users u ON u.id=r.reported_user_id "
        "JOIN users ru ON ru.id=r.reporter_id "
        f"WHERE {where} ORDER BY {order} LIMIT ?",
        (limit,),
    )
    return list(await cur.fetchall())


async def take_report(report_id: int, admin_id: int) -> bool:
    """Атомарно взяти в роботу. False — вже взято чи закрито."""
    cur = await db().execute(
        "UPDATE reports SET status='in_progress', taken_by=?, taken_at=? "
        "WHERE id=? AND status='open'",
        (admin_id, now(), report_id),
    )
    await db().commit()
    return cur.rowcount == 1


async def get_report(report_id: int) -> Row | None:
    cur = await db().execute(
        "SELECT r.*, u.username AS reported_username, ru.username AS reporter_username "
        "FROM reports r "
        "JOIN users u ON u.id=r.reported_user_id "
        "JOIN users ru ON ru.id=r.reporter_id "
        "WHERE r.id=?",
        (report_id,),
    )
    return await cur.fetchone()


async def resolve_report(report_id: int, status: str) -> bool:
    """Атомарно: True лише для того адміна, який закрив скаргу першим."""
    cur = await db().execute(
        "UPDATE reports SET status=?, resolved_at=? "
        "WHERE id=? AND status IN ('open','in_progress')",
        (status, now(), report_id),
    )
    await db().commit()
    return cur.rowcount == 1


async def create_role_request(user_id: int) -> bool:
    """False, якщо заявка вже висить."""
    cur = await db().execute(
        "SELECT 1 FROM role_requests WHERE user_id=? AND status='pending'", (user_id,)
    )
    if await cur.fetchone():
        return False
    await db().execute(
        "INSERT INTO role_requests (user_id, created_at) VALUES (?, ?)",
        (user_id, now()),
    )
    await db().commit()
    return True


async def pending_role_requests() -> list[Row]:
    cur = await db().execute(
        "SELECT r.*, u.username FROM role_requests r JOIN users u ON u.id=r.user_id "
        "WHERE r.status='pending' ORDER BY r.created_at"
    )
    return list(await cur.fetchall())


async def decide_role_request(request_id: int, approved: bool) -> Row | None:
    """Атомарно: заявку вирішує лише перший адмін, решта отримують None."""
    cur = await db().execute(
        "UPDATE role_requests SET status=?, decided_at=? WHERE id=? AND status='pending'",
        ("approved" if approved else "declined", now(), request_id),
    )
    if cur.rowcount != 1:
        await db().commit()
        return None
    cur = await db().execute("SELECT * FROM role_requests WHERE id=?", (request_id,))
    req = await cur.fetchone()
    if approved:
        await db().execute(
            "UPDATE users SET role='kerivnyk' WHERE id=? AND role='user'",
            (req["user_id"],),
        )
    await db().commit()
    return req


async def delete_user_data(user_id: int) -> dict[str, int]:
    """Точкове видалення персональних даних на запит людини (право на забуття).

    Стирає анкети в усіх іграх і архівні копії. Пари лишаються (там лише id,
    без ПІБ/телефонів). Повертає кількість видаленого для квитанції.
    """
    cur = await db().execute("DELETE FROM forms WHERE user_id=?", (user_id,))
    forms_deleted = cur.rowcount
    cur = await db().execute("DELETE FROM forms_archive WHERE user_id=?", (user_id,))
    archive_deleted = cur.rowcount
    await db().commit()
    return {"forms": forms_deleted, "archive": archive_deleted}


# ------------------------------------------------------------------ stats

async def stats() -> dict[str, int]:
    result = {}
    for key, sql in {
        "users": "SELECT COUNT(*) AS n FROM users",
        "teams": "SELECT COUNT(*) AS n FROM teams WHERE is_archived=0",
        "active_games": "SELECT COUNT(*) AS n FROM games WHERE status IN ('registration','drawn')",
        "finished_games": "SELECT COUNT(*) AS n FROM games WHERE status='finished'",
        "forms": "SELECT COUNT(*) AS n FROM forms",
        "open_reports": "SELECT COUNT(*) AS n FROM reports WHERE status IN ('open','in_progress')",
        "pending_roles": "SELECT COUNT(*) AS n FROM role_requests WHERE status='pending'",
        "undelivered": "SELECT COUNT(*) AS n FROM pairs p JOIN games g ON g.id=p.game_id "
                       "WHERE p.delivered_at IS NULL AND g.status='drawn'",
        "banned": "SELECT COUNT(*) AS n FROM users WHERE is_banned=1",
    }.items():
        cur = await db().execute(sql)
        result[key] = (await cur.fetchone())["n"]
    return result
