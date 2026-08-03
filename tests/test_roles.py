"""Керування ролями: матриця дозволів _apply_role."""

import asyncio
import os
from unittest.mock import AsyncMock

# app.config читає оточення при імпорті — задаємо до імпорту роутера
os.environ.setdefault("BOT_TOKEN", "42:TEST")
os.environ.setdefault("ADMIN_ID", "999")

from app import runtime  # noqa: E402
from app.db import repo  # noqa: E402
from app.routers import admin as admin_router  # noqa: E402

MAIN = admin_router.ADMIN_ID


def apply_role(actor_id, user, new_role, monkeypatch):
    set_role = AsyncMock()
    monkeypatch.setattr(repo, "set_role", set_role)
    result = asyncio.run(admin_router._apply_role(actor_id, user, new_role, AsyncMock()))
    return result, set_role


def test_main_admin_role_is_untouchable(monkeypatch):
    result, set_role = apply_role(MAIN, {"id": MAIN, "role": "user"}, "admin", monkeypatch)
    assert "не можна" in result
    set_role.assert_not_awaited()


def test_regular_admin_cannot_grant_admin(monkeypatch):
    result, set_role = apply_role(5, {"id": 1, "role": "user"}, "admin", monkeypatch)
    assert "головний" in result
    set_role.assert_not_awaited()


def test_regular_admin_cannot_demote_admin(monkeypatch):
    result, set_role = apply_role(5, {"id": 1, "role": "admin"}, "user", monkeypatch)
    assert "головний" in result
    set_role.assert_not_awaited()


def test_any_admin_can_remove_kerivnyk(monkeypatch):
    result, set_role = apply_role(5, {"id": 1, "role": "kerivnyk"}, "user", monkeypatch)
    assert result.startswith("Готово")
    set_role.assert_awaited_once_with(1, "user")


def test_any_admin_can_grant_kerivnyk(monkeypatch):
    result, set_role = apply_role(5, {"id": 1, "role": "user"}, "kerivnyk", monkeypatch)
    assert result.startswith("Готово")
    set_role.assert_awaited_once_with(1, "kerivnyk")


def test_main_admin_grants_and_revokes_admin(monkeypatch):
    result, _ = apply_role(MAIN, {"id": 1, "role": "user"}, "admin", monkeypatch)
    assert result.startswith("Готово")
    # анти-флуд одразу знає про нового адміна
    assert 1 in runtime.throttling_middleware._admin_ids
    result, _ = apply_role(MAIN, {"id": 1, "role": "admin"}, "user", monkeypatch)
    assert result.startswith("Готово")
    assert 1 not in runtime.throttling_middleware._admin_ids


def test_same_role_is_noop(monkeypatch):
    result, set_role = apply_role(5, {"id": 1, "role": "kerivnyk"}, "kerivnyk", monkeypatch)
    set_role.assert_not_awaited()
    assert "вже" in result
