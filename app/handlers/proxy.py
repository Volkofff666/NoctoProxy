from __future__ import annotations

import logging
from urllib.parse import quote

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.handlers.start import _check_subscribed, build_subscribe_gate_keyboard
from app.services.proxy_links import ProxyStore
from app.services.rate_limit import InMemoryRateLimiter
from app.services.storage import Storage

router = Router()
LOGGER = logging.getLogger(__name__)


def build_proxy_keyboard(index: int, name: str, tme_link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"⚡ Подключить {name}", url=tme_link)],
            [InlineKeyboardButton(text="⬅️ В меню", callback_data="user:home")],
        ]
    )


@router.message(Command("proxy"))
async def cmd_proxy(
    message: Message,
    proxy_store: ProxyStore,
    storage: Storage,
    rate_limiter: InMemoryRateLimiter,
    support_username: str,
    channel_id: str | None,
    channel_url: str | None,
) -> None:
    user = message.from_user
    await storage.touch_user(
        tg_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )

    allowed, retry_after = rate_limiter.allowed(user.id)
    if not allowed:
        await message.answer(f"Слишком часто. Попробуйте снова через {retry_after} сек.")
        return

    proxies = proxy_store.load_enabled()
    if not proxies:
        await message.answer(
            "Сейчас прокси временно недоступен. "
            f"Поддержка: https://t.me/{support_username}"
        )
        return

    # Subscribe-gate check
    if channel_id and channel_url:
        subscribed = await _check_subscribed(message.bot, channel_id, user.id)
        if not subscribed:
            await message.answer(
                "Чтобы получить прокси — подпишитесь на наш канал.",
                reply_markup=build_subscribe_gate_keyboard(channel_url),
            )
            return

    intro = (
        "🔒 <b>Прокси для Telegram</b>\n\n"
        "Добавьте <b>все серверы</b> и включите <b>автопереключение</b> — "
        "если один упадёт, Telegram сам перейдёт на следующий."
    )
    await message.answer(intro)

    for idx, proxy in enumerate(proxies):
        text = (
            f"<b>{idx + 1}. {proxy.name}</b>\n"
            f"<code>{proxy.server}:{proxy.port}</code>"
        )
        keyboard = build_proxy_keyboard(idx, proxy.name, proxy.tme_link)
        await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("copy_tg:"))
async def cb_copy_tg(
    callback: CallbackQuery,
    proxy_store: ProxyStore,
    storage: Storage,
) -> None:
    parts = callback.data.split(":", maxsplit=1)
    index = int(parts[1])

    proxies = proxy_store.load_enabled()
    if index < 0 or index >= len(proxies):
        await callback.answer("Прокси не найден", show_alert=True)
        return

    user = callback.from_user
    await storage.touch_user(
        tg_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )
    proxy = proxies[index]
    await callback.message.answer(f"tg:// ссылка для {proxy.name}:\n{proxy.tg_link}")
    await callback.answer("Отправил tg:// ссылку")


@router.message(Command("share"))
async def cmd_share(
    message: Message,
    proxy_store: ProxyStore,
    storage: Storage,
    support_username: str,
) -> None:
    user = message.from_user
    await storage.touch_user(
        tg_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )

    proxies = proxy_store.load_enabled()
    if not proxies:
        await message.answer(
            "Сейчас прокси временно недоступен. "
            f"Поддержка: https://t.me/{support_username}"
        )
        return

    proxy = proxies[0]
    await storage.record_share(user.id, source="cmd_share")
    share_text = (
        "Бесплатный Proxy для Telegram. "
        "Работает только для Telegram (не VPN)."
    )
    share_url = (
        f"https://t.me/share/url?url={quote(proxy.tme_link, safe='')}"
        f"&text={quote(share_text, safe='')}"
    )
    text = (
        "<b>Поделитесь этим прокси:</b>\n"
        "Бесплатный Proxy для Telegram.\n\n"
        f"<b>tg:// ссылка:</b> {proxy.tg_link}\n"
        f"<b>Подключить в 1 тап:</b> {proxy.tme_link}"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📨 Отправить в чат", url=share_url)],
            [InlineKeyboardButton(text="📋 Скопировать tg://", callback_data="copy_tg:0")],
            [InlineKeyboardButton(text="✅ Подключить", url=proxy.tme_link)],
            [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="user:home")],
        ]
    )
    await message.answer(text, reply_markup=keyboard, disable_web_page_preview=True)
