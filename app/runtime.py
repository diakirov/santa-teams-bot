"""Спільний стан процесу: час старту, посилання на middleware (для скидання кешів)."""

import time

from app.middlewares import AccessMiddleware, ThrottlingMiddleware

start_time = time.monotonic()
access_middleware = AccessMiddleware()
throttling_middleware = ThrottlingMiddleware()


def uptime_hours() -> float:
    return (time.monotonic() - start_time) / 3600
