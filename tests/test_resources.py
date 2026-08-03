"""Парсери cgroup і порогова логіка сигналів — на текстових зразках, без Docker."""

from app.services.resources import (
    DB_MAX_BYTES,
    DISK_FREE_MIN_BYTES,
    THROTTLE_ALERT_USEC,
    ResourceWatch,
    parse_kv,
    parse_scalar,
)

CPU_STAT_SAMPLE = """usage_usec 8631542
user_usec 5646549
system_usec 2984993
nr_periods 3103
nr_throttled 27
throttled_usec 45000000
"""

MEMORY_EVENTS_SAMPLE = """low 0
high 0
max 14
oom 2
oom_kill 1
"""


def test_parse_kv():
    stat = parse_kv(CPU_STAT_SAMPLE)
    assert stat["throttled_usec"] == 45_000_000
    assert stat["nr_throttled"] == 27
    events = parse_kv(MEMORY_EVENTS_SAMPLE)
    assert events["oom_kill"] == 1


def test_parse_kv_ignores_garbage():
    assert parse_kv("щось дивне\nключ значення\nx 5\n") == {"x": 5}
    assert parse_kv("") == {}


def test_parse_scalar():
    assert parse_scalar("152043520\n") == 152_043_520
    assert parse_scalar("max\n") is None  # без ліміту
    assert parse_scalar("") is None


def test_cpu_alert_needs_two_windows_in_a_row():
    w = ResourceWatch()
    big = THROTTLE_ALERT_USEC + 1_000_000
    assert w.check_cpu(0) is None                    # перше читання — база
    assert w.check_cpu(big) is None                  # перше погане вікно — ще терпимо
    alert = w.check_cpu(2 * big)                     # друге поспіль — сигнал
    assert alert is not None and "CPU" in alert


def test_cpu_alert_resets_on_quiet_window():
    w = ResourceWatch()
    big = THROTTLE_ALERT_USEC + 1_000_000
    w.check_cpu(0)
    w.check_cpu(big)                                 # погане вікно
    assert w.check_cpu(big + 1000) is None           # тихе вікно — скидання
    assert w.check_cpu(2 * big + 1000) is None       # знову лише перше погане


def test_memory_alert_needs_stability():
    w = ResourceWatch()
    limit = 256 * 1024**2
    high = int(limit * 0.9)
    assert w.check_memory(high, limit) is None       # разовий пік — ні
    alert = w.check_memory(high, limit)              # стабільно — сигнал
    assert alert is not None and "Памʼять" in alert
    assert w.check_memory(int(limit * 0.5), limit) is None  # нормалізувалось


def test_memory_without_limit_is_silent():
    w = ResourceWatch()
    assert w.check_memory(10**9, None) is None


def test_oom_alerts_on_new_kill_only():
    w = ResourceWatch()
    assert w.check_oom(1) is None                    # перше читання — база (старі вбивства)
    assert w.check_oom(1) is None
    alert = w.check_oom(2)                           # нове вбивство — одразу сигнал
    assert alert is not None and "OOM" in alert
    assert w.check_oom(2) is None


def test_disk_thresholds():
    ok_free = DISK_FREE_MIN_BYTES * 3
    assert ResourceWatch.check_disk(ok_free, 1024) is None
    assert "Диск" in ResourceWatch.check_disk(DISK_FREE_MIN_BYTES - 1, 1024)
    assert "База" in ResourceWatch.check_disk(ok_free, DB_MAX_BYTES + 1)
