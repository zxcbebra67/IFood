import os
import secrets
from contextlib import asynccontextmanager
from typing import Annotated, Protocol
from uuid import uuid4

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from db import init_db
from search import match_ingredients


# ---------- Запуск ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = init_db()  # упадёт при старте, если база не открылась
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


# ---------- Временные реализации ----------
class InMemoryRecipeRepository:
    def __init__(self) -> None:
        self._items: dict[str, Recipe] = {}

    def search(self, ingredients, dish_description, taste_description):
        # Нечёткий поиск по ингредиентам (все обязательны).
        # Описания принимаются и проверяются, но пока не используются.
        if not ingredients:
            return []
        scored = []
        for recipe in self._items.values():
            score = match_ingredients(ingredients, recipe.ingredients)
            if score is not None:
                scored.append((score, recipe))
        # по релевантности убыванию, при равенстве по id возрастанию
        scored.sort(key=lambda pair: (-pair[0], pair[1].id))
        return [recipe for _, recipe in scored]

    def save(self, recipe):
        self._items[recipe.id] = recipe


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
_recipes = InMemoryRecipeRepository()
_generator = MockRecipeGenerator()


def get_recipes() -> RecipeRepository:
    return _recipes


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
