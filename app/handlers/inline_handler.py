from __future__ import annotations

import logging

from aiogram import Router
from aiogram.enums import ButtonStyle
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
)

from app.services.proxy_links import ProxyStore

router = Router()
LOGGER = logging.getLogger(__name__)

VPN_BOT_URL = "https://t.me/noctovpn_bot?start=pxbt"


@router.inline_query()
async def inline_share(inline_query: InlineQuery, proxy_store: ProxyStore) -> None:
    proxies = proxy_store.load_enabled()

    if not proxies:
        await inline_query.answer(
            results=[
                InlineQueryResultArticle(
                    id="unavailable",
                    title="⚠️ Прокси временно недоступен",
                    description="Серверы скоро вернутся",
                    input_message_content=InputTextMessageContent(
                        message_text="⚠️ Прокси сейчас временно недоступен. Скоро вернётся!"
                    ),
                )
            ],
            cache_time=30,
        )
        return

    results: list[InlineQueryResultArticle] = []

    for idx, proxy in enumerate(proxies):
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(
                    text="⚡ Подключить прокси — 1 нажатие",
                    url=proxy.tme_link,
                    style=ButtonStyle.SUCCESS,
                )],
                [InlineKeyboardButton(
                    text="🚀 Попробовать NoctoVPN бесплатно",
                    url=VPN_BOT_URL,
                    style=ButtonStyle.PRIMARY,
                )],
            ]
        )
        results.append(
            InlineQueryResultArticle(
                id=f"proxy_{idx}",
                title=f"⚡ {proxy.name}",
                description="Бесплатный MTProto прокси — подключается одним нажатием",
                input_message_content=InputTextMessageContent(
                    message_text=(
                        "🔒 <b>Бесплатный прокси для Telegram</b>\n\n"
                        f"<b>{proxy.name}</b> — подключается за одно нажатие, без регистрации.\n\n"
                        "<i>Работает только в Telegram. Для Instagram и YouTube нужен VPN.</i>"
                    ),
                    parse_mode="HTML",
                ),
                reply_markup=keyboard,
            )
        )

    await inline_query.answer(results, cache_time=10, is_personal=True)
