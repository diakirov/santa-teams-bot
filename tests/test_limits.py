from app.services.limits import UNLIMITED, max_members, max_teams, retention_days

DEFAULTS = {
    "limit.user.max_teams": 5,
    "limit.user.max_members": 50,
    "limit.kerivnyk.max_teams": 10,
    "limit.kerivnyk.max_members": 100,
}


def test_role_defaults():
    assert max_teams("user", None, DEFAULTS) == 5
    assert max_teams("kerivnyk", None, DEFAULTS) == 10
    assert max_members("user", None, None, DEFAULTS) == 50
    assert max_members("kerivnyk", None, None, DEFAULTS) == 100


def test_admin_unlimited():
    assert max_teams("admin", None, DEFAULTS) == UNLIMITED
    assert max_members("admin", None, None, DEFAULTS) == UNLIMITED


def test_user_override_wins_over_default():
    assert max_teams("user", 12, DEFAULTS) == 12
    assert max_members("user", None, 200, DEFAULTS) == 200


def test_team_override_wins_over_everything():
    assert max_members("user", 500, 200, DEFAULTS) == 500
    assert max_members("admin", 30, None, DEFAULTS) == 30


def test_retention_by_role():
    assert retention_days("user") == 14
    assert retention_days("kerivnyk") == 30
    assert retention_days("admin") == 365
    assert retention_days("хтозна") == 14  # невідома роль — найсуворіший строк


def test_changed_global_default_applies_without_override():
    changed = dict(DEFAULTS, **{"limit.user.max_teams": 7})
    assert max_teams("user", None, changed) == 7
    # персональний виняток не залежить від зміни глобального
    assert max_teams("user", 3, changed) == 3
