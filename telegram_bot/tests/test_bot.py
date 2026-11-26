import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from aiohttp import web
from aiohttp.test_utils import TestServer

from telegram_bot.api import APIError, Recipe, RecipesAPI
from telegram_bot.bot import (
    Search,
    favorite_action,
    recipe_text,
    router,
    search_query,
)
from telegram_bot.demo_api import create_app


class APIIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = str(Path(self.directory.name) / "favorites.sqlite3")
        self.server = TestServer(create_app(self.db))
        await self.server.start_server()
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2))
        self.api = RecipesAPI(self.session, str(self.server.make_url("")).rstrip("/"))

    async def asyncTearDown(self):
        await self.session.close()
        await self.server.close()
        self.directory.cleanup()

    async def test_search_and_no_matches(self):
        self.assertEqual([r.id for r in await self.api.search("ЯЙЦА, помидор")], [1])
        self.assertEqual([r.id for r in await self.api.search("ПАСТА")], [2])
        self.assertEqual(await self.api.search("несуществующее"), [])

    async def test_save_is_idempotent_and_users_are_isolated(self):
        await self.api.save(100, 1)
        await self.api.save(100, 1)
        self.assertEqual([r.id for r in await self.api.favorites(100)], [1])
        self.assertEqual(await self.api.favorites(101), [])
        await self.api.remove(100, 1)
        await self.api.remove(100, 1)
        self.assertEqual(await self.api.favorites(100), [])

    async def test_favorites_survive_server_restart(self):
        await self.api.save(100, 2)
        await self.server.close()
        self.server = TestServer(create_app(self.db))
        await self.server.start_server()
        self.api = RecipesAPI(self.session, str(self.server.make_url("")).rstrip("/"))
        self.assertEqual([r.id for r in await self.api.favorites(100)], [2])

    async def test_missing_recipe_and_invalid_query(self):
        with self.assertRaises(APIError):
            await self.api.save(100, 999)
        with self.assertRaises(APIError):
            await self.api.search(" ")
        with self.assertRaises(APIError):
            await self.api.search("x" * 2001)

    async def test_unavailable_server(self):
        await self.server.close()
        with self.assertRaises(APIError):
            await self.api.search("яйца")

    async def test_bad_server_responses(self):
        for status, payload in [
            (200, {"wrong": []}),
            (200, {"items": [None]}),
            (200, {"items": [{"id": "wrong"}]}),
            (500, {}),
        ]:

            async def invalid_response(request, status=status, payload=payload):
                return web.json_response(payload, status=status)

            app = web.Application()
            app.router.add_get("/recipes/search", invalid_response)
            server = TestServer(app)
            await server.start_server()
            try:
                api = RecipesAPI(self.session, str(server.make_url("")).rstrip("/"))
                with self.assertRaises(APIError):
                    await api.search("яйца")
            finally:
                await server.close()


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.message = AsyncMock(spec=Message)
        self.message.answer = AsyncMock()
        self.message.text = "яйца"
        self.state = AsyncMock(spec=FSMContext)
        self.api = AsyncMock(spec=RecipesAPI)

    async def test_success_clears_state_and_shows_recipe(self):
        self.api.search.return_value = [Recipe(1, "Омлет", ["яйца"], ["Приготовить"])]
        await search_query(self.message, self.state, self.api)
        self.api.search.assert_awaited_once_with("яйца")
        self.state.clear.assert_awaited_once()
        self.assertIn("Омлет", self.message.answer.call_args_list[-1].args[0])
        keyboard = self.message.answer.call_args_list[-1].kwargs["reply_markup"]
        self.assertEqual(keyboard.inline_keyboard[0][0].callback_data, "save:1")

    async def test_empty_results_and_failure_keep_search_active(self):
        self.api.search.return_value = []
        await search_query(self.message, self.state, self.api)
        self.state.clear.assert_not_awaited()
        self.api.search.side_effect = APIError("Ошибка сервера")
        await search_query(self.message, self.state, self.api)
        self.state.clear.assert_not_awaited()
        self.assertIn("Ошибка сервера", self.message.answer.call_args.args[0])

    async def test_non_text_and_oversized_input_do_not_call_api(self):
        for text in (None, " ", "x" * 2001, "/unknown"):
            self.message.text = text
            await search_query(self.message, self.state, self.api)
        self.api.search.assert_not_awaited()

    async def test_callback_uses_sender_identity(self):
        callback = AsyncMock(spec=CallbackQuery)
        callback.answer = AsyncMock()
        callback.data = "save:7"
        callback.from_user = User(id=123, is_bot=False, first_name="Test")
        callback.message = self.message
        await favorite_action(callback, self.api)
        self.api.save.assert_awaited_once_with(123, 7)
        callback.answer.assert_awaited_once()

    async def test_invalid_callback_does_not_call_backend(self):
        callback = AsyncMock(spec=CallbackQuery)
        callback.answer = AsyncMock()
        callback.data = "save:invalid"
        await favorite_action(callback, self.api)
        self.api.save.assert_not_awaited()
        callback.answer.assert_awaited_once()

    def test_recipe_escapes_html(self):
        result = recipe_text(Recipe(1, "<b>Яйца</b>", ["<сыр>"], ["A & B"]))
        self.assertIn("&lt;b&gt;", result)
        self.assertIn("&lt;сыр&gt;", result)
        self.assertIn("A &amp; B", result)


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_telegram_event_routing(self):
        # A real Dispatcher/FSM with only Telegram's network calls mocked.
        bot = Bot("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk")
        bot.session = AsyncMock()
        storage = MemoryStorage()
        dp = Dispatcher(storage=storage)
        dp.include_router(router)
        api = AsyncMock(spec=RecipesAPI)
        api.search.return_value = [Recipe(1, "Омлет", ["яйца"], ["Приготовить"])]
        api.favorites.return_value = []
        user = User(id=42, is_bot=False, first_name="Test")
        state = FSMContext(storage, StorageKey(bot_id=bot.id, chat_id=42, user_id=42))

        async def send(text):
            message = Message(
                message_id=1,
                date=datetime.now(UTC),
                chat=Chat(id=42, type="private"),
                from_user=user,
                text=text,
            )
            await dp.feed_update(bot, Update(update_id=1, message=message), api=api)

        try:
            await send("/start")
            self.assertIsNone(await state.get_state())
            await send("🔍 Найти рецепт")
            self.assertEqual(await state.get_state(), Search.query.state)
            await send("яйца")
            api.search.assert_awaited_once_with("яйца")
            self.assertIsNone(await state.get_state())
            await send("/search")
            await send("Отмена")
            self.assertIsNone(await state.get_state())
            await send("/search")
            await send("/favorites")
            api.favorites.assert_awaited_once_with(42)
            self.assertIsNone(await state.get_state())
            callback = CallbackQuery(
                id="test",
                from_user=user,
                chat_instance="test",
                data="save:1",
                message=Message(
                    message_id=2,
                    date=datetime.now(UTC),
                    chat=Chat(id=42, type="private"),
                    from_user=user,
                    text="Омлет",
                ),
            )
            await dp.feed_update(
                bot, Update(update_id=2, callback_query=callback), api=api
            )
            api.save.assert_awaited_once_with(42, 1)
        finally:
            await storage.close()
            await bot.session.close()


class DemoLaunchTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_command_launch_starts_and_stops_demo(self):
        from telegram_bot.__main__ import run

        async def fake_bot():
            async with aiohttp.ClientSession() as session:
                api = RecipesAPI(session, "http://127.0.0.1:8081")
                self.assertEqual([item.id for item in await api.search("яйца")], [1])

        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "demo.sqlite3")
            with (
                patch.dict("os.environ", {"BOT_TOKEN": "test"}),
                patch("telegram_bot.__main__.main", side_effect=fake_bot),
                patch(
                    "telegram_bot.__main__.create_app",
                    side_effect=lambda _: create_app(database),
                ),
            ):
                await run(True)
