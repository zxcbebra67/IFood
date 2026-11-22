import os
import secrets
import re
from contextlib import asynccontextmanager
from typing import Annotated, Protocol
import json
from uuid import uuid4

from fastapi import Depends, FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from openai import APIError, AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from db import init_db
from search import match_ingredients


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

class GeneratedRecipeSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(
        description="Аппетитное, краткое и понятное название блюда на русском языке."
    )
    ingredients: list[str] = Field(
        description="Список ингредиентов с точными граммовками. Формат: 'Продукт — количество'. Минимум 2 элемента."
    )
    steps: list[str] = Field(
        description="Пошаговая инструкция приготовления блюда. Минимум 2 шага."
    )
    description: str = Field(
        description="Краткое описание блюда (1-2 предложения): вкус, текстура и подача."
    )
    taste_tags: list[str] = Field(
        description="Список из 2-4 вкусовых тегов в нижнем регистре (например: ['сытное', 'пряное'])."
    )
    dish_type: str | None = Field(
        description="Категория блюда: строго одно из значений 'Завтрак', 'Суп', 'Горячее', 'Салат', 'Десерт', 'Выпечка' или null."
    )

class FavoriteState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_favorite: bool
    
# ---------- Интерфейсы ----------
class RecipeRepository(Protocol):
    def search(
        self,
        ingredients: list[str],
        dish_description: str | None,
        taste_description: str | None,
    ) -> list[Recipe]: ...

    def save(self, recipe: Recipe) -> None: ...

    def get_by_id(self, recipe_id: str) -> Recipe | None: ...

    def add_favorite(self, user_id: int, recipe_id: str) -> None: ...

    def remove_favorite(self, user_id: int, recipe_id: str) -> None: ...

    def list_favorites(self, user_id: int) -> list[Recipe]: ...

class RecipeGenerator(Protocol):
    async def generate(self, dish_description: str) -> Recipe: ...

