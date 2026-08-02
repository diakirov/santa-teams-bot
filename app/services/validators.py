"""Валідація і нормалізація введеного користувачами."""

import re

# ліміти довжин полів (символів)
MAX_FULL_NAME = 100
MAX_PHONE_RAW = 32
MAX_ADDRESS = 300
MAX_ALLERGIES = 500
MAX_WISHES = 1000
MAX_TEAM_NAME = 64
MAX_REPORT_REASON = 500

_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_BAD_SCHEME_RE = re.compile(r"\b(?!https?:)[a-z][a-z0-9+.-]{1,20}://", re.IGNORECASE)


def normalize_phone(raw: str) -> tuple[bool, str]:
    """Дістає цифри, приймає лише 380XXXXXXXXX (12 цифр).

    "+38 (067) 123-45-67" → (True, "380671234567").
    """
    if len(raw) > MAX_PHONE_RAW:
        return False, ""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 12 and digits.startswith("380"):
        return True, digits
    return False, digits


def has_url(text: str) -> bool:
    return bool(_URL_RE.search(text))


def has_forbidden_scheme(text: str) -> bool:
    """Посилання дозволені лише http/https — javascript:, tg:, ftp: тощо відхиляємо."""
    return bool(_BAD_SCHEME_RE.search(text))


def format_short_name(full_name: str) -> str:
    """"Петренко Іван Миколайович" → "Петренко І."."""
    parts = full_name.split()
    if not parts:
        return full_name
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[1][0]}."
