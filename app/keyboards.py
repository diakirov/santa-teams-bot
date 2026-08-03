"""Клавіатури і фабрики callback-даних."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

# кнопки головного меню (reply)
BTN_MY_TEAMS = "🎄 Мої команди"
BTN_MY_FORM = "📝 Моя анкета"
BTN_MY_RECEIVER = "🎁 Мій отримувач"
BTN_ENTER_CODE = "🔑 Ввести код"
BTN_CREATE_TEAM = "➕ Створити команду"
BTN_HELP = "❓ Допомога"

MENU_BUTTONS = {
    BTN_MY_TEAMS, BTN_MY_FORM, BTN_MY_RECEIVER,
    BTN_ENTER_CODE, BTN_CREATE_TEAM, BTN_HELP,
}


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_MY_TEAMS), KeyboardButton(text=BTN_MY_FORM)],
            [KeyboardButton(text=BTN_MY_RECEIVER), KeyboardButton(text=BTN_ENTER_CODE)],
            [KeyboardButton(text=BTN_CREATE_TEAM), KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
    )


class TeamCb(CallbackData, prefix="t"):
    """Дії з командою. Права перевіряються в хендлері, не тут."""
    act: str
    team_id: int


class MemberCb(CallbackData, prefix="m"):
    act: str
    team_id: int
    user_id: int


class AdminCb(CallbackData, prefix="a"):
    act: str
    arg: int = 0


class FormCb(CallbackData, prefix="f"):
    act: str
    game_id: int = 0


class ArchiveCb(CallbackData, prefix="ar"):
    game_id: int


class FeedbackCb(CallbackData, prefix="fb"):
    kind: str  # bug | idea


class RepListCb(CallbackData, prefix="rl"):
    """Фільтри черги скарг/фідбеку."""
    bucket: str  # open | work | done
    kind: str    # all | user | fb


def _btn(text: str, cb: CallbackData) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=cb.pack())


def _kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


# ------------------------------------------------------------------ команди

def teams_list(teams_own, teams_member) -> InlineKeyboardMarkup:
    rows = []
    for t in teams_own:
        rows.append([_btn(f"👑 {t['name']}", TeamCb(act="card", team_id=t["id"]))])
    for t in teams_member:
        rows.append([_btn(t["name"], TeamCb(act="mcard", team_id=t["id"]))])
    return _kb(*rows)


def team_card_kb(team_id: int, status: str | None) -> InlineKeyboardMarkup:
    """Картка команди для власника."""
    rows = [
        [
            _btn("📨 Запросити", TeamCb(act="invite", team_id=team_id)),
            _btn("👥 Учасники", TeamCb(act="members", team_id=team_id)),
        ],
        [
            _btn("📋 Хто без анкети", TeamCb(act="noform", team_id=team_id)),
            _btn("🔔 Нагадати", TeamCb(act="remind", team_id=team_id)),
        ],
    ]
    if status == "registration":
        rows.append([_btn("🎲 Жеребкування", TeamCb(act="draw", team_id=team_id))])
    elif status == "drawn":
        rows.append([
            _btn("👀 Показати пари", TeamCb(act="pairs", team_id=team_id)),
            _btn("🔁 Недоставлені", TeamCb(act="redeliver", team_id=team_id)),
        ])
        rows.append([
            _btn("🔄 Скинути гру", TeamCb(act="reset", team_id=team_id)),
            _btn("🏁 Завершити гру", TeamCb(act="finish", team_id=team_id)),
        ])
    elif status is None:
        rows.append([_btn("🆕 Нова гра", TeamCb(act="newgame", team_id=team_id))])
    rows.append([_btn("⚙️ Ще…", TeamCb(act="more", team_id=team_id))])
    return _kb(*rows)


def team_more_kb(team_id: int, has_open_game: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [_btn("➕ Додати вручну", TeamCb(act="add", team_id=team_id))],
        [_btn("➖ Видалити учасника", TeamCb(act="del", team_id=team_id))],
    ]
    if has_open_game:
        rows.append([_btn("⏸ Пропустити цю гру", TeamCb(act="skip", team_id=team_id))])
    rows += [
        [_btn("🚫 Заблокувати в команді", TeamCb(act="block", team_id=team_id))],
        [_btn("⚠️ Поскаржитись адміну", TeamCb(act="report", team_id=team_id))],
        [_btn("⬅️ Назад", TeamCb(act="card", team_id=team_id))],
    ]
    return _kb(*rows)


def member_card_kb(team_id: int, in_game: bool, drawn: bool) -> InlineKeyboardMarkup:
    """Картка команди для звичайного учасника."""
    rows = []
    if in_game and not drawn:
        rows.append([_btn("🚪 Вийти з гри", TeamCb(act="leavegame", team_id=team_id))])
    rows.append([_btn("👋 Вийти з команди", TeamCb(act="leaveteam", team_id=team_id))])
    return _kb(*rows)


def pick_member_kb(team_id: int, act: str, members) -> InlineKeyboardMarkup:
    rows = [
        [_btn(
            m["full_name"] or (f"@{m['username']}" if m["username"] else str(m["user_id"])),
            MemberCb(act=act, team_id=team_id, user_id=m["user_id"]),
        )]
        for m in members
    ]
    rows.append([_btn("⬅️ Назад", TeamCb(act="card", team_id=team_id))])
    return _kb(*rows)


def confirm_kb(yes_cb: CallbackData, no_cb: CallbackData, yes_text: str = "✅ Так") -> InlineKeyboardMarkup:
    return _kb([_btn(yes_text, yes_cb), _btn("✖️ Скасувати", no_cb)])


def team_type_kb() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("♻️ Постійна команда", FormCb(act="perm"))],
        [_btn("1️⃣ Одноразова гра", FormCb(act="temp"))],
    )


def temp_confirm_kb() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("✅ Так, одноразова", FormCb(act="temp_yes"))],
        [_btn("⬅️ Ні, постійна", FormCb(act="perm"))],
    )


# ------------------------------------------------------------------ анкета

def form_confirm_kb() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("✅ Все вірно", FormCb(act="save"))],
        [
            _btn("✏️ ПІБ", FormCb(act="fix_full_name")),
            _btn("✏️ Телефон", FormCb(act="fix_phone")),
        ],
        [
            _btn("✏️ Адреса", FormCb(act="fix_address")),
            _btn("✏️ Алергії", FormCb(act="fix_allergies")),
        ],
        [_btn("✏️ Побажання", FormCb(act="fix_wishes"))],
        [_btn("🔄 Почати спочатку", FormCb(act="restart"))],
    )


def form_reuse_kb() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("✅ Так, актуально", FormCb(act="reuse"))],
        [_btn("🔧 Майже — виправлю пару полів", FormCb(act="tweak"))],
        [_btn("✏️ Заповнити заново", FormCb(act="refill"))],
    )


def form_paused_kb() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("▶️ Продовжити анкету", FormCb(act="resume"))],
        [_btn("✖️ Скасувати анкету", FormCb(act="abort"))],
    )


def form_game_pick_kb(games) -> InlineKeyboardMarkup:
    """Якщо користувач у кількох іграх з відкритою реєстрацією — обрати, куди анкета."""
    return _kb(*[
        [_btn(g["team_name"], FormCb(act="pickgame", game_id=g["game_id"]))]
        for g in games
    ])


def mydata_kb(game_id: int) -> InlineKeyboardMarkup:
    return _kb([_btn("✏️ Змінити анкету", FormCb(act="edit", game_id=game_id))])


# ------------------------------------------------------------------ адмінка

def admin_menu_kb(registration_open: bool, is_main_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [_btn("📊 Статистика", AdminCb(act="stats"))],
        [_btn("⚠️ Скарги", AdminCb(act="reports"))],
        [_btn("👑 Запити ролей", AdminCb(act="roles"))],
        [_btn("👥 Люди з ролями", AdminCb(act="people"))],
        [_btn("⚙️ Ліміти", AdminCb(act="limits"))],
    ]
    # вимикач реєстрації — лише головному адміну
    if is_main_admin:
        toggle = "⏸ Закрити реєстрацію" if registration_open else "▶️ Відкрити реєстрацію"
        rows.append([_btn(toggle, AdminCb(act="toggle_reg"))])
    return _kb(*rows)


def people_list_kb(admins, kerivnyky) -> InlineKeyboardMarkup:
    def label(u, badge):
        name = f"@{u['username']}" if u["username"] else f"id {u['id']}"
        return f"{badge} {name}" + (" 🚫" if u["is_banned"] else "")

    rows = [[_btn(label(u, "🛠"), AdminCb(act="person", arg=u["id"]))] for u in admins]
    rows += [[_btn(label(u, "👑"), AdminCb(act="person", arg=u["id"]))] for u in kerivnyky]
    return _kb(*rows)


def person_card_kb(user, viewer_is_main_admin: bool) -> InlineKeyboardMarkup:
    uid = user["id"]
    rows = []
    if user["role"] == "kerivnyk":
        rows.append([_btn("👑 Зняти роль керівника", AdminCb(act="rm_kerivnyk", arg=uid))])
    elif user["role"] != "admin":
        rows.append([_btn("👑 Зробити керівником", AdminCb(act="mk_kerivnyk", arg=uid))])
    if viewer_is_main_admin:
        if user["role"] == "admin":
            rows.append([_btn("🛠 Зняти роль адміністратора", AdminCb(act="rm_admin", arg=uid))])
        else:
            rows.append([_btn("🛠 Зробити адміністратором", AdminCb(act="mk_admin", arg=uid))])
    if user["is_banned"]:
        rows.append([_btn("✅ Розбанити", AdminCb(act="unban_ask", arg=uid))])
    else:
        rows.append([_btn("🚫 Забанити", AdminCb(act="ban_ask", arg=uid))])
    rows.append([_btn("🗑 Видалити дані (як /forget)", AdminCb(act="forget_ask", arg=uid))])
    rows.append([_btn("⬅️ До списку", AdminCb(act="people"))])
    return _kb(*rows)


def feedback_type_kb() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("🐞 Щось не працює", FeedbackCb(kind="bug"))],
        [_btn("💡 Є пропозиція", FeedbackCb(kind="idea"))],
    )


def report_actions_kb(report_id: int, rtype: str, status: str) -> InlineKeyboardMarkup | None:
    """Дії з карткою скарги/фідбеку залежно від типу і статусу. None — без кнопок."""
    rows = []
    if status == "open":
        rows.append([_btn("🛠 Взяти в роботу", AdminCb(act="rep_take", arg=report_id))])
    if status in ("open", "in_progress"):
        # відповідь через бота працює навіть без @username — автор точно запускав бота
        rows.append([_btn("✉️ Написати автору", AdminCb(act="rep_reply", arg=report_id))])
        if rtype == "user":
            # довгі підписи — кожен своїм рядком, інакше Telegram обрізає текст
            rows.append([_btn("🚫 Забанити глобально", AdminCb(act="rep_ban", arg=report_id))])
            rows.append([_btn("✖️ Відхилити скаргу", AdminCb(act="rep_dismiss", arg=report_id))])
        else:
            rows.append([_btn("✔️ Закрити", AdminCb(act="rep_dismiss", arg=report_id))])
    return _kb(*rows) if rows else None


def reports_filter_kb(bucket: str, kind: str) -> InlineKeyboardMarkup:
    def mark(label: str, active: bool) -> str:
        return f"· {label} ·" if active else label

    def b(label: str, value: str) -> InlineKeyboardButton:
        return _btn(mark(label, value == bucket), RepListCb(bucket=value, kind=kind))

    def k(label: str, value: str) -> InlineKeyboardButton:
        return _btn(mark(label, value == kind), RepListCb(bucket=bucket, kind=value))

    return _kb(
        [b("📥 Нові", "open"), b("🛠 В роботі", "work"), b("🗄 Закриті", "done")],
        [k("Усі", "all"), k("👤 Скарги", "user"), k("🐞💡 Фідбек", "fb")],
    )


def role_request_kb(request_id: int) -> InlineKeyboardMarkup:
    return _kb(
        [_btn("✅ Схвалити", AdminCb(act="role_yes", arg=request_id))],
        [_btn("✖️ Відхилити", AdminCb(act="role_no", arg=request_id))],
    )


def archive_list_kb(items) -> InlineKeyboardMarkup:
    """items: (game_id, підпис) — підпис уже містить строк зберігання."""
    return _kb(*[
        [_btn(label, ArchiveCb(game_id=game_id))] for game_id, label in items
    ])


def back_to_card_kb(team_id: int) -> InlineKeyboardMarkup:
    return _kb([_btn("⬅️ Назад до картки", TeamCb(act="card", team_id=team_id))])
