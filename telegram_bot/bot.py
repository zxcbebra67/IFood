import asyncio
import logging
import os
from html import escape
from pathlib import Path

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
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
CANCEL = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Отмена")]], resize_keyboard=True)


class Search(StatesGroup):
    query = State()


def recipe_text(recipe: Recipe) -> str:
    ingredients = "\n".join(f"• {escape(item)}" for item in recipe.ingredients)
    steps = "\n".join(f"{i}. {escape(step)}" for i, step in enumerate(recipe.steps, 1))
    return (
        f"<b>{escape(recipe.title)}</b>\n\nИнгредиенты:\n{ingredients}\n\nПриготовление:\n{steps}"
    )


def recipe_keyboard(recipe_id: int, saved: bool = False):
    action, label = ("remove", "💔 Удалить из избранного") if saved else ("save", "⭐ В избранное")
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=f"{action}:{recipe_id}")]]
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
                        recipe_keyboard(recipe.id, saved) if start + 1800 >= len(plain) else None
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
        await message.answer(str(error), reply_markup=MENU)


@router.message(F.text == "ℹ️ Информация")
async def information(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "ЯЕда помогает искать рецепты и сохранять их в избранное.\n"
        "/search — поиск\n/favorites — избранное\n/cancel — главное меню\n\n"
        "Генерация рецептов и история пока в разработке.",
        reply_markup=MENU,
    )


@router.message(F.text.in_({"✍️ Сгенерировать рецепт", "История"}))
async def upcoming(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Эта возможность пока в разработке. Уже доступны поиск и избранное.",
        reply_markup=MENU,
    )


@router.message(Search.query)
async def search_query(message: Message, state: FSMContext, api: RecipesAPI):
    query = (message.text or "").strip()
    if not query or query.startswith("/") or len(query) > 300:
        await message.answer("Нужен текст запроса от 1 до 300 символов. Для выхода нажми «Отмена».")
        return
    await message.answer("Ищу рецепты…")
    try:
        items = await api.search(query)
    except APIError as error:
        await message.answer(str(error) + "\nМожно повторить запрос или нажать «Отмена».")
        return
    if not items:
        await message.answer("Ничего не нашлось. Попробуй другое название или меньше ингредиентов.")
        return
    await state.clear()
    await message.answer("Вот что нашлось (до 5 рецептов):", reply_markup=MENU)
    await show_recipes(message, items)


@router.callback_query(F.data.startswith("save:"))
@router.callback_query(F.data.startswith("remove:"))
async def favorite_action(callback: CallbackQuery, api: RecipesAPI):
    action, _, value = (callback.data or "").partition(":")
    if not value.isascii() or not value.isdecimal() or int(value) < 1:
        await callback.answer("Некорректная кнопка. Запусти поиск заново.", show_alert=True)
        return
    # Stop Telegram's spinner before the potentially slow backend request.
    await callback.answer()
    try:
        if action == "save":
            await api.save(callback.from_user.id, int(value))
        else:
            await api.remove(callback.from_user.id, int(value))
    except APIError as error:
        if isinstance(callback.message, Message):
            await callback.message.answer(str(error))
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
        raise RuntimeError("Заполни BOT_TOKEN в telegram_bot/.env (пример: .env.example)")
    headers = {}
    if os.getenv("API_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['API_TOKEN']}"
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=15), headers=headers
    ) as session:
        dp = Dispatcher()
        dp.include_router(router)
        dp["api"] = RecipesAPI(session, os.getenv("API_BASE_URL", "http://127.0.0.1:8081"))
        async with Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML)) as bot:
            await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
