"""Робота з ISO-мітками часу, які зберігає БД."""

from datetime import datetime, timedelta, timezone

FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def parse(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.strptime(iso, FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def days_since(iso: str | None) -> int | None:
    then = parse(iso)
    return None if then is None else (datetime.now(timezone.utc) - then).days


def expires(iso: str | None, window_days: int) -> tuple[int, str] | None:
    """(скільки днів лишилось, дата видалення ДД.ММ.РРРР) для запису з міткою iso."""
    then = parse(iso)
    if then is None:
        return None
    deadline = then + timedelta(days=window_days)
    left = (deadline - datetime.now(timezone.utc)).days
    return max(left, 0), deadline.strftime("%d.%m.%Y")
