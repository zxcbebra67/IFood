import asyncio
import logging
import os
import re
from html import escape
from pathlib import Path

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv

from .api import APIError, Recipe, RecipesAPI

router = Router()
MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🔍 Найти рецепт")],
        [
            KeyboardButton(text="⭐ Избранное"),
            KeyboardButton(text="✍️ Сгенерировать рецепт"),
        ],
        [KeyboardButton(text="ℹ️ Информация"), KeyboardButton(text="История")],
    ],
    resize_keyboard=True,
)
CANCEL = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Отмена")]], resize_keyboard=True
)


class Search(StatesGroup):
    query = State()


class Generation(StatesGroup):
    description = State()


def recipe_text(recipe: Recipe) -> str:
    ingredients = "\n".join(f"• {escape(item)}" for item in recipe.ingredients)
    steps = "\n".join(f"{i}. {escape(step)}" for i, step in enumerate(recipe.steps, 1))
    return f"<b>{escape(recipe.title)}</b>\n\nИнгредиенты:\n{ingredients}\n\nПриготовление:\n{steps}"


def recipe_keyboard(recipe_id: int | str, saved: bool = False):
    action, label = (
        ("remove", "💔 Удалить из избранного") if saved else ("save", "⭐ В избранное")
    )
    if isinstance(recipe_id, str):
        action += "-id"
    callback_data = f"{action}:{recipe_id}"
    if len(callback_data.encode("utf-8")) > 64:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=callback_data)]]
    )


async def show_recipes(message: Message, recipes: list[Recipe], saved: bool = False):
    for recipe in recipes:
        text = recipe_text(recipe)
        # Telegram limits text length; attach the action to the final part.
        if len(text.encode("utf-16-le")) // 2 <= 4000:
            await message.answer(text, reply_markup=recipe_keyboard(recipe.id, saved))
        else:
            plain = f"{recipe.title}\n\nИнгредиенты:\n" + "\n".join(recipe.ingredients)
            plain += "\n\nПриготовление:\n" + "\n".join(recipe.steps)
            for start in range(0, len(plain), 1800):
                await message.answer(
                    plain[start : start + 1800],
                    parse_mode=None,
                    reply_markup=(
                        recipe_keyboard(recipe.id, saved)
                        if start + 1800 >= len(plain)
                        else None
                    ),
                )


@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Добро пожаловать в бот ЯЕда! 🍽\n\n"
        "Здесь можно подобрать рецепт по названию или ингредиентам "
        "и сохранить понравившийся в избранное.\n\n"
        "Нажми «🔍 Найти рецепт», чтобы начать. Приятного аппетита!",
        reply_markup=MENU,
    )


@router.message(Command("cancel"))
@router.message(F.text == "Отмена")
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Ты в главном меню.", reply_markup=MENU)


@router.message(Command("search"))
@router.message(F.text == "🔍 Найти рецепт")
async def search_start(message: Message, state: FSMContext):
    await state.set_state(Search.query)
    await message.answer(
        "Напиши название блюда или ингредиенты через запятую.\nНапример: яйца, помидор",
        reply_markup=CANCEL,
    )


@router.message(Command("favorites"))
@router.message(F.text == "⭐ Избранное")
async def favorites(message: Message, state: FSMContext, api: RecipesAPI):
    await state.clear()
    if not message.from_user:
        return
    try:
        items = await api.favorites(message.from_user.id)
        await message.answer(
            "Твоё избранное:"
            if items
            else "В избранном пока пусто. Найди рецепт и нажми «⭐ В избранное».",
            reply_markup=MENU,
        )
        await show_recipes(message, items, saved=True)
    except APIError as error:
        await message.answer(str(error), reply_markup=MENU, parse_mode=None)


@router.message(F.text == "ℹ️ Информация")
async def information(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "В ЯЕде можно искать и генерировать рецепты, а понравившиеся добавлять в избранное.\n"
        "/search — поиск\n/favorites — избранное\n/cancel — главное меню\n\n"
        "/generate — генерация рецепта\n"
        "История пока в разработке.",
        reply_markup=MENU,
    )


@router.message(Command("generate"))
@router.message(F.text == "✍️ Сгенерировать рецепт")
async def generation_start(message: Message, state: FSMContext):
    await state.set_state(Generation.description)
    await message.answer(
        "Опиши блюдо, которое хочешь приготовить. Например: острый ужин из курицы и риса.",
        reply_markup=CANCEL,
    )


