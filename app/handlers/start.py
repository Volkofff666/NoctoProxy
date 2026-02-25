from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote

from aiogram import Bot, F, Router
from aiogram.enums import ButtonStyle, ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.services.proxy_links import ProxyItem, ProxyStore
from app.services.storage import Storage

router = Router()
VPN_BOT_URL = "https://t.me/noctovpn_bot"
LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Keyboard builders
# ---------------------------------------------------------------------------

def build_start_keyboard(
    proxy_url: str,
    support_username: str,
    channel_url: str | None,
    show_admin_panel: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="Подключить прокси", url=proxy_url, style=ButtonStyle.SUCCESS)],
        [InlineKeyboardButton(text="🚀 Попробовать VPN", url=VPN_BOT_URL)],
    ]

    secondary_buttons: list[InlineKeyboardButton] = [
        InlineKeyboardButton(text="📚 Все прокси", callback_data="user:proxies"),
        InlineKeyboardButton(text="📤 Поделиться", callback_data="user:share"),
        InlineKeyboardButton(text="ℹ️ О VPN", callback_data="user:vpn_info"),
        InlineKeyboardButton(text="👥 Пригласить друга", callback_data="user:referral"),
        InlineKeyboardButton(text="Инструкция", callback_data="user:instruction"),
        InlineKeyboardButton(text="Поддержка", url=f"https://t.me/{support_username}"),
    ]
    if channel_url:
        secondary_buttons.append(InlineKeyboardButton(text="📣 Подписаться на канал", url=channel_url))
    if show_admin_panel:
        secondary_buttons.append(InlineKeyboardButton(text="🛠 Админ панель", callback_data="admin:menu"))

    for idx in range(0, len(secondary_buttons), 2):
        rows.append(secondary_buttons[idx:idx + 2])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_subscribe_gate_keyboard(channel_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📣 Подписаться на канал", url=channel_url)],
            [InlineKeyboardButton(text="✅ Я подписался", callback_data="user:check_sub")],
        ]
    )


def build_instruction_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="user:home")]]
    )


def build_vpn_info_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Открыть VPN-бот", url=VPN_BOT_URL)],
            [InlineKeyboardButton(text="📋 Скопировать промокод", callback_data="user:vpn_promo")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="user:home")],
        ]
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


def build_referral_keyboard(share_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📤 Поделиться ботом", url=share_url)],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="user:home")],
        ]
    )


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


def build_channel_reminder_keyboard(channel_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📣 Подписаться на канал", url=channel_url, style=ButtonStyle.SUCCESS)]
        ]
    )


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _main_menu_text() -> str:
    return (
        "<b>Бесплатный Proxy для Telegram</b>\n\n"
        "Прокси работает <b>только внутри Telegram</b> — другие приложения и сайты он не разблокирует.\n"
        "Если нужен полноценный VPN для Instagram, YouTube и любых сайтов — нажмите «🚀 Попробовать VPN».\n\n"
        "Выберите действие:"
    )


def _subscribe_gate_text() -> str:
    return (
        "Прокси бесплатный, но чтобы получить доступ — нужно подписаться на наш канал.\n\n"
        "Там публикуем обновления серверов, новости про блокировки и статусы работы прокси — "
        "так вы всегда будете в курсе."
    )


# ---------------------------------------------------------------------------
# Subscription check
# ---------------------------------------------------------------------------

async def _check_subscribed(bot: Bot, channel_id: str, user_id: int) -> bool:
    """Return True if user is subscribed to the channel. Fails open on API errors."""
    try:
        member = await bot.get_chat_member(channel_id, user_id)
        return member.status in {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        }
    except Exception:
        LOGGER.warning(
            "Could not check subscription for user %s in channel %s — allowing access",
            user_id,
            channel_id,
        )
        return True  # fail open


# ---------------------------------------------------------------------------
# Delayed send helpers
# ---------------------------------------------------------------------------

