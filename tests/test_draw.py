import random

import pytest

from app.services.draw import make_pairs


@pytest.mark.parametrize("n", [2, 3, 5, 15, 50])
def test_ring_properties(n):
    for seed in range(50):
        random.seed(seed)
        players = list(range(100, 100 + n))
        pairs = make_pairs(players)

        givers = [g for g, _ in pairs]
        receivers = [r for _, r in pairs]

        # ніхто не дарує собі
        assert all(g != r for g, r in pairs)
        # кожен дарує рівно раз і отримує рівно раз
        assert sorted(givers) == sorted(players)
        assert sorted(receivers) == sorted(players)


def test_single_cycle():
    random.seed(7)
    players = list(range(10))
    mapping = dict(make_pairs(players))
    # рухаючись за парами, повертаємось на старт рівно за n кроків
    current = players[0]
    for _ in range(len(players) - 1):
        current = mapping[current]
        assert current != players[0]
    assert mapping[current] == players[0]


def test_too_few_players():
    with pytest.raises(ValueError):
        make_pairs([1])
    with pytest.raises(ValueError):
        make_pairs([])
