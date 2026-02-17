from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from dotenv import load_dotenv
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.handlers import (
    admin_router,
    donate_router,
    fallback_router,
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
    vpn_promo_code = os.getenv("VPN_PROMO_CODE", "NOCTO3")
    vpn_promo_bonus_days = int(os.getenv("VPN_PROMO_BONUS_DAYS", "3"))
    channel_url_raw = os.getenv("CHANNEL_URL", "").strip()
    channel_url = channel_url_raw if channel_url_raw else None
    channel_reminder_delay_sec = int(os.getenv("CHANNEL_REMINDER_DELAY_SEC", "1800"))
    broadcast_workers = int(os.getenv("BROADCAST_WORKERS", "20"))
    tribute_url_raw = os.getenv("TRIBUTE_URL", "").strip()
    tribute_url = tribute_url_raw if tribute_url_raw else None
    db_path = os.getenv("DB_PATH", "bot.db")
    proxies_path = os.getenv("PROXIES_PATH", "config/proxies.json")
    admin_ids = parse_admin_ids(os.getenv("ADMIN_IDS"))
    redis_url = (os.getenv("REDIS_URL") or "").strip()

    if not bot_token:
        raise RuntimeError("BOT_TOKEN is required")

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    storage = Storage(db_path)
    await storage.init()

    proxy_store = ProxyStore(proxies_path)
    rate_limiter = InMemoryRateLimiter(cooldown_seconds=3)
    redis_client: Redis | None = None
    fsm_storage = MemoryStorage()
    if redis_url:
        try:
            redis_client = Redis.from_url(redis_url, encoding="utf-8", decode_responses=True)
            await redis_client.ping()
            fsm_storage = RedisStorage(redis=redis_client)
            logging.info("FSM storage: Redis (%s)", redis_url)
        except RedisError as exc:
            logging.warning(
                "Redis is unavailable (%s). Falling back to in-memory FSM storage.",
                exc,
            )
            if redis_client is not None:
                await redis_client.aclose()
                redis_client = None
    else:
        logging.info("FSM storage: Memory (REDIS_URL is empty)")

    bot = Bot(
        token=bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=fsm_storage)

    dp.include_router(start_router)
    dp.include_router(proxy_router)
    dp.include_router(help_router)
    dp.include_router(donate_router)
    dp.include_router(admin_router)
    dp.include_router(fallback_router)

    dp.workflow_data.update(
        {
            "storage": storage,
            "proxy_store": proxy_store,
            "rate_limiter": rate_limiter,
            "support_username": support_username,
            "vpn_promo_code": vpn_promo_code,
            "vpn_promo_bonus_days": vpn_promo_bonus_days,
            "channel_url": channel_url,
            "channel_reminder_delay_sec": channel_reminder_delay_sec,
            "broadcast_workers": broadcast_workers,
            "tribute_url": tribute_url,
            "admin_ids": admin_ids,
        }
    )

    await bot.delete_webhook(drop_pending_updates=False)
    try:
        await dp.start_polling(bot)
    finally:
        if redis_client is not None:
            await redis_client.aclose()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(main())