async def _send_vpn_promo(
    bot: Bot,
    tg_id: int,
    vpn_promo_code: str,
    vpn_promo_bonus_days: int,
    delay_seconds: int,
) -> None:
    await asyncio.sleep(delay_seconds)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Попробовать VPN бесплатно", url=VPN_BOT_URL)],
        ]
    )
    try:
        await bot.send_message(
            tg_id,
            (
                "Кстати, у нас есть <b>VPN</b> — он работает не только в Telegram, "
                "но и в Instagram, YouTube и любых других приложениях.\n\n"
                "Первые <b>1 сутки бесплатно</b>, никакого риска.\n"
                f"С промокодом <code>{vpn_promo_code}</code> — бонус ещё +{vpn_promo_bonus_days} дня.\n"
                "Потом всего <b>179 ₽/мес</b>.\n\n"
                "Попробуйте — вдруг понравится 😉"
            ),
            reply_markup=keyboard,
        )
    except Exception:
        LOGGER.exception("Failed to send VPN promo to user %s", tg_id)


async def _send_vpn_promo_final(
    bot: Bot,
    tg_id: int,
    vpn_promo_code: str,
    vpn_promo_bonus_days: int,
    delay_seconds: int,
) -> None:
    await asyncio.sleep(delay_seconds)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Попробовать VPN — 1 сутки бесплатно", url=VPN_BOT_URL)],
        ]
    )
    try:
        await bot.send_message(
            tg_id,
            (
                "Прокси работает? Надеемся, что да 👍\n\n"
                "Напоминаем: если нужен <b>полный VPN</b> для всех приложений и сайтов — "
                "Instagram, YouTube, любые сервисы — у нас есть @noctovpn_bot.\n\n"
                "Триал <b>1 сутки бесплатно</b>. "
                f"Промокод <code>{vpn_promo_code}</code> даёт ещё +{vpn_promo_bonus_days} дня.\n"
                "Цена после триала — <b>179 ₽/мес</b>."
            ),
            reply_markup=keyboard,
        )
    except Exception:
        LOGGER.exception("Failed to send final VPN promo to user %s", tg_id)


async def _send_channel_reminder(
    bot: Bot,
    tg_id: int,
    channel_url: str,
    delay_seconds: int,
) -> None:
    await asyncio.sleep(delay_seconds)
    try:
        await bot.send_message(
            tg_id,
            (
                "💡 У нас есть канал — там публикуем:\n"
                "• статус серверов если что-то упало\n"
                "• новые прокси когда добавляем\n"
                "• новости о блокировках\n\n"
                "Подпишитесь, чтобы всегда знать актуальное состояние прокси 👇"
            ),
            reply_markup=build_channel_reminder_keyboard(channel_url),
            disable_web_page_preview=True,
        )
    except Exception:
        LOGGER.exception("Failed to send channel reminder to user %s", tg_id)


