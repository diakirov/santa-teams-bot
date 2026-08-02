"""Жеребкування кільцем: ніхто не дарує собі, кожен отримує рівно один подарунок."""

import random


def make_pairs(player_ids: list[int]) -> list[tuple[int, int]]:
    """Повертає список (хто дарує, кому дарує). Потрібно щонайменше 2 учасники."""
    if len(player_ids) < 2:
        raise ValueError("Для жеребкування потрібно щонайменше 2 учасники")
    shuffled = list(player_ids)
    random.shuffle(shuffled)
    return [
        (shuffled[i], shuffled[(i + 1) % len(shuffled)])
        for i in range(len(shuffled))
    ]
