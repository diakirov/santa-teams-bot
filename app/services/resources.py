"""Нагляд за ресурсами контейнера: cgroup v2, диск, пороги сигналів адміну.

Парсери і порогова логіка чисті (без файлової системи) — їх покривають тести.
Реальні читання зібрані в snapshot()/read_*, їх викликає фонова задача.
"""

import logging
import os
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

CPU_STAT = Path("/sys/fs/cgroup/cpu.stat")
MEMORY_CURRENT = Path("/sys/fs/cgroup/memory.current")
MEMORY_MAX = Path("/sys/fs/cgroup/memory.max")
MEMORY_EVENTS = Path("/sys/fs/cgroup/memory.events")

# пороги (видимі головному адміну в /health)
THROTTLE_ALERT_USEC = 30_000_000   # 30 с троттлінгу за 5-хвилинне вікно
THROTTLE_STRIKES = 2               # скільки вікон поспіль, щоб сигналити
MEM_ALERT_RATIO = 0.8              # 80% ліміту памʼяті
MEM_STRIKES = 2                    # «стабільно», а не разовий пік
DISK_FREE_MIN_BYTES = 5 * 1024**3  # менше 5 ГБ вільного — сигнал
DB_MAX_BYTES = 10 * 1024**3        # база більша за 10 ГБ — сигнал

GB = 1024**3
MB = 1024**2


def parse_kv(text: str) -> dict[str, int]:
    """cpu.stat і memory.events: рядки виду `ключ число`."""
    result: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("-").isdigit():
            result[parts[0]] = int(parts[1])
    return result


def parse_scalar(text: str) -> int | None:
    """memory.current / memory.max: одне число; `max` = без ліміту."""
    value = text.strip()
    if not value or value == "max":
        return None
    return int(value) if value.isdigit() else None


class ResourceWatch:
    """Тримає попередні виміри й вирішує, коли справді час сигналити.

    Сигнал — це системність (троттлінг двічі поспіль, памʼять стабільно
    вище порога), а не разова висока цифра.
    """

    def __init__(self) -> None:
        self.prev_throttled_usec: int | None = None
        self.throttle_strikes = 0
        self.mem_strikes = 0
        self.prev_oom_kills: int | None = None

    def check_cpu(self, throttled_usec: int) -> str | None:
        prev, self.prev_throttled_usec = self.prev_throttled_usec, throttled_usec
        if prev is None:
            return None
        delta = throttled_usec - prev
        if delta > THROTTLE_ALERT_USEC:
            self.throttle_strikes += 1
        else:
            self.throttle_strikes = 0
            return None
        if self.throttle_strikes >= THROTTLE_STRIKES:
            return (
                "🐌 CPU: бот системно впирається в ліміт — "
                f"троттлінг {delta / 1_000_000:.0f} с за останні 5 хв "
                f"({self.throttle_strikes} вікон поспіль). "
                "Якщо це не разовий сплеск, варто підняти cpus у compose."
            )
        return None

    def check_memory(self, current: int, limit: int | None) -> str | None:
        if not limit:
            return None
        if current / limit > MEM_ALERT_RATIO:
            self.mem_strikes += 1
        else:
            self.mem_strikes = 0
            return None
        if self.mem_strikes >= MEM_STRIKES:
            return (
                f"📈 Памʼять: стабільно {current / MB:.0f} МБ із {limit / MB:.0f} МБ "
                f"(понад {MEM_ALERT_RATIO:.0%} ліміту). Близько до OOM — "
                "перевір, що відбувається, або підніми mem_limit."
            )
        return None

    def check_oom(self, oom_kills: int) -> str | None:
        prev, self.prev_oom_kills = self.prev_oom_kills, oom_kills
        if prev is not None and oom_kills > prev:
            return (
                f"💥 OOM: контейнер убивав процес через памʼять ({oom_kills - prev} раз). "
                "Бот, найімовірніше, тихо перезапустився — перевір логи."
            )
        return None

    @staticmethod
    def check_disk(free_bytes: int, db_bytes: int) -> str | None:
        if free_bytes < DISK_FREE_MIN_BYTES:
            return (
                f"💾 Диск: вільно лише {free_bytes / GB:.1f} ГБ "
                f"(поріг {DISK_FREE_MIN_BYTES / GB:.0f} ГБ). "
                "Найімовірніші винуватці — образи Docker і логи."
            )
        if db_bytes > DB_MAX_BYTES:
            return (
                f"💾 База: {db_bytes / GB:.1f} ГБ — більша за поріг "
                f"{DB_MAX_BYTES / GB:.0f} ГБ. Це підозріло для цього бота."
            )
        return None


def cgroup_available() -> bool:
    return CPU_STAT.exists() and MEMORY_CURRENT.exists()


def read_cpu_stat() -> dict[str, int]:
    return parse_kv(CPU_STAT.read_text())


def read_memory() -> tuple[int | None, int | None]:
    current = parse_scalar(MEMORY_CURRENT.read_text())
    limit = parse_scalar(MEMORY_MAX.read_text()) if MEMORY_MAX.exists() else None
    return current, limit


def read_oom_kills() -> int | None:
    if not MEMORY_EVENTS.exists():
        return None
    return parse_kv(MEMORY_EVENTS.read_text()).get("oom_kill")


def disk_and_db(db_path: str) -> tuple[int, int]:
    free = shutil.disk_usage(os.path.dirname(os.path.abspath(db_path)) or "/").free
    try:
        db_bytes = os.path.getsize(db_path)
    except OSError:
        db_bytes = 0
    return free, db_bytes


def health_block(db_path: str) -> str:
    """Блок ресурсів для /health — лише головному адміну."""
    free, db_bytes = disk_and_db(db_path)
    lines = ["⚙️ Ресурси (пороги сигналів у дужках)"]
    if cgroup_available():
        current, limit = read_memory()
        if current is not None:
            limit_text = f"{limit / MB:.0f} МБ" if limit else "без ліміту"
            lines.append(
                f"Памʼять: {current / MB:.0f} МБ із {limit_text} "
                f"(сигнал: стабільно > {MEM_ALERT_RATIO:.0%})"
            )
        throttled = read_cpu_stat().get("throttled_usec")
        if throttled is not None:
            lines.append(
                f"CPU: троттлінг сумарно {throttled / 1_000_000:.0f} с від старту "
                f"(сигнал: > {THROTTLE_ALERT_USEC / 1_000_000:.0f} с за 5 хв, "
                f"{THROTTLE_STRIKES} вікна поспіль)"
            )
        oom = read_oom_kills()
        if oom is not None:
            lines.append(f"OOM-kill від старту: {oom} (сигнал: будь-який новий)")
    else:
        lines.append("cgroup недоступний (запуск без Docker) — стежу лише за диском")
    lines.append(
        f"Диск: вільно {free / GB:.1f} ГБ (сигнал: < {DISK_FREE_MIN_BYTES / GB:.0f} ГБ), "
        f"база {db_bytes / MB:.1f} МБ (сигнал: > {DB_MAX_BYTES / GB:.0f} ГБ)"
    )
    return "\n".join(lines)
