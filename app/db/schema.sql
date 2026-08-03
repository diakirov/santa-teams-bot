PRAGMA user_version = 1;

CREATE TABLE IF NOT EXISTS users (
    id                   INTEGER PRIMARY KEY,           -- telegram_id
    username             TEXT,
    role                 TEXT NOT NULL DEFAULT 'user'
                         CHECK (role IN ('user', 'kerivnyk', 'admin')),
    is_banned            INTEGER NOT NULL DEFAULT 0,
    ban_reason           TEXT,
    banned_by            INTEGER,                       -- хто наклав бан (для правила «бан головного знімає лише головний»)
    banned_at            TEXT,
    max_teams_override   INTEGER,
    max_members_override INTEGER,
    first_seen_at        TEXT NOT NULL,
    last_seen_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS teams (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id              INTEGER NOT NULL REFERENCES users(id),
    name                  TEXT NOT NULL,
    invite_code           TEXT NOT NULL UNIQUE,
    is_temporary          INTEGER NOT NULL DEFAULT 0,
    is_archived           INTEGER NOT NULL DEFAULT 0,
    member_limit_override INTEGER,
    created_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_teams_owner ON teams(owner_id);

CREATE TABLE IF NOT EXISTS team_members (
    team_id    INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    is_blocked INTEGER NOT NULL DEFAULT 0,
    added_by   INTEGER,                                 -- NULL = сам за кодом
    joined_at  TEXT NOT NULL,
    PRIMARY KEY (team_id, user_id)
);
CREATE INDEX IF NOT EXISTS ix_members_user ON team_members(user_id);

CREATE TABLE IF NOT EXISTS games (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id     INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'registration'
                CHECK (status IN ('registration', 'drawn', 'finished', 'cancelled')),
    created_at  TEXT NOT NULL,
    drawn_at    TEXT,
    finished_at TEXT
);
-- одна активна гра на команду; стан живе в БД і переживає рестарт
CREATE UNIQUE INDEX IF NOT EXISTS ux_games_active
    ON games(team_id) WHERE status IN ('registration', 'drawn');

CREATE TABLE IF NOT EXISTS game_players (
    game_id   INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    user_id   INTEGER NOT NULL REFERENCES users(id),
    joined_at TEXT NOT NULL,
    PRIMARY KEY (game_id, user_id)
);
CREATE INDEX IF NOT EXISTS ix_players_user ON game_players(user_id);

CREATE TABLE IF NOT EXISTS forms (
    game_id    INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    full_name  TEXT NOT NULL,
    phone      TEXT NOT NULL,
    address    TEXT NOT NULL,
    allergies  TEXT NOT NULL,
    wishes     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (game_id, user_id)
);
CREATE INDEX IF NOT EXISTS ix_forms_user ON forms(user_id);

-- архів анкет одноразових команд: учасникам недоступний одразу,
-- власнику команди видно 14 днів (керівнику 30), адміну — рік, далі purge
CREATE TABLE IF NOT EXISTS forms_archive (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id     INTEGER NOT NULL,
    team_name   TEXT NOT NULL,
    game_id     INTEGER NOT NULL,
    owner_id    INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    full_name   TEXT NOT NULL,
    phone       TEXT NOT NULL,
    address     TEXT NOT NULL,
    allergies   TEXT NOT NULL,
    wishes      TEXT NOT NULL,
    archived_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_archive_owner ON forms_archive(owner_id);

CREATE TABLE IF NOT EXISTS pairs (
    game_id        INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    giver_id       INTEGER NOT NULL,
    receiver_id    INTEGER NOT NULL,
    delivered_at   TEXT,
    delivery_error TEXT,
    PRIMARY KEY (game_id, giver_id),
    UNIQUE (game_id, receiver_id)
);

CREATE TABLE IF NOT EXISTS reports (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_id      INTEGER NOT NULL REFERENCES users(id),
    reported_user_id INTEGER NOT NULL REFERENCES users(id),  -- для фідбеку = reporter_id
    team_id          INTEGER REFERENCES teams(id) ON DELETE SET NULL,
    type             TEXT NOT NULL DEFAULT 'user'
                     CHECK (type IN ('user', 'bug', 'idea')),
    reason           TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'open'
                     CHECK (status IN ('open', 'in_progress', 'banned', 'dismissed', 'closed')),
    taken_by         INTEGER,                                -- який адмін узяв у роботу
    taken_at         TEXT,
    -- міні-діалог автор ↔ адмін через бота: реплаї й адресація відповіді
    author_msg_id    INTEGER,                                -- останнє повідомлення автора (для цитати йому)
    admin_msg_id     INTEGER,                                -- останнє повідомлення адміна (для цитати адміну)
    last_admin_id    INTEGER,                                -- кому з адмінів летить відповідь автора
    created_at       TEXT NOT NULL,
    resolved_at      TEXT
);
CREATE INDEX IF NOT EXISTS ix_reports_status ON reports(status);

CREATE TABLE IF NOT EXISTS role_requests (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    status     TEXT NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending', 'approved', 'declined')),
    created_at TEXT NOT NULL,
    decided_at TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR IGNORE INTO settings (key, value) VALUES
    ('limit.user.max_teams', '5'),
    ('limit.user.max_members', '50'),
    ('limit.kerivnyk.max_teams', '10'),
    ('limit.kerivnyk.max_members', '100'),
    ('registration_open', '1');

CREATE TABLE IF NOT EXISTS fsm_state (
    key        TEXT PRIMARY KEY,
    state      TEXT,
    data       TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);
