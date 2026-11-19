import os
import secrets
from contextlib import asynccontextmanager
from typing import Annotated, Protocol
import json
from uuid import uuid4

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from db import init_db


# ---------- Запуск ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = init_db()  # упадёт при старте, если база не открылась
    repo = SQLiteRecipeRepository(app.state.db)
    seed_initial_recipes(repo)
    yield
    app.state.db.close()


app = FastAPI(title="ЯЕда API", lifespan=lifespan)


# ---------- Модели ----------
class Recipe(BaseModel):
    id: str
    title: str
    ingredients: list[str] = Field(min_length=1)
    steps: list[str] = Field(min_length=1)
    description: str
    taste_tags: list[str]
    dish_type: str | None


class RecipeList(BaseModel):
    items: list[Recipe]


class GenerateRequest(BaseModel):
    dish_description: str = Field(min_length=1, max_length=2000, pattern=r"\S")


# ---------- Интерфейсы ----------
class RecipeRepository(Protocol):
    def search(
        self,
        ingredients: list[str],
        dish_description: str | None,
        taste_description: str | None,
    ) -> list[Recipe]: ...

    def save(self, recipe: Recipe) -> None: ...


class RecipeGenerator(Protocol):
    async def generate(self, dish_description: str) -> Recipe: ...

# ---------- Реализация работы с SQLite ----------
class SQLiteRecipeRepository:
    def __init__(self, conn) -> None:
        self._conn = conn

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]

    def search(self, ingredients: list[str], dish_description: str | None, taste_description: str | None) -> list[Recipe]:
        # Достаем все рецепты из БД и фильтруем (так как в SQLite списки лежат в JSON)
        rows = self._conn.execute(
            "SELECT id, title, ingredients, steps, description, taste_tags, dish_type FROM recipes ORDER BY id"
        ).fetchall()

        all_recipes = [
            Recipe(
                id=row[0],
                title=row[1],
                ingredients=json.loads(row[2]),
                steps=json.loads(row[3]),
                description=row[4],
                taste_tags=json.loads(row[5]),
                dish_type=row[6],
            )
            for row in rows
        ]

        needles = [i.lower() for i in ingredients]
        if not needles:
            return all_recipes[:20]  # Если искали по описанию, возвращаем до 20 рецептов

        return [
            r
            for r in all_recipes
            if all(any(n in line.lower() for line in r.ingredients) for n in needles)
        ]

    def save(self, recipe: Recipe) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO recipes (id, title, ingredients, steps, description, taste_tags, dish_type)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                recipe.id,
                recipe.title,
                json.dumps(recipe.ingredients, ensure_ascii=False),
                json.dumps(recipe.steps, ensure_ascii=False),
                recipe.description,
                json.dumps(recipe.taste_tags, ensure_ascii=False),
                recipe.dish_type,
            ),
        )


