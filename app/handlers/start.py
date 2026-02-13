from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from urllib.parse import quote

from app.services.proxy_links import ProxyItem, ProxyStore
from app.services.storage import Storage

router = Router()


def build_start_keyboard(
    proxy_url: str,
    support_username: str,
    tribute_url: str | None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="✅ Подключить прокси", url=proxy_url)],
        [InlineKeyboardButton(text="📤 Поделиться", callback_data="user:share")],
        [InlineKeyboardButton(text="📌 Инструкция", callback_data="user:instruction")],
        [
            InlineKeyboardButton(
                text="💬 Поддержка",
                url=f"https://t.me/{support_username}",
            )
        ]
    ]
    if tribute_url:
        rows.append([InlineKeyboardButton(text="❤️ Поддержать проект", url=tribute_url)])
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
        "Бесплатный MTProto прокси для Telegram.\n"
        "Подходит только для Telegram (не VPN).\n"
        f"tg:// ссылка: {tg_link}"
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


def _extract_referrer(command: CommandObject | None, self_id: int) -> int | None:
    if not command or not command.args:
        return None

    args = command.args.strip()
    if not args.startswith("ref_"):
        return None

    raw_id = args.removeprefix("ref_")
    if not raw_id.isdigit():
        return None

    inviter_id = int(raw_id)
    if inviter_id == self_id:
        return None
    return inviter_id


def _main_menu_text() -> str:
    return (
        "<b>Полностью бесплатный MTProto прокси для Telegram.</b>\n"
        "Работает только для Telegram (это не VPN), чтобы Telegram оставался доступен всегда.\n\n"
        "Выберите действие:"
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
    command: CommandObject | None,
    proxy_store: ProxyStore,
    storage: Storage,
    support_username: str,
    tribute_url: str | None,
) -> None:
    user = message.from_user
    referrer_id = _extract_referrer(command, user.id)
    await storage.touch_user(
        tg_id=user.id,
        invited_by=referrer_id,
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
    keyboard = build_start_keyboard(main_proxy.tme_link, support_username, tribute_url)
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
    invite_link = f"https://t.me/{me.username}?start=ref_{user.id}"
    invited_count = await storage.count_invited_by(user.id)
    text = (
        "Ваша ссылка для приглашения:\n"
        f"{invite_link}\n\n"
        f"Вы пригласили: {invited_count}\n\n"
        "Поделитесь ссылкой с друзьями."
    )
    await message.answer(text, reply_markup=build_invite_keyboard(), disable_web_page_preview=True)


@router.callback_query(F.data == "user:home")
async def cb_user_home(
    callback: CallbackQuery,
    proxy_store: ProxyStore,
    storage: Storage,
    support_username: str,
    tribute_url: str | None,
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
    keyboard = build_start_keyboard(main_proxy.tme_link, support_username, tribute_url)
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
        "<b>Как включить прокси:</b>\n"
        "1. Откройте Telegram -> Настройки -> Данные и память -> Прокси.\n"
        "2. Добавьте адрес через кнопку подключения или вставьте tg:// ссылку.\n"
        "3. Включите Использовать прокси.\n"
        "4. В этом же разделе включите авто-переключение прокси.\n\n"
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
        "Бесплатные прокси для Telegram:",
        "Можно добавить как дополнительный адрес и включить авто-переключение.",
        "",
    ]
    for idx, proxy in enumerate(proxies):
        lines.append(f"{idx + 1}. {proxy.name} | {proxy.server}:{proxy.port}")
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
    invite_link = f"https://t.me/{me.username}?start=ref_{user.id}"
    invited_count = await storage.count_invited_by(user.id)
    text = (
        "Ваша ссылка для приглашения:\n"
        f"{invite_link}\n\n"
        f"Вы пригласили: {invited_count}"
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
    tg_link = proxy.tg_link
    tme_link = proxy.tme_link
    text = (
        "<b>Поделитесь этим прокси:</b>\n"
        "Бесплатный MTProto прокси для Telegram.\n"
        f"tg:// ссылка: {tg_link}\n"
        f"Подключить в 1 тап: {tme_link}"
    )
    await _safe_edit(
        callback,
        text,
        reply_markup=build_share_actions_keyboard(tme_link, tg_link),
        disable_web_page_preview=True,
    )
    await callback.answer()