# ---------------------------------------------------------------------------
# Safe edit helper
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    proxy_store: ProxyStore,
    storage: Storage,
    support_username: str,
    channel_url: str | None,
    channel_id: str | None,
    admin_ids: set[int],
    channel_reminder_delay_sec: int,
    vpn_onboarding_delay_sec: int,
    vpn_onboarding_final_delay_sec: int,
    vpn_promo_code: str,
    vpn_promo_bonus_days: int,
) -> None:
    user = message.from_user
    is_new_user = await storage.touch_user(
        tg_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )

    # Parse referral argument: /start ref_123456
    args = (command.args or "").strip()
    if is_new_user and args.startswith("ref_"):
        try:
            referrer_id = int(args[4:])
            await storage.set_referrer(user.id, referrer_id)
        except ValueError:
            pass

    enabled = proxy_store.load_enabled()

    # Proxy unavailable
    if not enabled:
        support_url = f"https://t.me/{support_username}"
        unavail_text = (
            "Привет! 👋\n\n"
            "Прокси сейчас временно недоступен — мы уже в курсе и работаем над этим.\n"
            f"Если вопрос срочный, напишите в поддержку: {support_url}"
        )
        if channel_url:
            unavail_text += f"\n\nСледите за статусом в нашем канале: {channel_url}"
        await message.answer(unavail_text, disable_web_page_preview=True)
        return

    # Subscribe-gate (only when CHANNEL_ID is configured)
    if channel_id:
        subscribed = await _check_subscribed(message.bot, channel_id, user.id)
        if not subscribed:
            await message.answer(
                _subscribe_gate_text(),
                reply_markup=build_subscribe_gate_keyboard(channel_url or ""),
            )
            # Start VPN onboarding for new users even when gated
            if is_new_user:
                if vpn_onboarding_delay_sec > 0:
                    asyncio.create_task(
                        _send_vpn_promo(
                            message.bot, user.id,
                            vpn_promo_code, vpn_promo_bonus_days,
                            vpn_onboarding_delay_sec,
                        )
                    )
                if vpn_onboarding_final_delay_sec > 0:
                    asyncio.create_task(
                        _send_vpn_promo_final(
                            message.bot, user.id,
                            vpn_promo_code, vpn_promo_bonus_days,
                            vpn_onboarding_final_delay_sec,
                        )
                    )
            return

    # Show main menu
    main_proxy = enabled[0]
    keyboard = build_start_keyboard(
        main_proxy.tme_link,
        support_username,
        channel_url,
        show_admin_panel=user.id in admin_ids,
    )
    await message.answer(_main_menu_text(), reply_markup=keyboard)

    # Onboarding for new users
    if is_new_user:
        # Channel promotion — only without gate (with gate they already subscribed)
        if channel_url and not channel_id:
            await message.answer(
                "💡 <b>Полезный совет:</b> подпишитесь на наш канал — "
                "там первыми узнаете если сервер упадёт или появятся новые прокси.\n\n"
                "Так не придётся гадать почему прокси вдруг перестал работать.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="📣 Подписаться", url=channel_url)]]
                ),
                disable_web_page_preview=True,
            )
            if channel_reminder_delay_sec > 0:
                asyncio.create_task(
                    _send_channel_reminder(
                        message.bot, user.id, channel_url, channel_reminder_delay_sec,
                    )
                )

        # VPN onboarding chain
        if vpn_onboarding_delay_sec > 0:
            asyncio.create_task(
                _send_vpn_promo(
                    message.bot, user.id,
                    vpn_promo_code, vpn_promo_bonus_days,
                    vpn_onboarding_delay_sec,
                )
            )
        if vpn_onboarding_final_delay_sec > 0:
            asyncio.create_task(
                _send_vpn_promo_final(
                    message.bot, user.id,
                    vpn_promo_code, vpn_promo_bonus_days,
                    vpn_onboarding_final_delay_sec,
                )
            )


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


@router.callback_query(F.data.in_({"user:home", "home"}))
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


@router.callback_query(F.data == "user:check_sub")
async def cb_check_sub(
    callback: CallbackQuery,
    proxy_store: ProxyStore,
    storage: Storage,
    support_username: str,
    channel_url: str | None,
    channel_id: str | None,
    admin_ids: set[int],
) -> None:
    user = callback.from_user
    await storage.touch_user(
        tg_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )

    # Re-check subscription
    if channel_id:
        subscribed = await _check_subscribed(callback.bot, channel_id, user.id)
        if not subscribed:
            await callback.answer(
                "Подписка не обнаружена — подпишитесь и попробуйте снова 😊",
                show_alert=True,
            )
            return

    # Subscription confirmed (or gate is now disabled) — show home menu
    enabled = proxy_store.load_enabled()
    if not enabled:
        support_url = f"https://t.me/{support_username}"
        await _safe_edit(
            callback,
            "Привет! 👋\n\n"
            "Прокси сейчас временно недоступен — мы уже в курсе и работаем над этим.\n"
            f"Поддержка: {support_url}",
        )
        await callback.answer("Спасибо за подписку!")
        return

    main_proxy = enabled[0]
    keyboard = build_start_keyboard(
        main_proxy.tme_link,
        support_username,
        channel_url,
        show_admin_panel=user.id in admin_ids,
    )
    await _safe_edit(callback, _main_menu_text(), reply_markup=keyboard)
    await callback.answer("Отлично, доступ открыт! 🎉")