@router.message(F.text == "История")
async def upcoming(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "История пока в разработке. Поиск, генерация рецептов и избранное уже доступны.",
        reply_markup=MENU,
    )


@router.message(Search.query)
async def search_query(message: Message, state: FSMContext, api: RecipesAPI):
    query = (message.text or "").strip()
    if not query or query.startswith("/") or len(query) > 2000:
        await message.answer(
            "Нужен текст запроса от 1 до 2000 символов. Для выхода нажми «Отмена»."
        )
        return
    await message.answer("Ищу рецепты…")
    try:
        items = await api.search(query)
    except APIError as error:
        await message.answer(
            str(error) + "\nМожно повторить запрос или нажать «Отмена».", parse_mode=None
        )
        return
    if not items:
        await message.answer(
            "Ничего не нашлось. Попробуй другой запрос или нажми «✍️ Сгенерировать рецепт».",
            reply_markup=MENU,
        )
        return
    await state.clear()
    await message.answer("Вот что нашлось:", reply_markup=MENU)
    await show_recipes(message, items)


@router.message(Generation.description)
async def generation_query(message: Message, state: FSMContext, api: RecipesAPI):
    description = (message.text or "").strip()
    if not description or description.startswith("/") or len(description) > 2000:
        await message.answer("Опиши блюдо текстом от 1 до 2000 символов.")
        return
    await state.clear()
    await message.answer("Готовлю рецепт…", reply_markup=MENU)
    try:
        recipe = await api.generate(description)
    except APIError as error:
        await message.answer(str(error), parse_mode=None)
        return
    await show_recipes(message, [recipe])


@router.callback_query(F.data.startswith("save-id:"))
@router.callback_query(F.data.startswith("remove-id:"))
@router.callback_query(F.data.startswith("save:"))
@router.callback_query(F.data.startswith("remove:"))
async def favorite_action(callback: CallbackQuery, api: RecipesAPI):
    action, _, value = (callback.data or "").partition(":")
    if action.endswith("-id") and value.strip():
        action = action.removesuffix("-id")
        recipe_id = value
    elif value.isascii() and value.isdecimal() and int(value) > 0:
        recipe_id = int(value)
    elif re.fullmatch(r"recipe_[a-zA-Z0-9_-]+", value):
        recipe_id = value
    else:
        await callback.answer(
            "Некорректная кнопка. Запусти поиск заново.", show_alert=True
        )
        return
    # Stop Telegram's spinner before the potentially slow backend request.
    await callback.answer()
    try:
        if action == "save":
            await api.save(callback.from_user.id, recipe_id)
        else:
            await api.remove(callback.from_user.id, recipe_id)
    except APIError as error:
        if isinstance(callback.message, Message):
            await callback.message.answer(str(error), parse_mode=None)
        return
    if isinstance(callback.message, Message):
        # Sending confirmation also works for an old/already pressed button.
        await callback.message.answer(
            "✅ Рецепт сохранён в избранное."
            if action == "save"
            else "Рецепт удалён из избранного.",
            reply_markup=MENU,
        )


@router.message()
async def fallback(message: Message):
    await message.answer(
        "Выбери действие в меню. Для поиска нажми «🔍 Найти рецепт».", reply_markup=MENU
    )


async def main():
    load_dotenv(Path(__file__).with_name(".env"))
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "Заполни BOT_TOKEN в telegram_bot/.env (пример: .env.example)"
        )
    proxy_url = os.getenv("TELEGRAM_PROXY_URL", "").strip()
    try:
        telegram_session = AiohttpSession(proxy=proxy_url or None)
    except (TypeError, ValueError):
        raise RuntimeError(
            "Проверь TELEGRAM_PROXY_URL: нужен URL прокси с поддерживаемой схемой и портом."
        ) from None
    headers = {}
    if os.getenv("BOT_API_KEY"):
        headers["X-API-Key"] = os.environ["BOT_API_KEY"]
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=15), headers=headers
    ) as session:
        dp = Dispatcher()
        dp.include_router(router)
        dp["api"] = RecipesAPI(
            session, os.getenv("API_BASE_URL", "http://127.0.0.1:8081")
        )
        async with Bot(
            token, session=telegram_session, default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        ) as bot:
            await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
