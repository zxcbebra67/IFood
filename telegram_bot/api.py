from __future__ import annotations

"""Only this module knows how to talk to the team's backend."""
from dataclasses import dataclass
from urllib.parse import quote

import aiohttp


class APIError(Exception):
    pass


@dataclass(frozen=True)
class Recipe:
    id: int | str
    title: str
    ingredients: list[str]
    steps: list[str]

    @classmethod
    def parse(cls, data: dict) -> Recipe:
        recipe_id = data.get("id")
        valid_id = (type(recipe_id) is int and recipe_id > 0) or (
            isinstance(recipe_id, str) and bool(recipe_id.strip())
        )
        if (
            not valid_id
            or not isinstance(data.get("title"), str)
            or not data["title"].strip()
            or any(
                not isinstance(data.get(key), list)
                or not all(isinstance(v, str) for v in data[key])
                for key in ("ingredients", "steps")
            )
        ):
            raise ValueError("Invalid recipe")
        return cls(data["id"], data["title"], data["ingredients"], data["steps"])


class RecipesAPI:
    def __init__(self, session: aiohttp.ClientSession, base_url: str):
        self.session = session
        self.base_url = base_url.rstrip("/")

    async def request(self, method: str, path: str, **kwargs):
        try:
            async with self.session.request(
                method, self.base_url + path, **kwargs
            ) as response:
                if response.status == 400:
                    error = await response.json()
                    if isinstance(error, dict) and isinstance(error.get("message"), str):
                        raise APIError(error["message"])
                if response.status == 404:
                    raise APIError("Рецепт больше не найден. Попробуй поискать заново.")
                if response.status >= 400:
                    raise APIError(
                        "Сервер пока не может выполнить запрос. Попробуй позже."
                    )
                return await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as error:
            raise APIError(
                "Не удалось связаться с сервером. Попробуй чуть позже."
            ) from error

    async def recipes(self, path: str, **kwargs) -> list[Recipe]:
        data = await self.request("GET", path, **kwargs)
        try:
            return [Recipe.parse(item) for item in data["items"]]
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise APIError("Сервер вернул непонятный ответ. Попробуй позже.") from error

    async def generate(self, description: str) -> Recipe:
        data = await self.request(
            "POST",
            "/recipes/generate",
            json={"dish_description": description},
            timeout=aiohttp.ClientTimeout(total=60),
        )
        try:
            return Recipe.parse(data)
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise APIError("Сервер вернул непонятный рецепт. Попробуй позже.") from error

    async def search(self, query: str) -> list[Recipe]:
        parameter = "ingredients" if "," in query else "dish_description"
        return await self.recipes("/recipes/search", params={parameter: query})

    async def favorites(self, user_id: int) -> list[Recipe]:
        return await self.recipes("/fav", params={"user_id": user_id})

    async def save(self, user_id: int, recipe_id: int | str):
        await self.request(
            "PUT", f"/fav/{quote(str(recipe_id), safe='')}", params={"user_id": user_id}
        )

    async def remove(self, user_id: int, recipe_id: int | str):
        await self.request(
            "DELETE", f"/fav/{quote(str(recipe_id), safe='')}", params={"user_id": user_id}
        )