@router.callback_query(F.data.in_({"user:instruction", "instruction"}))
async def cb_instruction(
    callback: CallbackQuery,
    storage: Storage,
    support_username: str,
    channel_url: str | None,
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
    if channel_url:
        text += f"\n\n<b>Актуальные серверы и статусы — в нашем канале:</b> {channel_url}"
    await _safe_edit(callback, text, reply_markup=build_instruction_keyboard(), disable_web_page_preview=True)
    await callback.answer()


@router.callback_query(F.data == "user:vpn_info")
async def cb_vpn_info(
    callback: CallbackQuery,
    storage: Storage,
    vpn_promo_code: str,
    vpn_promo_bonus_days: int,
) -> None:
    user = callback.from_user
    await storage.touch_user(
        tg_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )

    text = (
        "<b>NoctoVPN — быстрый VPN для всего</b>\n\n"
        "В отличие от прокси, VPN работает во <b>всех приложениях и браузерах</b>:\n"
        "Instagram, YouTube, любые сайты и сервисы — всё без ограничений.\n\n"
        "• <b>179 ₽ / месяц</b>\n"
        "• <b>1 сутки</b> — бесплатный пробный период, без риска\n"
        "• Серверы до <b>10 Gbit</b>\n\n"
        f"<b>Промокод:</b> <code>{vpn_promo_code}</code>\n"
        f"Даёт +{vpn_promo_bonus_days} дня к пробному периоду — итого <b>4 дня бесплатно</b>.\n\n"
        "Попробуйте 1 сутки бесплатно — нажмите кнопку ниже."
    )
    await _safe_edit(callback, text, reply_markup=build_vpn_info_keyboard())
    await callback.answer()


@router.callback_query(F.data == "user:vpn_promo")
async def cb_vpn_promo(
    callback: CallbackQuery,
    storage: Storage,
    vpn_promo_code: str,
    vpn_promo_bonus_days: int,
) -> None:
    user = callback.from_user
    await storage.touch_user(
        tg_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )

    await callback.message.answer(
        (
            "<b>Ваш промокод для VPN:</b>\n"
            f"<code>{vpn_promo_code}</code>\n\n"
            f"Бонус: +{vpn_promo_bonus_days} дня к пробной подписке."
        )
    )
    await callback.answer("Промокод отправлен")


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
        f"<b>Подключить в 1 тап:</b> {tme_link}\n\n"
        "<i>Нужен VPN для всех сайтов и приложений? → @noctovpn_bot</i>"
    )
    await _safe_edit(
        callback,
        text,
        reply_markup=build_share_actions_keyboard(tme_link, tg_link),
        disable_web_page_preview=True,
    )
    await callback.answer()


async def _get_referral_text_and_keyboard(
    bot: Bot,
    user_id: int,
    ref_count: int,
    channel_url: str | None,
) -> tuple[str, InlineKeyboardMarkup]:
    me = await bot.get_me()
    ref_link = f"https://t.me/{me.username}?start=ref_{user_id}"
    share_text = "Бесплатный MTProto прокси для Telegram — быстро и без регистрации."
    if channel_url:
        share_text += f" Актуальные серверы и статусы: {channel_url}"
    share_url = (
        f"https://t.me/share/url?url={quote(ref_link, safe='')}"
        f"&text={quote(share_text, safe='')}"
    )
    text = (
        "<b>Пригласить друга</b>\n\n"
        "Приглашай друзей — помогай проекту расти 💪\n\n"
        "Отправьте эту ссылку друзьям — они получат бесплатный прокси для Telegram:\n\n"
        f"<code>{ref_link}</code>\n\n"
        f"Вы уже пригласили: <b>{ref_count}</b> чел."
    )
    keyboard = build_referral_keyboard(share_url)
    return text, keyboard


@router.message(Command("referral"))
async def cmd_referral(
    message: Message,
    storage: Storage,
    channel_url: str | None,
) -> None:
    user = message.from_user
    await storage.touch_user(
        tg_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )
    ref_count = await storage.count_referrals(user.id)
    text, keyboard = await _get_referral_text_and_keyboard(message.bot, user.id, ref_count, channel_url)
    await message.answer(text, reply_markup=keyboard, disable_web_page_preview=True)


@router.callback_query(F.data == "user:referral")
async def cb_user_referral(
    callback: CallbackQuery,
    storage: Storage,
    channel_url: str | None,
) -> None:
    user = callback.from_user
    await storage.touch_user(
        tg_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )
    ref_count = await storage.count_referrals(user.id)
    text, keyboard = await _get_referral_text_and_keyboard(callback.bot, user.id, ref_count, channel_url)
    await _safe_edit(callback, text, reply_markup=keyboard, disable_web_page_preview=True)
    await callback.answer()
