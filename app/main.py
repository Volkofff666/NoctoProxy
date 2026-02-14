from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dotenv import load_dotenv

from app.handlers import (
    admin_router,
    donate_router,
    help_router,
    proxy_router,
    start_router,
)
from app.services.proxy_links import ProxyStore
from app.services.rate_limit import InMemoryRateLimiter
from app.services.storage import Storage


def parse_admin_ids(raw: str | None) -> set[int]:
    if not raw:
        return set()
    result: set[int] = set()
    for chunk in raw.split(","):
        item = chunk.strip()
        if not item:
            continue
        result.add(int(item))
    return result


async def main() -> None:
    load_dotenv()

    bot_token = os.getenv("BOT_TOKEN")
    support_username = os.getenv("SUPPORT_USERNAME", "nocto_support")
    channel_url_raw = os.getenv("CHANNEL_URL", "").strip()
    channel_url = channel_url_raw if channel_url_raw else None
    tribute_url_raw = os.getenv("TRIBUTE_URL", "").strip()
    tribute_url = tribute_url_raw if tribute_url_raw else None
    db_path = os.getenv("DB_PATH", "bot.db")
    proxies_path = os.getenv("PROXIES_PATH", "config/proxies.json")
    admin_ids = parse_admin_ids(os.getenv("ADMIN_IDS"))

    if not bot_token:
        raise RuntimeError("BOT_TOKEN is required")

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    storage = Storage(db_path)
    await storage.init()

    proxy_store = ProxyStore(proxies_path)
    rate_limiter = InMemoryRateLimiter(cooldown_seconds=3)

    bot = Bot(
        token=bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    dp.include_router(start_router)
    dp.include_router(proxy_router)
    dp.include_router(help_router)
    dp.include_router(donate_router)
    dp.include_router(admin_router)

    dp.workflow_data.update(
        {
            "storage": storage,
            "proxy_store": proxy_store,
            "rate_limiter": rate_limiter,
            "support_username": support_username,
            "channel_url": channel_url,
            "tribute_url": tribute_url,
            "admin_ids": admin_ids,
        }
    )

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(main())
