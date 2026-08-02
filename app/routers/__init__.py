"""Порядок реєстрації важливий: form першим (перехоплює команди посеред анкети),
fallback — останнім."""

from app.routers import admin, archive, fallback, form, game, start, teams

routers = [
    form.router,
    start.router,
    teams.router,
    game.router,
    archive.router,
    admin.router,
    fallback.router,
]
