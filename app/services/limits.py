"""Розрахунок ефективних лімітів.

Пріоритет: разовий override команди → постійний override користувача →
глобальний дефолт для ролі з settings. Ліміти перевіряються лише в момент
дії (створення команди / вступ), тому зміна глобальних дефолтів безпечна
для наявних команд: «зайвих» учасників ніхто не видаляє.
"""

UNLIMITED = 10**9

# скільки днів власник команди бачить архів анкет після завершення одноразової гри
RETENTION_DAYS = {"user": 14, "kerivnyk": 30, "admin": 365}


def retention_days(role: str) -> int:
    return RETENTION_DAYS.get(role, RETENTION_DAYS["user"])


def max_teams(role: str, user_override: int | None, defaults: dict[str, int]) -> int:
    if role == "admin":
        return UNLIMITED
    if user_override is not None:
        return user_override
    key = "limit.kerivnyk.max_teams" if role == "kerivnyk" else "limit.user.max_teams"
    return defaults[key]


def max_members(
    owner_role: str,
    team_override: int | None,
    owner_override: int | None,
    defaults: dict[str, int],
) -> int:
    if team_override is not None:
        return team_override
    if owner_role == "admin":
        return UNLIMITED
    if owner_override is not None:
        return owner_override
    key = "limit.kerivnyk.max_members" if owner_role == "kerivnyk" else "limit.user.max_members"
    return defaults[key]
