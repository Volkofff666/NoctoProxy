from __future__ import annotations

from urllib.parse import quote

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.services.proxy_links import ProxyItem, ProxyStore
from app.services.storage import Storage

router = Router()


def build_start_keyboard(
    proxy_url: str,
    support_username: str,
    channel_url: str | None,
    show_admin_panel: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="✅ Подключить прокси", url=proxy_url)],
    ]

    secondary_buttons: list[InlineKeyboardButton] = [
        InlineKeyboardButton(text="📚 Все прокси", callback_data="user:proxies"),
        InlineKeyboardButton(text="📤 Поделиться", callback_data="user:share"),
        InlineKeyboardButton(text="📌 Инструкция", callback_data="user:instruction"),
        InlineKeyboardButton(text="💬 Поддержка", url=f"https://t.me/{support_username}"),
    ]
    if channel_url:
        secondary_buttons.append(InlineKeyboardButton(text="📣 Подписаться на канал", url=channel_url))
    if show_admin_panel:
        secondary_buttons.append(InlineKeyboardButton(text="🛠 Админ панель", callback_data="admin:menu"))

    for idx in range(0, len(secondary_buttons), 2):
        rows.append(secondary_buttons[idx:idx + 2])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_instruction_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="user:home")]]
    )


def build_proxy_list_keyboard(proxies: list[ProxyItem]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for idx, proxy in enumerate(proxies):
        rows.append([InlineKeyboardButton(text=f"✅ Подключить {proxy.name}", url=proxy.tme_link)])
        rows.append(
            [InlineKeyboardButton(text=f"📋 Скопировать tg:// ({proxy.name})", callback_data=f"copy_tg:{idx}")]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="user:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_invite_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="user:home")]]
    )


def build_share_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="user:home")]]
    )


def build_share_actions_keyboard(tme_link: str, tg_link: str) -> InlineKeyboardMarkup:
    share_text = (
        "Бесплатный Proxy для Telegram. "
        "Работает только для Telegram (не VPN)."
    )
    share_url = (
        f"https://t.me/share/url?url={quote(tme_link, safe='')}"
        f"&text={quote(share_text, safe='')}"
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📨 Отправить в чат", url=share_url)],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="user:home")],
        ]
    )


def _main_menu_text() -> str:
    return (
        "<b>Бесплатный Proxy для Telegram</b>\n\n"
        "Подходит только для Telegram (это <b>не VPN</b>) и не влияет на другие приложения.\n"
        "Выберите действие ниже:"
    )


async def _safe_edit(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    disable_web_page_preview: bool | None = None,
) -> None:
    try:
        await callback.message.edit_text(
            text,
            reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    proxy_store: ProxyStore,
    storage: Storage,
    support_username: str,
    channel_url: str | None,
    admin_ids: set[int],
) -> None:
    user = message.from_user
    await storage.touch_user(
        tg_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )

    enabled = proxy_store.load_enabled()
    if not enabled:
        support_url = f"https://t.me/{support_username}"
        text = (
            "Привет! Сейчас прокси временно недоступен. "
            f"Напишите в поддержку: {support_url}"
        )
        await message.answer(text)
        return

    main_proxy = enabled[0]
    keyboard = build_start_keyboard(
        main_proxy.tme_link,
        support_username,
        channel_url,
        show_admin_panel=user.id in admin_ids,
    )
    await message.answer(_main_menu_text(), reply_markup=keyboard)


@router.message(Command("invite"))
async def cmd_invite(message: Message, storage: Storage) -> None:
    user = message.from_user
    await storage.touch_user(
        tg_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )

    me = await message.bot.get_me()
    invite_link = f"https://t.me/{me.username}"
    await storage.record_share(user.id, source="cmd_invite")
    text = (
        "<b>Ссылка, чтобы поделиться ботом</b>\n"
        f"{invite_link}\n\n"
        "Отправьте ссылку друзьям, чтобы они могли быстро подключить Proxy."
    )
    await message.answer(text, reply_markup=build_invite_keyboard(), disable_web_page_preview=True)


