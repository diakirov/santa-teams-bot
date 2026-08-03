"""З'єднання з SQLite, ініціалізація схеми, бекап."""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)

_db: aiosqlite.Connection | None = None

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def connect(db_path: str) -> aiosqlite.Connection:
    global _db
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    _db = await aiosqlite.connect(db_path)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA foreign_keys=ON")
    await _db.execute("PRAGMA busy_timeout=5000")
    await _db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    await _migrate(_db)
    await _db.commit()
    log.info("База даних готова: %s", db_path)
    return _db


async def _migrate(conn: aiosqlite.Connection) -> None:
    """Догоняючі міграції для БД, створених старішою схемою."""
    cur = await conn.execute("PRAGMA table_info(users)")
    columns = {row["name"] for row in await cur.fetchall()}
    if "banned_by" not in columns:
        await conn.execute("ALTER TABLE users ADD COLUMN banned_by INTEGER")
        log.info("Міграція: додано users.banned_by")
    if "banned_at" not in columns:
        await conn.execute("ALTER TABLE users ADD COLUMN banned_at TEXT")
        log.info("Міграція: додано users.banned_at")
    cur = await conn.execute("PRAGMA table_info(reports)")
    report_columns = {row["name"] for row in await cur.fetchall()}
    if "type" not in report_columns:
        await conn.execute(
            "ALTER TABLE reports ADD COLUMN type TEXT NOT NULL DEFAULT 'user'"
        )
        log.info("Міграція: додано reports.type")
    # нові статуси в CHECK: SQLite не вміє ALTER CHECK — перебудовуємо таблицю
    cur = await conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='reports'"
    )
    row = await cur.fetchone()
    if row and "in_progress" not in row["sql"]:
        log.info("Міграція: перебудова reports під статуси in_progress/closed")
        await conn.execute("ALTER TABLE reports RENAME TO reports_old")
        await conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        shared = "id, reporter_id, reported_user_id, team_id, type, reason, status, created_at, resolved_at"
        await conn.execute(
            f"INSERT INTO reports ({shared}) SELECT {shared} FROM reports_old"
        )
        await conn.execute("DROP TABLE reports_old")
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_reports_status ON reports(status)"
        )


def db() -> aiosqlite.Connection:
    if _db is None:
        raise RuntimeError("База даних не ініціалізована")
    return _db


async def close() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None


async def backup(db_path: str, keep: int = 14) -> str | None:
    """Щоденний знімок: VACUUM INTO data/backups/, тримаємо останні `keep` файлів."""
    backups_dir = Path(os.path.dirname(os.path.abspath(db_path))) / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    target = backups_dir / f"santa-{stamp}.db"
    if target.exists():
        return None
    await db().execute("VACUUM INTO ?", (str(target),))
    old = sorted(backups_dir.glob("santa-*.db"))[:-keep]
    for f in old:
        f.unlink(missing_ok=True)
    log.info("Бекап створено: %s", target.name)
    return str(target)
