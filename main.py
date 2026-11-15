from contextlib import asynccontextmanager

from typing import Annotated, Protocol

from fastapi import Depends, FastAPI, Query
from pydantic import BaseModel, Field

from db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = init_db()  # упадёт при старте, если база не открылась
    yield
    app.state.db.close()


app = FastAPI(title="FastAPI Web Server", lifespan=lifespan)


@app.get("/healthcheck")
def health_check():
    return {
        "status": "ok",
        "message": "Сервер работает стабильно",
    }

class Recipe(BaseModel):
    id: int | None = None
    title: str
    ingredients: list[str]
    steps: list[str]
    source: str  # "db" или "mock"


class GenerateRequest(BaseModel):
    ingredients: list[str] = Field(min_length=1)


# ---------- Интерфейсы ----------
class RecipeRepository(Protocol):
    def search(self, ingredients: list[str], limit: int) -> list[Recipe]: ...


class RecipeGenerator(Protocol):
    def generate(self, ingredients: list[str]) -> Recipe: ...


# ---------- Временные реализации ----------
class EmptyRecipeRepository:

    def search(self, ingredients: list[str], limit: int) -> list[Recipe]:
        return []


class MockRecipeGenerator:

    def generate(self, ingredients: list[str]) -> Recipe:
        return Recipe(
            title=f"Блюдо из: {', '.join(ingredients)}",
            ingredients=ingredients,
            steps=[
                "Подготовьте все продукты.",
                "Нарежьте и обработайте продукты.",
                "Приготовьте блюдо до готовности.",
            ],
            source="mock",
        )

def get_repository() -> RecipeRepository:
    return EmptyRecipeRepository()


def get_generator() -> RecipeGenerator:
    return MockRecipeGenerator()

@app.get("/recipes/search", response_model=list[Recipe])
def search_recipes(
    ingredients: Annotated[list[str], Query(min_length=1)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    repo: RecipeRepository = Depends(get_repository),
):
    return repo.search(ingredients, limit)


@app.post("/recipes/generate", response_model=Recipe)
def generate_recipe(
    body: GenerateRequest,
    repo: RecipeRepository = Depends(get_repository),
    generator: RecipeGenerator = Depends(get_generator),
):
    found = repo.search(body.ingredients, limit=1)
    if found:
        return found[0]
    return generator.generate(body.ingredients)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)