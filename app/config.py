"""Конфігурація з оточення. Падає на старті, якщо бракує обов'язкового."""

import os
import sys

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        print(f"Помилка: змінна оточення {name} не задана. Дивись .env.example", file=sys.stderr)
        raise SystemExit(1)
    return value


BOT_TOKEN = _required("BOT_TOKEN")

try:
    ADMIN_ID = int(_required("ADMIN_ID"))
except ValueError:
    print("Помилка: ADMIN_ID має бути числом (Telegram ID)", file=sys.stderr)
    raise SystemExit(1)

DB_PATH = os.getenv("DB_PATH", "./data/santa.db").strip()
HEALTHCHECK_URL = os.getenv("HEALTHCHECK_URL", "").strip()
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()
