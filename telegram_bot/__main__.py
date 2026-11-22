"""Convenient entry point: python -m telegram_bot --demo."""

import argparse
import asyncio
import logging
import os
from pathlib import Path

from aiohttp import web
from dotenv import load_dotenv

from .bot import main
from .demo_api import create_app


async def run(demo: bool):
    load_dotenv(Path(__file__).with_name(".env"))
    if not os.getenv("BOT_TOKEN"):
        raise RuntimeError("Сначала впиши BOT_TOKEN в telegram_bot/.env (см. .env.example)")
    if not demo:
        await main()
        return
    runner = web.AppRunner(create_app(str(Path(__file__).with_name("demo.sqlite3"))))
    await runner.setup()
    try:
        await web.TCPSite(runner, "127.0.0.1", 8081).start()
        os.environ["API_BASE_URL"] = "http://127.0.0.1:8081"
        logging.getLogger(__name__).info(
            "ДЕМО: локальный сервер, пять тестовых рецептов. Остановка: Ctrl+C."
        )
        await main()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ЯЕда — поиск и избранное")
    parser.add_argument("--demo", action="store_true", help="Запустить вместе с тестовым сервером")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run(args.demo))
    except (RuntimeError, OSError, ValueError) as error:
        parser.exit(1, f"Не удалось запустить: {error}\n")
