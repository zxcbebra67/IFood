"""Local demonstration server, not the production backend. Binds to loopback only."""

import argparse
import sqlite3
from pathlib import Path

from aiohttp import web

RECIPES = [
    {
        "id": 1,
        "title": "Омлет с помидором",
        "ingredients": [
            "яйца — 2 шт.",
            "помидор — 1 шт.",
            "молоко — 2 ст. л.",
            "соль по вкусу",
        ],
        "steps": [
            "Взбей яйца с молоком и солью.",
            "Нарежь помидор и прогрей на сковороде.",
            "Залей яичной смесью и готовь под крышкой до готовности.",
        ],
    },
    {
        "id": 2,
        "title": "Паста с сыром",
        "ingredients": [
            "макароны — 200 г",
            "сыр — 50 г",
            "сливочное масло — 20 г",
            "соль по вкусу",
        ],
        "steps": [
            "Отвари макароны по инструкции на упаковке.",
            "Слей воду, оставив пару ложек.",
            "Добавь масло, тёртый сыр и немного воды от варки. Перемешай.",
        ],
    },
    {
        "id": 3,
        "title": "Салат из огурцов и помидоров",
        "ingredients": [
            "огурец — 1 шт.",
            "помидор — 2 шт.",
            "растительное масло — 1 ст. л.",
            "соль по вкусу",
        ],
        "steps": ["Вымой и нарежь овощи.", "Добавь масло и соль, перемешай."],
    },
    {
        "id": 4,
        "title": "Рис с курицей",
        "ingredients": [
            "рис — 150 г",
            "курица (филе) — 250 г",
            "морковь — 1 шт.",
            "соль по вкусу",
        ],
        "steps": [
            "Нарежь курицу и морковь.",
            "Обжарь курицу, затем добавь морковь.",
            (
                "Добавь промытый рис, соль и воду по инструкции к рису. "
                "Туши до готовности риса и полной готовности курицы."
            ),
        ],
    },
    {
        "id": 5,
        "title": "Банановая овсянка",
        "ingredients": ["овсяные хлопья — 50 г", "молоко — 200 мл", "банан — 1 шт."],
        "steps": [
            "Свари хлопья на молоке по инструкции на упаковке.",
            "Добавь нарезанный банан.",
        ],
    },
]
DB_KEY = web.AppKey("db", sqlite3.Connection)


def normalize(value):
    return value.casefold().replace("ё", "е").strip()


async def search(request):
    query = request.query.get("query", "").strip()
    try:
        limit = int(request.query.get("limit", "5"))
    except ValueError:
        raise web.HTTPBadRequest(text="Invalid limit")
    if not 1 <= len(query) <= 300 or not 1 <= limit <= 20:
        raise web.HTTPBadRequest(text="Invalid search")
    terms = [normalize(term) for term in query.split(",") if term.strip()]
    items = [
        recipe
        for recipe in RECIPES
        if terms
        and all(
            term in normalize(recipe["title"] + " " + " ".join(recipe["ingredients"]))
            for term in terms
        )
    ]
    return web.json_response({"items": items[:limit]})


def user_id(request):
    try:
        value = int(request.match_info["user_id"])
        if value <= 0:
            raise ValueError
        return value
    except ValueError:
        raise web.HTTPBadRequest(text="Invalid user ID")


async def favorites(request):
    rows = request.app[DB_KEY].execute(
        "SELECT recipe_id FROM favorites WHERE user_id = ? ORDER BY recipe_id",
        (user_id(request),),
    )
    ids = {row[0] for row in rows}
    return web.json_response({"items": [recipe for recipe in RECIPES if recipe["id"] in ids]})


async def change_favorite(request):
    uid = user_id(request)
    try:
        rid = int(request.match_info["recipe_id"])
    except ValueError:
        raise web.HTTPBadRequest(text="Invalid recipe ID")
    if rid not in {recipe["id"] for recipe in RECIPES}:
        raise web.HTTPNotFound()
    db = request.app[DB_KEY]
    if request.method == "PUT":
        db.execute("INSERT OR IGNORE INTO favorites VALUES (?, ?)", (uid, rid))
    else:
        db.execute("DELETE FROM favorites WHERE user_id = ? AND recipe_id = ?", (uid, rid))
    db.commit()
    return web.json_response({"saved": request.method == "PUT"})


def create_app(db_path=":memory:"):
    app = web.Application()

    async def database_lifetime(app):
        db = sqlite3.connect(db_path)
        db.execute(
            "CREATE TABLE IF NOT EXISTS favorites "
            "(user_id INTEGER, recipe_id INTEGER, PRIMARY KEY (user_id, recipe_id))"
        )
        db.commit()
        app[DB_KEY] = db
        try:
            yield
        finally:
            db.close()

    app.cleanup_ctx.append(database_lifetime)
    app.router.add_get("/recipes", search)
    app.router.add_get("/users/{user_id}/favorites", favorites)
    app.router.add_put("/users/{user_id}/favorites/{recipe_id}", change_favorite)
    app.router.add_delete("/users/{user_id}/favorites/{recipe_id}", change_favorite)
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Локальный демонстрационный сервер ЯЕда")
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()
    print("ДЕМО: 5 тестовых рецептов. Это не сервер команды.")
    web.run_app(
        create_app(str(Path(__file__).with_name("demo.sqlite3"))),
        host="127.0.0.1",
        port=args.port,
    )
