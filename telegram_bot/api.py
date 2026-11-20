from __future__ import annotations

"""Only this module knows how to talk to the team's backend."""
from dataclasses import dataclass

import aiohttp


class APIError(Exception):
    pass


@dataclass(frozen=True)
class Recipe:
    id: int
    title: str
    ingredients: list[str]
    steps: list[str]

    @classmethod
    def parse(cls, data: dict) -> Recipe:
        if (
            type(data.get("id")) is not int
            or data["id"] < 1
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

    async def search(self, query: str) -> list[Recipe]:
        return await self.recipes("/recipes", params={"query": query, "limit": 5})

    async def favorites(self, user_id: int) -> list[Recipe]:
        return await self.recipes(f"/users/{user_id}/favorites")

    async def save(self, user_id: int, recipe_id: int):
        await self.request("PUT", f"/users/{user_id}/favorites/{recipe_id}")

    async def remove(self, user_id: int, recipe_id: int):
        await self.request("DELETE", f"/users/{user_id}/favorites/{recipe_id}")