# ---------- Генератор 100 рецептов (Сидирование БД) ----------
def seed_initial_recipes(repo: SQLiteRecipeRepository) -> None:
    """Если база пустая, заполняет её 100 уникальными рецептами."""
    if repo.count() > 0:
        return

    base_dishes = [
        ("Завтрак", "Овсяная каша", ["овсяные хлопья — 80 г", "молоко — 200 мл", "соль — щепотка"], ["Вскипятите молоко.", "Всыпьте хлопья и варите 5 минут."], ["сладкое", "сытное"]),
        ("Завтрак", "Омлет с молоком", ["яйца — 3 шт", "молоко — 50 мл", "масло сливочное — 10 г"], ["Взбейте яйца с молоком.", "Жарьте под крышкой 4 минуты."], ["нежное", "быстрое"]),
        ("Завтрак", "Сырники домашние", ["творог — 250 г", "яйцо — 1 шт", "мука — 30 г", "сахар — 20 г"], ["Смешайте ингредиенты.", "Обжарьте до золотистой корочки."], ["сладкое", "творожное"]),
        ("Суп", "Куриный суп с лапшой", ["куриное филе — 300 г", "лапша — 100 г", "морковь — 1 шт", "лук — 1 шт", "картофель — 2 шт"], ["Сварите бульон.", "Добавьте овощи и лапшу, варите 15 минут."], ["горячее", "легкое"]),
        ("Суп", "Крем-суп из тыквы", ["тыква — 500 г", "сливки — 100 мл", "лук — 1 шт", "чеснок — 2 зубчика"], ["Отварите тыкву.", "Взбейте блендером со сливками."], ["нежное", "пряное"]),
        ("Суп", "Борщ классический", ["говядина — 400 г", "свекла — 2 шт", "капуста — 300 г", "картофель — 3 шт", "томатная паста — 2 ст.л."], ["Сварите мясной бульон.", "Соедините овощи и свекольную зажарку."], ["наваристое", "кисло-сладкое"]),
        ("Горячее", "Паста Карбонара", ["спагетти — 200 г", "бекон — 150 г", "яичный желток — 2 шт", "пармезан — 50 г"], ["Отварите спагетти.", "Смешайте горячую пасту с беконом, желтками и сыром."], ["сытное", "сливочное"]),
        ("Горячее", "Курица терияки с рисом", ["куриное бедро — 300 г", "соус терияки — 60 мл", "рис — 150 г", "кунжут — 5 г"], ["Отварите рис.", "Обжарьте курицу в соусе терияки 5 минут."], ["азиатское", "сладко-соленое"]),
        ("Горячее", "Гречка с грибами", ["гречка — 150 г", "шампиньоны — 200 г", "лук — 1 шт", "масло растительное — 20 мл"], ["Отварите гречку.", "Обжарьте грибы с луком и смешайте с крупой."], ["постное", "ароматное"]),
        ("Салат", "Цезарь с курицей", ["куриная грудка — 150 г", "салат айсберг — 100 г", "помидоры черри — 80 г", "пармезан — 30 г"], ["Обжарьте курицу.", "Смешайте листья, курицу, черри и сыр."], ["свежее", "хрустящее"]),
        ("Салат", "Греческий салат", ["огурец — 1 шт", "помидор — 2 шт", "сыр фета — 100 г", "маслины — 50 г", "оливковое масло — 20 мл"], ["Нарежьте овощи и фету.", "Заправьте маслом."], ["свежее", "соленое"]),
        ("Десерт", "Шарлотка с яблоками", ["яблоки — 3 шт", "яйца — 3 шт", "сахар — 150 г", "мука — 150 г"], ["Взбейте яйца с сахаром и мукой.", "Залейте яблоки и выпекайте 35 минут."], ["сладкое", "выпечка"]),
    ]

    addons = [
        ("с зеленью", "свежая зелень — 15 г", "Украсьте свежей зеленью.", "свежее"),
        ("с чесноком", "чеснок — 1 зубчик", "Добавьте измельченный чеснок.", "пикантное"),
        ("по-домашнему", "перец черный молотый — по вкусу", "Приправьте свежемолотым перцем.", "домашнее"),
        ("с сыром", "сыр твердый — 40 г", "Посыпьте тертым сыром.", "сырное"),
        ("острый", "перец чили — 5 г", "Добавьте мелко нарезанный чили.", "острое"),
        ("на скорую руку", "специи универсальные — 1 ч.л.", "Используйте любимые специи.", "быстрое"),
        ("фитнес-версия", "оливковое масло — 5 мл", "Используйте минимум масла.", "легкое"),
        ("праздничный", "орехи грецкие — 20 г", "Добавьте дробленые орехи.", "праздничное"),
        ("с лимоном", "сок лимона — 10 мл", "Сбрызните лимонным соком.", "цитрусовое"),
    ]

    for i in range(100):
        dish_type, b_title, b_ingr, b_steps, b_tags = base_dishes[i % len(base_dishes)]
        a_name, a_ingr, a_step, a_tag = addons[(i // len(base_dishes)) % len(addons)]

        recipe = Recipe(
            id=f"recipe_{i+1:03d}_{uuid4().hex[:4]}",
            title=f"{b_title} ({a_name}) #{i+1}",
            ingredients=b_ingr + [a_ingr],
            steps=b_steps + [a_step],
            description=f"Рецепт блюда «{b_title}» ({a_name}). Категория: {dish_type.lower()}.",
            taste_tags=list(set(b_tags + [a_tag])),
            dish_type=dish_type,
        )
        repo.save(recipe)

# ---------- Временные реализации ----------
class MockRecipeGenerator:
    """Вместо внешнего AI API: возвращает заготовку."""

    async def generate(self, dish_description: str) -> Recipe:
        return Recipe(
            id="",  # настоящий id назначает backend, а не генератор
            title=f"Блюдо по запросу: {dish_description[:60]}",
            ingredients=["Продукт — 100 г"],
            steps=["Подготовьте продукты.", "Приготовьте блюдо до готовности."],
            description=dish_description,
            taste_tags=[],
            dish_type=None,
        )
        
# ---------- Зависимости ----------
_generator = MockRecipeGenerator()


def get_recipes(request: Request) -> RecipeRepository:
    return SQLiteRecipeRepository(request.app.state.db)


def get_generator() -> RecipeGenerator:
    return _generator

# ---------- Ошибки и request_id ----------
class AppError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message


def error_response(request: Request, message: str) -> JSONResponse:
    rid = getattr(request.state, "request_id", "-")
    return JSONResponse(
        status_code=400,
        content={"message": message, "request_id": rid},
        headers={"X-Request-Id": rid},
    )


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request.state.request_id = f"req-{uuid4().hex[:12]}"
    response = await call_next(request)
    response.headers["X-Request-Id"] = request.state.request_id
    return response


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return error_response(request, exc.message)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return error_response(request, "Некорректные данные запроса.")


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    return error_response(request, "Запрос не может быть выполнен.")


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    return error_response(request, "Внутренняя ошибка. Попробуйте позже.")


# ---------- Авторизация бота ----------
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(key: str | None = Depends(api_key_header)) -> None:
    expected = os.environ.get("BOT_API_KEY")
    if not expected or not key or not secrets.compare_digest(key, expected):
        raise AppError("Неверный или отсутствующий ключ доступа.")


auth = [Depends(require_api_key)]


# ---------- Вспомогательное ----------
def check_query(request: Request, allowed: set[str]) -> None:
    """400 на неизвестные и повторяющиеся query-параметры."""
    keys = [k for k, _ in request.query_params.multi_items()]
    if len(keys) != len(set(keys)) or not set(keys) <= allowed:
        raise AppError("Недопустимые или повторяющиеся параметры запроса.")


def parse_ingredients(raw: str | None) -> list[str]:
    if raw is None:
        return []
    parts = [p.strip() for p in raw.split(",")]
    if any(not p for p in parts):
        raise AppError("Между запятыми в ingredients есть пустой продукт.")
    if len(parts) > 30 or any(len(p) > 100 for p in parts):
        raise AppError("Не более 30 продуктов, каждый до 100 символов.")
    return parts


Text = Annotated[str | None, Query(min_length=1, max_length=2000, pattern=r"\S")]


# ---------- Эндпоинты ----------
@app.get("/healthcheck")
def health_check():
    return {"status": "ok"}


@app.get("/recipes/search", response_model=RecipeList, dependencies=auth)
def search_recipes(
    request: Request,
    ingredients: Text = None,
    dish_description: Text = None,
    taste_description: Text = None,
    repo: RecipeRepository = Depends(get_recipes),
):
    check_query(request, {"ingredients", "dish_description", "taste_description"})
    items = parse_ingredients(ingredients)
    if not items and not (dish_description or taste_description):
        raise AppError("Укажите ингредиенты или описание блюда.")
    return RecipeList(items=repo.search(items, dish_description, taste_description))


@app.post("/recipes/generate", response_model=Recipe, dependencies=auth)
async def generate_recipe(
    body: GenerateRequest,
    repo: RecipeRepository = Depends(get_recipes),
    generator: RecipeGenerator = Depends(get_generator),
):
    draft = await generator.generate(body.dish_description.strip())
    recipe = draft.model_copy(update={"id": f"recipe_{uuid4().hex[:8]}"})
    repo.save(recipe)  # сохраняем ДО ответа
    return recipe


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)