"""Local demonstration server, not the production backend. Binds to loopback only."""

import argparse
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

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
    query = (request.query.get("ingredients") or request.query.get("dish_description", "")).strip()
    if not 1 <= len(query) <= 2000:
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
    return web.json_response({"items": items})


async def generate(request):
    try:
        body = await request.json()
    except ValueError:
        body = None
    description = body.get("dish_description") if isinstance(body, dict) else None
    if not isinstance(description, str) or not 1 <= len(description.strip()) <= 2000:
        return web.json_response(
            {"message": "Опиши блюдо текстом от 1 до 2000 символов.", "request_id": uuid4().hex},
            status=400,
        )
    description = description.strip()
    recipe = {
        "id": f"recipe_{uuid4().hex}",
        "title": f"Демо-рецепт: {description[:60]}",
        "ingredients": ["Тестовый ингредиент — 100 г"],
        "steps": ["Подготовь ингредиенты из списка."],
        "description": description,
        "taste_tags": [],
        "dish_type": None,
    }
    db = request.app[DB_KEY]
    with db:
        db.execute("INSERT INTO generated_recipes VALUES (?, ?)", (recipe["id"], json.dumps(recipe)))
    return web.json_response(recipe)


def user_id(request):
    try:
        value = int(request.query.get("user_id", ""))
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
    items = [recipe for recipe in RECIPES if recipe["id"] in ids]
    generated = request.app[DB_KEY].execute(
        "SELECT recipe FROM generated_recipes JOIN favorites "
        "ON generated_recipes.id = favorites.recipe_id WHERE user_id = ? ORDER BY id",
        (user_id(request),),
    )
    items.extend(json.loads(row[0]) for row in generated)
    return web.json_response({"items": items})


async def change_favorite(request):
    uid = user_id(request)
    value = request.match_info["recipe_id"]
    db = request.app[DB_KEY]
    rid = int(value) if value.isascii() and value.isdecimal() else value
    if rid not in {recipe["id"] for recipe in RECIPES} and not db.execute(
        "SELECT 1 FROM generated_recipes WHERE id = ?", (rid,)
    ).fetchone():
        raise web.HTTPNotFound()
    if request.method == "PUT":
        db.execute("INSERT OR IGNORE INTO favorites VALUES (?, ?)", (uid, rid))
    else:
        db.execute(
            "DELETE FROM favorites WHERE user_id = ? AND recipe_id = ?", (uid, rid)
        )
    db.commit()
    return web.json_response({"is_favorite": request.method == "PUT"})


def create_app(db_path=":memory:"):
    app = web.Application()

    async def database_lifetime(app):
        db = sqlite3.connect(db_path)
        db.execute(
            "CREATE TABLE IF NOT EXISTS favorites "
            "(user_id INTEGER, recipe_id INTEGER, PRIMARY KEY (user_id, recipe_id))"
        )
        db.execute("CREATE TABLE IF NOT EXISTS generated_recipes (id TEXT PRIMARY KEY, recipe TEXT NOT NULL)")
        db.commit()
        app[DB_KEY] = db
        try:
            yield
        finally:
            db.close()

    app.cleanup_ctx.append(database_lifetime)
    app.router.add_get("/recipes/search", search)
    app.router.add_post("/recipes/generate", generate)
    app.router.add_get("/fav", favorites)
    app.router.add_put("/fav/{recipe_id}", change_favorite)
    app.router.add_delete("/fav/{recipe_id}", change_favorite)
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Локальный демонстрационный сервер ЯЕда"
    )
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()
    print("ДЕМО: 5 тестовых рецептов. Это не сервер команды.")
    web.run_app(
        create_app(str(Path(__file__).with_name("demo.sqlite3"))),
        host="127.0.0.1",
        port=args.port,
    )
