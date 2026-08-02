"""Персистентний FSM-storage для aiogram поверх SQLite.

Стан майстрів (анкета, створення команди) переживає рестарт контейнера.
"""

import json
from typing import Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey

from app.db.core import db, now


def _key(key: StorageKey) -> str:
    return f"{key.bot_id}:{key.chat_id}:{key.user_id}"


class SQLiteStorage(BaseStorage):
    async def set_state(self, key: StorageKey, state: State | str | None = None) -> None:
        value = state.state if isinstance(state, State) else state
        if value is None:
            await db().execute(
                "UPDATE fsm_state SET state=NULL, updated_at=? WHERE key=?",
                (now(), _key(key)),
            )
        else:
            await db().execute(
                "INSERT INTO fsm_state (key, state, data, updated_at) VALUES (?, ?, '{}', ?) "
                "ON CONFLICT(key) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at",
                (_key(key), value, now()),
            )
        await db().commit()

    async def get_state(self, key: StorageKey) -> str | None:
        cur = await db().execute("SELECT state FROM fsm_state WHERE key=?", (_key(key),))
        row = await cur.fetchone()
        return row["state"] if row else None

    async def set_data(self, key: StorageKey, data: dict[str, Any]) -> None:
        await db().execute(
            "INSERT INTO fsm_state (key, state, data, updated_at) VALUES (?, NULL, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
            (_key(key), json.dumps(data, ensure_ascii=False), now()),
        )
        await db().commit()

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        cur = await db().execute("SELECT data FROM fsm_state WHERE key=?", (_key(key),))
        row = await cur.fetchone()
        return json.loads(row["data"]) if row else {}

    async def close(self) -> None:
        pass