# ---------- Реализация работы с SQLite и 100 рецептов ----------
class SQLiteRecipeRepository:
    def __init__(self, conn) -> None:
        self._conn = conn

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]

    def search(
        self,
        ingredients: list[str],
        dish_description: str | None,
        taste_description: str | None,
    ) -> list[Recipe]:
        def norm(text: str) -> str:
            return text.lower().replace("ё", "е").strip()

        rows = self._conn.execute(
            "SELECT id, title, ingredients, steps, description, taste_tags, dish_type FROM recipes"
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

        free_text = norm(f"{dish_description or ''} {taste_description or ''}")

        # 1. отрицания
        negations: list[str] = []
        for match in re.finditer(r"\b(?:без|не)\s+([а-яa-z0-9-]+)", free_text):
            word = match.group(1)
            stem = word[:-2] if len(word) > 4 else (word[:-1] if len(word) > 3 else word)
            negations.append(stem)

        positive_text = re.sub(r"\b(?:без|не)\s+[а-яa-z0-9-]+", " ", free_text)

        # 2. "быстрое" блюдо (<= 30 минут)
        require_fast = bool(re.search(r"\bбыстр[а-я]*\b", positive_text))
        positive_text = re.sub(r"\bбыстр[а-я]*\b", " ", positive_text)

        stop_words = {
            "приготовь", "рецепт", "блюдо", "хочу", "сделай", "что-нибудь",
            "на", "из", "с", "со", "в", "во", "к", "ко", "и", "или", "для", "по",
        }
        positive_tokens = [
            w for w in re.findall(r"[а-яa-z0-9-]+", positive_text)
            if len(w) >= 2 and w not in stop_words
        ]
        positive_stems = [
            w[:-2] if len(w) > 5 else (w[:-1] if len(w) > 3 else w)
            for w in positive_tokens
        ]

        scored: list[tuple[float, Recipe]] = []

        for recipe in all_recipes:
            ingr_blob = norm(" ".join(recipe.ingredients))
            tags_blob = norm(" ".join(recipe.taste_tags))
            title_desc_blob = norm(f"{recipe.title} {recipe.description} {recipe.dish_type or ''}")
            steps_blob = norm(" ".join(recipe.steps))
            full_blob = f"{title_desc_blob} {ingr_blob} {tags_blob} {steps_blob}"

            if any(neg in full_blob for neg in negations):
                continue

            if require_fast:
                minutes = [int(m) for m in re.findall(r"(\d+)\s*мин", steps_blob)]
                if not minutes or sum(minutes) > 30:
                    continue

            score = 0.0

            if ingredients:
                ingr_score = match_ingredients(ingredients, recipe.ingredients)
                if ingr_score is None:
                    continue
                score += float(ingr_score) * 2.0

            # Смысловое сопоставление по описанию блюда и вкусам
            if positive_stems:
                matched_stems = 0
                for stem in positive_stems:
                    if stem in tags_blob:
                        score += 1.5
                        matched_stems += 1
                    elif stem in title_desc_blob or stem in ingr_blob:
                        score += 1.0
                        matched_stems += 1
                    elif stem in steps_blob:
                        score += 0.5
                        matched_stems += 1

                # Если запрос шёл только по описанию/вкусам, требуем хотя бы одно совпадение
                if matched_stems == 0:
                    continue
            elif not ingredients and not require_fast and not negations:
                continue

            scored.append((score, recipe))

        # Сортировка: по релевантности убыванию, при равенстве — по id возрастанию
        scored.sort(key=lambda pair: (-pair[0], pair[1].id))
        return [recipe for _, recipe in scored]


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
    
    def get_by_id(self, recipe_id: str) -> Recipe | None:
        row = self._conn.execute(
            "SELECT id, title, ingredients, steps, description, taste_tags, dish_type FROM recipes WHERE id = ?",
            (recipe_id,),
        ).fetchone()
        if not row:
            return None
        return Recipe(
            id=row[0],
            title=row[1],
            ingredients=json.loads(row[2]),
            steps=json.loads(row[3]),
            description=row[4],
            taste_tags=json.loads(row[5]),
            dish_type=row[6],
        )

    def add_favorite(self, user_id: int, recipe_id: str) -> None:
        cur = self._conn.execute(
            """
            INSERT OR IGNORE INTO favorites (user_id, recipe_id)
            SELECT ?, id FROM recipes WHERE id = ?
            """,
            (user_id, recipe_id),
        )
        if cur.rowcount == 0:
            exists = self._conn.execute(
                "SELECT 1 FROM recipes WHERE id = ?", (recipe_id,)
            ).fetchone()
            if not exists:
                raise AppError("Рецепт не найден.")

    def remove_favorite(self, user_id: int, recipe_id: str) -> None:
        exists = self._conn.execute(
            "SELECT 1 FROM recipes WHERE id = ?", (recipe_id,)
        ).fetchone()
        if not exists:
            raise AppError("Рецепт не найден.")

        self._conn.execute(
            "DELETE FROM favorites WHERE user_id = ? AND recipe_id = ?",
            (user_id, recipe_id),
        )

    def list_favorites(self, user_id: int) -> list[Recipe]:
        rows = self._conn.execute(
            """
            SELECT r.id, r.title, r.ingredients, r.steps, r.description, r.taste_tags, r.dish_type
            FROM favorites f
            JOIN recipes r ON r.id = f.recipe_id
            WHERE f.user_id = ?
            ORDER BY f.created_at DESC, f.recipe_id ASC
            """,
            (user_id,),
        ).fetchall()

        return [
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

def seed_initial_recipes(repo: SQLiteRecipeRepository) -> None:
    """Наполняет БД 100 логически согласованными рецептами без технических номеров."""
    if repo.count() > 0:
        return

    # 1. Сладкие блюда (завтраки, десерты, выпечка)
    sweet_dishes = [
        ("Завтрак", "Овсяная каша", ["овсяные хлопья — 80 г", "молоко — 200 мл", "соль — щепотка"], ["Вскипятите молоко.", "Всыпьте хлопья и варите 5 минут на медленном огне."], ["сладкое", "сытное"]),
        ("Завтрак", "Сырники домашние", ["творог — 250 г", "яйцо — 1 шт", "мука — 30 г", "сахар — 20 г"], ["Смешайте творог, яйцо, муку и сахар.", "Обжарьте на сковороде по 3 минуты с каждой стороны."], ["сладкое", "творожное"]),
        ("Завтрак", "Рисовая каша", ["рис круглозерный — 70 г", "молоко — 250 мл", "масло сливочное — 10 г"], ["Промойте рис и залейте молоком.", "Варите 20 минут до мягкости."], ["нежное", "сытное"]),
        ("Десерт", "Шарлотка воздушная", ["яблоки — 3 шт", "яйца — 3 шт", "сахар — 150 г", "мука — 150 г"], ["Взбейте яйца с сахаром, добавьте муку.", "Залейте нарезанные яблоки и запекайте 35 минут при 180 °C."], ["сладкое", "выпечка"]),
        ("Десерт", "Творожная запеканка", ["творог — 400 г", "яйца — 2 шт", "манка — 40 г", "сметана — 50 г"], ["Соедините все ингредиенты до однородности.", "Запекайте 30 минут при 180 °C."], ["нежное", "творожное"]),
    ]

    sweet_addons = [
        ("с ягодами", "свежие ягоды — 50 г", "Украсьте блюдо свежими ягодами перед подачей.", "ягодное"),
        ("с мёдом и орехами", "мёд — 1 ст. л.", "Полейте мёдом перед подачей.", "медовое"),
        ("с корицей", "корица молотая — 0.5 ч. л.", "Добавьте щепотку корицы для аромата.", "пряное"),
        ("с бананом", "банан — 1 шт", "Добавьте нарезанный кружочками банан.", "фруктовое"),
        ("с ванилью", "ванильный сахар — 1 ч. л.", "Добавьте ванильный сахар при приготовлении.", "ароматное"),
        ("с изюмом", "изюм — 30 г", "Добавьте предварительно распаренный изюм.", "сладкое"),
        ("с кокосовой стружкой", "кокосовая стружка — 15 г", "Посыпьте кокосовой стружкой.", "тропическое"),
        ("с шоколадом", "тёмный шоколад — 25 г", "Посыпьте тёртым тёмным шоколадом.", "шоколадное"),
    ]

    # 2. Несладкие блюда (супы, горячее, салаты, завтраки)
    savory_dishes = [
        ("Завтрак", "Омлет пышный", ["яйца — 3 шт", "молоко — 50 мл", "масло сливочное — 10 г"], ["Взбейте яйца с молоком и солью.", "Жарьте под крышкой 4 минуты на слабом огне."], ["нежное", "быстрое"]),
        ("Суп", "Куриный суп с лапшой", ["куриное филе — 300 г", "лапша — 100 г", "морковь — 1 шт", "лук — 1 шт", "картофель — 2 шт"], ["Сварите куриный бульон за 20 минут.", "Добавьте нарезанные овощи и лапшу, варите ещё 15 минут."], ["горячее", "легкое"]),
        ("Суп", "Крем-суп из тыквы", ["тыква — 500 г", "сливки — 100 мл", "лук — 1 шт", "масло оливковое — 15 мл"], ["Отварите кубики тыквы с луком до мягкости.", "Взбейте блендером со сливками."], ["нежное", "овощное"]),
        ("Суп", "Борщ классический", ["говядина — 400 г", "свёкла — 2 шт", "капуста — 300 г", "картофель — 3 шт", "томатная паста — 2 ст. л."], ["Сварите мясной бульон.", "Соедините овощи и пассерованную свёклу, варите 20 минут."], ["наваристое", "кисло-сладкое"]),
        ("Горячее", "Паста Карбонара", ["спагетти — 200 г", "бекон — 150 г", "яичный желток — 2 шт", "пармезан — 50 г"], ["Отварите спагетти аль денте.", "Обжарьте бекон и смешайте с горячей пастой, желтками и сыром."], ["сытное", "сливочное"]),
        ("Горячее", "Курица с рисом", ["куриное бедро — 300 г", "рис — 150 г", "морковь — 1 шт", "масло растительное — 20 мл"], ["Отварите рис до готовности.", "Обжарьте кусочки курицы с морковью 15 минут и соедините с рисом."], ["сытное", "домашнее"]),
        ("Горячее", "Гречка с грибами", ["гречка — 150 г", "шампиньоны — 200 г", "лук — 1 шт", "масло растительное — 20 мл"], ["Отварите рассыпчатую гречку.", "Обжарьте грибы с луком до золотистости и смешайте с крупой."], ["постное", "ароматное"]),
        ("Салат", "Салат с курицей и овощами", ["куриная грудка — 150 г", "салат айсберг — 100 г", "помидоры черри — 80 г", "масло оливковое — 15 мл"], ["Обжарьте куриную грудку и нарежьте ломтиками.", "Смешайте листья салата, черри и курицу, заправьте маслом."], ["свежее", "хрустящее"]),
        ("Салат", "Греческий салат", ["огурец — 1 шт", "помидор — 2 шт", "сыр фета — 100 г", "маслины — 50 г", "оливковое масло — 20 мл"], ["Нарежьте овощи и фету крупными кубиками.", "Добавьте маслины и заправьте оливковым маслом."], ["свежее", "соленое"]),
    ]

    savory_addons = [
        ("с зеленью", "свежая зелень — 15 г", "Посыпьте мелко рубленной зеленью перед подачей.", "свежее"),
        ("с чесноком", "чеснок — 2 зубчика", "Добавьте измельчённый чеснок в конце приготовления.", "пикантное"),
        ("с сыром", "сыр твёрдый — 40 г", "Посыпьте блюдо тёртым сыром.", "сырное"),
        ("по-деревенски", "перец чёрный молотый — по вкусу", "Приправьте свежемолотым чёрным перцем.", "домашнее"),
        ("с лимонной ноткой", "сок лимона — 10 мл", "Сбрызните лимонным соком перед подачей.", "с кислинкой"),
        ("острый", "перец чили — 5 г", "Добавьте мелко нарезанный перец чили.", "острое"),
        ("в сливочном соусе", "сливки 20% — 50 мл", "Прогрейте со сливками 2 минуты до загустения.", "сливочное"),
        ("с кунжутом", "кунжут — 10 г", "Посыпьте обжаренным кунжутом.", "ароматное"),
    ]

    recipes_to_save: list[Recipe] = []

    for d_idx, (dtype, title, ingr, steps, tags) in enumerate(sweet_dishes):
        for a_idx, (a_name, a_ingr, a_step, a_tag) in enumerate(sweet_addons):
            recipes_to_save.append(
                Recipe(
                    id=f"cat_sweet_{d_idx+1}_{a_idx+1}",
                    title=f"{title} {a_name}",
                    ingredients=ingr + [a_ingr],
                    steps=steps + [a_step],
                    description=f"{title} {a_name}. Отличный вариант для категории «{dtype.lower()}».",
                    taste_tags=list(dict.fromkeys(tags + [a_tag])),
                    dish_type=dtype,
                )
            )

    for d_idx, (dtype, title, ingr, steps, tags) in enumerate(savory_dishes):
        for a_idx, (a_name, a_ingr, a_step, a_tag) in enumerate(savory_addons):
            recipes_to_save.append(
                Recipe(
                    id=f"cat_savory_{d_idx+1}_{a_idx+1}",
                    title=f"{title} {a_name}",
                    ingredients=ingr + [a_ingr],
                    steps=steps + [a_step],
                    description=f"{title} {a_name}. Сытное и понятное блюдо ({dtype.lower()}).",
                    taste_tags=list(dict.fromkeys(tags + [a_tag])),
                    dish_type=dtype,
                )
            )
            
    for recipe in recipes_to_save:
        repo.save(recipe)


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

class OpenRouterRecipeGenerator:
    """Генератор рецептов через OpenRouter с использованием Responses API и Structured Outputs."""

    def __init__(self) -> None:
        self.api_key = os.environ.get("OPENROUTER_API_KEY")
        self.model = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
        self.client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=self.api_key or "missing-key",
            default_headers={
                "HTTP-Referer": "http://localhost:8080",
                "X-Title": "YaEda API",
            },
        )

    async def generate(self, dish_description: str) -> Recipe:
        if not self.api_key:
            raise AppError("Не настроен ключ доступа OPENROUTER_API_KEY.")

        try:
            response = await self.client.responses.parse(
                model=self.model,
                input=[
                    {
                        "role": "system",
                        "content": (
                            "Ты — опытный шеф-повар. Составь реалистичный рецепт на русском языке по запросу пользователя:\n"
                            "1. Ингредиенты: доступные продукты с точными граммовками (г, мл, ч. л., ст. л., шт.).\n"
                            "2. Шаги: чётко, в повелительном наклонении («Нарежьте», «Обжарьте»), одно действие на шаг с указанием времени и температуры, без воды.\n"
                            "3. Описание: 2–3 аппетитных предложения о вкусе, текстуре и подаче без штампов.\n"
                            "4. Теги: точные вкусовые характеристики (например: «пряное», «хрустящее», «сливочное»)."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Придумай рецепт: {dish_description}",
                    },
                ],
                text_format=GeneratedRecipeSchema,
            )

            if response.status == "incomplete":
                raise AppError("Нейросеть не успела дописать рецепт. Попробуйте сократить запрос.")

            parsed: GeneratedRecipeSchema | None = response.output_parsed
            if not parsed:
                raise AppError("Не удалось получить структурированный рецепт от модели.")

            return Recipe(
                id="",
                title=parsed.title,
                ingredients=parsed.ingredients,
                steps=parsed.steps,
                description=parsed.description,
                taste_tags=parsed.taste_tags,
                dish_type=parsed.dish_type,
            )

        except APIError as e:
            raise AppError(f"Ошибка API генерации (код {e.status_code}): {e.message}")
        except AppError:
            raise
        except Exception:
            raise AppError("Внутренняя ошибка при генерации рецепта. Попробуйте позже.")
       
# ---------- Зависимости ----------
_generator = OpenRouterRecipeGenerator()



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

UserId = Annotated[int, Query(ge=1, le=9223372036854775807)]
RecipeId = Annotated[str, Path(min_length=1, pattern=r"\S")]


async def ensure_empty_body(request: Request) -> None:
    """Для PUT/DELETE /fav тело запроса не принимается (вернуть 400)."""
    body = await request.body()
    if body:
        raise AppError("Тело запроса не допускается.")


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

@app.get("/recipes/{recipe_id}", response_model=Recipe, dependencies=auth)
def get_recipe(
    request: Request,
    recipe_id: RecipeId,
    repo: RecipeRepository = Depends(get_recipes),
):
    check_query(request, set())
    recipe = repo.get_by_id(recipe_id)
    if not recipe:
        raise AppError("Рецепт не найден.")
    return recipe


@app.get("/fav", response_model=RecipeList, dependencies=auth)
def list_favorites(
    request: Request,
    user_id: UserId,
    repo: RecipeRepository = Depends(get_recipes),
):
    check_query(request, {"user_id"})
    return RecipeList(items=repo.list_favorites(user_id))


@app.put("/fav/{recipe_id}", response_model=FavoriteState, dependencies=auth)
async def add_favorite(
    request: Request,
    recipe_id: RecipeId,
    user_id: UserId,
    repo: RecipeRepository = Depends(get_recipes),
):
    check_query(request, {"user_id"})
    await ensure_empty_body(request)
    repo.add_favorite(user_id, recipe_id)
    return FavoriteState(is_favorite=True)


@app.delete("/fav/{recipe_id}", response_model=FavoriteState, dependencies=auth)
async def remove_favorite(
    request: Request,
    recipe_id: RecipeId,
    user_id: UserId,
    repo: RecipeRepository = Depends(get_recipes),
):
    check_query(request, {"user_id"})
    await ensure_empty_body(request)
    repo.remove_favorite(user_id, recipe_id)
    return FavoriteState(is_favorite=False)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8080,
        reload=True,
        reload_includes=["*.py"],
        reload_excludes=[".venv/*", "data/*"],
    )