@router.callback_query(F.data == "user:home")
async def cb_user_home(
    callback: CallbackQuery,
    proxy_store: ProxyStore,
    storage: Storage,
    support_username: str,
    channel_url: str | None,
    admin_ids: set[int],
) -> None:
    user = callback.from_user
    await storage.touch_user(
        tg_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )

    enabled = proxy_store.load_enabled()
    if not enabled:
        support_url = f"https://t.me/{support_username}"
        await _safe_edit(
            callback,
            "Сейчас прокси временно недоступен.\n"
            f"Поддержка: {support_url}",
        )
        await callback.answer()
        return

    main_proxy = enabled[0]
    keyboard = build_start_keyboard(
        main_proxy.tme_link,
        support_username,
        channel_url,
        show_admin_panel=user.id in admin_ids,
    )
    await _safe_edit(callback, _main_menu_text(), reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "user:instruction")
async def cb_instruction(
    callback: CallbackQuery,
    storage: Storage,
    support_username: str,
) -> None:
    user = callback.from_user
    await storage.touch_user(
        tg_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )

    text = (
        "<b>Как подключить прокси</b>\n\n"
        "1. Нажмите кнопку <b>Подключить</b> у нужного сервера.\n"
        "2. Telegram откроет экран добавления прокси.\n"
        "3. Подтвердите добавление и включите <b>Использовать прокси</b>.\n"
        "4. Рекомендуем включить <b>Автопереключение</b> в том же разделе.\n\n"
        f"<b>Поддержка:</b> https://t.me/{support_username}"
    )
    await _safe_edit(callback, text, reply_markup=build_instruction_keyboard())
    await callback.answer()


@router.callback_query(F.data == "user:proxies")
async def cb_user_proxies(
    callback: CallbackQuery,
    proxy_store: ProxyStore,
    storage: Storage,
    support_username: str,
) -> None:
    user = callback.from_user
    await storage.touch_user(
        tg_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )

    proxies = proxy_store.load_enabled()
    if not proxies:
        await _safe_edit(
            callback,
            "Сейчас прокси временно недоступен.\n"
            f"Поддержка: https://t.me/{support_username}",
            reply_markup=build_instruction_keyboard(),
        )
        await callback.answer()
        return

    lines = [
        "<b>Доступные прокси для Telegram</b>",
        "Добавьте несколько серверов и включите автопереключение в Telegram.",
        "",
    ]
    for idx, proxy in enumerate(proxies):
        lines.append(f"{idx + 1}. <b>{proxy.name}</b> | <code>{proxy.server}:{proxy.port}</code>")
    await _safe_edit(callback, "\n".join(lines), reply_markup=build_proxy_list_keyboard(proxies))
    await callback.answer()


@router.callback_query(F.data == "user:invite")
async def cb_user_invite(
    callback: CallbackQuery,
    storage: Storage,
) -> None:
    user = callback.from_user
    await storage.touch_user(
        tg_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )

    me = await callback.bot.get_me()
    invite_link = f"https://t.me/{me.username}"
    await storage.record_share(user.id, source="cb_invite")
    text = (
        "<b>Ссылка, чтобы поделиться ботом</b>\n"
        f"{invite_link}\n\n"
        "Отправьте ее друзьям."
    )
    await _safe_edit(
        callback,
        text,
        reply_markup=build_invite_keyboard(),
        disable_web_page_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data == "user:share")
async def cb_user_share(
    callback: CallbackQuery,
    proxy_store: ProxyStore,
    storage: Storage,
    support_username: str,
) -> None:
    user = callback.from_user
    await storage.touch_user(
        tg_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )

    proxies = proxy_store.load_enabled()
    if not proxies:
        await _safe_edit(
            callback,
            "Сейчас прокси временно недоступен.\n"
            f"Поддержка: https://t.me/{support_username}",
            reply_markup=build_share_keyboard(),
        )
        await callback.answer()
        return

    proxy = proxies[0]
    await storage.record_share(user.id, source="cb_share")
    tg_link = proxy.tg_link
    tme_link = proxy.tme_link
    text = (
        "<b>Поделитесь этим прокси:</b>\n"
        "Бесплатный Proxy для Telegram.\n\n"
        f"<b>tg:// ссылка:</b> {tg_link}\n"
        f"<b>Подключить в 1 тап:</b> {tme_link}"
    )
    await _safe_edit(
        callback,
        text,
        reply_markup=build_share_actions_keyboard(tme_link, tg_link),
        disable_web_page_preview=True,
    )
    await callback.answer()
