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
VPN_BOT_URL = "https://t.me/noctovpn_bot?start=pxbt"
LOGGER = logging.getLogger(__name__)


def _greet(first_name: str) -> str:
    """Return 'Name, ' prefix or empty string if name is absent."""
    name = (first_name or "").strip()
    return f"{name}, " if name else ""


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
        [InlineKeyboardButton(text="⚡ Подключить прокси", url=proxy_url, style=ButtonStyle.SUCCESS)],
        [InlineKeyboardButton(text="🚀 Попробовать NoctoVPN бесплатно", url=VPN_BOT_URL, style=ButtonStyle.PRIMARY)],
        [
            InlineKeyboardButton(text="📡 Все серверы", callback_data="user:proxies"),
            InlineKeyboardButton(text="📖 Как подключить", callback_data="user:instruction"),
        ],
        [
            InlineKeyboardButton(text="ℹ️ О VPN", callback_data="user:vpn_info"),
            InlineKeyboardButton(text="👥 Пригласить друга", callback_data="user:referral"),
        ],
    ]

    bottom: list[InlineKeyboardButton] = [
        InlineKeyboardButton(text="💬 Поддержка", url=f"https://t.me/{support_username}"),
    ]
    if channel_url:
        bottom.append(InlineKeyboardButton(text="📣 Наш канал", url=channel_url))
    rows.append(bottom)

    if show_admin_panel:
        rows.append([InlineKeyboardButton(text="🛠 Панель", callback_data="admin:menu")])

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
        inline_keyboard=[
            [InlineKeyboardButton(text="📡 Перейти к серверам", callback_data="user:proxies")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="user:home")],
        ]
    )


def build_vpn_info_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Попробовать бесплатно — 3 дня", url=VPN_BOT_URL)],
            [InlineKeyboardButton(text="🎁 Получить 3 дня бесплатно", callback_data="user:vpn_promo")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="user:home")],
        ]
    )


def build_proxy_list_keyboard(proxies: list[ProxyItem]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for proxy in proxies:
        rows.append([InlineKeyboardButton(text=f"✅ Подключить {proxy.name}", url=proxy.tme_link)])
    rows.append([InlineKeyboardButton(text="🚀 NoctoVPN — открывает Instagram и YouTube", url=VPN_BOT_URL)])
    rows.append([
        InlineKeyboardButton(text="📖 Инструкция", callback_data="user:instruction"),
        InlineKeyboardButton(text="⬅️ Назад", callback_data="user:home"),
    ])
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


def build_share_actions_keyboard(tme_link: str) -> InlineKeyboardMarkup:
    share_text = (
        "Бесплатный прокси для Telegram — подключается за 1 нажатие, без регистрации."
    )
    share_url = (
        f"https://t.me/share/url?url={quote(tme_link, safe='')}"
        f"&text={quote(share_text, safe='')}"
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📨 Отправить другу", url=share_url)],
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
        "🔒 <b>Бесплатный прокси для Telegram</b>\n\n"
        "Нажмите <b>«⚡ Подключить прокси»</b> — Telegram сам добавит сервер за один шаг.\n\n"
        "📌 <b>Instagram и YouTube всё равно не работают?</b>\n"
        "Прокси — только для Telegram. Для всего остального нужен VPN.\n"
        "Попробуйте NoctoVPN — <b>3 дня бесплатно</b> 👆"
    )


def _subscribe_gate_text() -> str:
    return (
        "👋 Привет!\n\n"
        "Прокси бесплатный — чтобы получить доступ, подпишитесь на наш канал.\n\n"
        "Это займёт 5 секунд, а взамен вы получаете:\n"
        "• 🔔 Оповещение, если сервер упал — не будете гадать почему не работает\n"
        "• 🆕 Новые прокси раньше всех\n"
        "• 📡 Статус серверов в реальном времени\n\n"
        "Подписались? Нажмите кнопку ниже 👇"
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
    first_name: str = "",
) -> None:
    await asyncio.sleep(delay_seconds)
    g = _greet(first_name)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Попробовать VPN — 3 дня бесплатно", url=VPN_BOT_URL)],
        ]
    )
    try:
        await bot.send_message(
            tg_id,
            (
                f"{g}Instagram, YouTube у вас открываются? 🤔\n\n"
                "Прокси работает <b>только внутри Telegram</b>. "
                "Для всех остальных приложений и сайтов нужен VPN.\n\n"
                "У нас есть @noctovpn_bot — по ссылке дадут <b>3 дня доступа бесплатно</b>, без карты.\n\n"
                "Попробуйте — терять нечего 👇"
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
    first_name: str = "",
) -> None:
    await asyncio.sleep(delay_seconds)
    g = _greet(first_name)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Попробовать NoctoVPN бесплатно", url=VPN_BOT_URL)],
        ]
    )
    try:
        await bot.send_message(
            tg_id,
            (
                f"{g}всё ещё пользуетесь нашим прокси? 👍\n\n"
                "Напоминаем: для Instagram, YouTube и любых сайтов нужен <b>полный VPN</b> — прокси там не поможет.\n\n"
                "@noctovpn_bot:\n"
                "• <b>3 дня доступа бесплатно</b>\n"
                "• Всего <b>179 ₽/мес</b>\n"
                "• Без карты и лишней регистрации\n\n"
                "Попробуйте прямо сейчас 👇"
            ),
            reply_markup=keyboard,
        )
    except Exception:
        LOGGER.exception("Failed to send final VPN promo to user %s", tg_id)


async def _send_vpn_promo_dojim(
    bot: Bot,
    tg_id: int,
    vpn_promo_code: str,
    vpn_promo_bonus_days: int,
    delay_seconds: int,
    first_name: str = "",
) -> None:
    await asyncio.sleep(delay_seconds)
    g = _greet(first_name)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔥 Забрать 3 дня бесплатно", url=VPN_BOT_URL)],
        ]
    )
    try:
        await bot.send_message(
            tg_id,
            (
                f"{g}последнее напоминание про VPN 🙏\n\n"
                "@noctovpn_bot по ссылке даёт <b>3 дня доступа бесплатно</b>.\n"
                "После — <b>179 ₽/мес</b>. Если не понравится — просто не продлевайте.\n\n"
                "Это дешевле одной поездки на такси, а работает везде:\n"
                "Instagram, YouTube, любые сайты и приложения 🌍"
            ),
            reply_markup=keyboard,
        )
    except Exception:
        LOGGER.exception("Failed to send dojim VPN promo to user %s", tg_id)


async def _send_channel_reminder(
    bot: Bot,
    tg_id: int,
    channel_url: str,
    delay_seconds: int,
    first_name: str = "",
) -> None:
    await asyncio.sleep(delay_seconds)
    g = _greet(first_name)
    try:
        await bot.send_message(
            tg_id,
            (
                f"💡 {g}прокси-серверы иногда меняются.\n\n"
                "Чтобы не гадать «почему вдруг перестало работать» — подпишитесь на наш канал. "
                "Там мы сразу сообщаем о смене серверов, падениях и даём новые адреса.\n\n"
                "Одна подписка — и прокси всегда будет работать 👇"
            ),
            reply_markup=build_channel_reminder_keyboard(channel_url),
            disable_web_page_preview=True,
        )
    except Exception:
        LOGGER.exception("Failed to send channel reminder to user %s", tg_id)


async def _send_connection_check(
    bot: Bot,
    tg_id: int,
    first_name: str,
    delay_seconds: int,
) -> None:
    await asyncio.sleep(delay_seconds)
    g = _greet(first_name)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, работает!", callback_data="user:proxy_ok"),
                InlineKeyboardButton(text="❌ Не работает", callback_data="user:proxy_fail"),
            ]
        ]
    )
    try:
        await bot.send_message(
            tg_id,
            f"🔌 {g}прокси уже подключили?\n\nTelegram работает нормально?",
            reply_markup=keyboard,
        )
    except Exception:
        LOGGER.exception("Failed to send connection check to user %s", tg_id)


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
    vpn_onboarding_dojim_delay_sec: int,
    connection_check_delay_sec: int,
    vpn_promo_code: str,
    vpn_promo_bonus_days: int,
) -> None:
    user = message.from_user
    first_name = user.first_name or ""
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
        unavail_text = (
            "⚠️ <b>Прокси временно недоступен</b>\n\n"
            "Мы уже в курсе и работаем над этим. Обычно это занимает не больше нескольких минут."
        )
        unavail_kb_rows: list[list[InlineKeyboardButton]] = [
            [InlineKeyboardButton(text="💬 Написать в поддержку", url=f"https://t.me/{support_username}")],
        ]
        if channel_url:
            unavail_kb_rows.append(
                [InlineKeyboardButton(text="📣 Статус в нашем канале", url=channel_url)]
            )
        await message.answer(
            unavail_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=unavail_kb_rows),
        )
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
                            first_name=first_name,
                        )
                    )
                if vpn_onboarding_final_delay_sec > 0:
                    asyncio.create_task(
                        _send_vpn_promo_final(
                            message.bot, user.id,
                            vpn_promo_code, vpn_promo_bonus_days,
                            vpn_onboarding_final_delay_sec,
                            first_name=first_name,
                        )
                    )
                if vpn_onboarding_dojim_delay_sec > 0:
                    asyncio.create_task(
                        _send_vpn_promo_dojim(
                            message.bot, user.id,
                            vpn_promo_code, vpn_promo_bonus_days,
                            vpn_onboarding_dojim_delay_sec,
                            first_name=first_name,
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
        # Connection check — fires after a short delay to ask if proxy worked
        if connection_check_delay_sec > 0:
            asyncio.create_task(
                _send_connection_check(
                    message.bot, user.id, first_name, connection_check_delay_sec,
                )
            )

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
                        first_name=first_name,
                    )
                )

        # VPN onboarding chain
        if vpn_onboarding_delay_sec > 0:
            asyncio.create_task(
                _send_vpn_promo(
                    message.bot, user.id,
                    vpn_promo_code, vpn_promo_bonus_days,
                    vpn_onboarding_delay_sec,
                    first_name=first_name,
                )
            )
        if vpn_onboarding_final_delay_sec > 0:
            asyncio.create_task(
                _send_vpn_promo_final(
                    message.bot, user.id,
                    vpn_promo_code, vpn_promo_bonus_days,
                    vpn_onboarding_final_delay_sec,
                    first_name=first_name,
                )
            )
        if vpn_onboarding_dojim_delay_sec > 0:
            asyncio.create_task(
                _send_vpn_promo_dojim(
                    message.bot, user.id,
                    vpn_promo_code, vpn_promo_bonus_days,
                    vpn_onboarding_dojim_delay_sec,
                    first_name=first_name,
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
        await _safe_edit(
            callback,
            "⚠️ <b>Прокси временно недоступен</b>\n\nМы уже в курсе и работаем над этим.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="💬 Поддержка", url=f"https://t.me/{support_username}"),
            ]]),
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
    await callback.answer()
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass
    await callback.message.answer(_main_menu_text(), reply_markup=keyboard)


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
    await callback.answer("Отлично, доступ открыт! 🎉")
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass
    await callback.message.answer(_main_menu_text(), reply_markup=keyboard)


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
        "📖 <b>Как подключить прокси</b>\n\n"
        "1. Нажмите кнопку <b>«⚡ Подключить»</b> у нужного сервера\n"
        "2. Telegram откроет экран — нажмите <b>«Включить»</b>\n"
        "3. Убедитесь что переключатель <b>«Использовать этот прокси»</b> активен ✅\n\n"
        "💡 <b>Совет:</b> добавьте все серверы из списка и включите "
        "<b>«Автопереключение»</b> — тогда при падении одного сервера "
        "Telegram сам перейдёт на следующий.\n\n"
        f"Если что-то не работает — <a href=\"https://t.me/{support_username}\">напишите в поддержку</a>"
    )
    if channel_url:
        text += f"\n\n📣 Актуальные серверы и статусы: <a href=\"{channel_url}\">наш канал</a>"
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
        "🚀 <b>NoctoVPN — открывает Instagram, YouTube и всё остальное</b>\n\n"
        "Прокси работает только внутри Telegram. VPN — <b>на всех сайтах и приложениях</b> сразу.\n\n"
        "✅ Instagram, YouTube, TikTok\n"
        "✅ Любые сайты и приложения\n"
        "✅ На телефоне и компьютере\n"
        "✅ Скорость до <b>10 Гбит/с</b>\n\n"
        f"💸 Всего <b>179 ₽/мес</b> — дешевле одной поездки в метро.\n\n"
        "🎁 По ссылке получите <b>3 дня доступа бесплатно</b> — без карты, без риска.\n\n"
        "Попробуйте прямо сейчас — ничего не теряете 👇"
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
            "<b>Ваши 3 дня VPN бесплатно:</b>\n"
            f"{VPN_BOT_URL}\n\n"
            "Откройте ссылку и запустите бота."
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🚀 Открыть NoctoVPN", url=VPN_BOT_URL)]]
        ),
        disable_web_page_preview=True,
    )
    await callback.answer("Ссылка отправлена")


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
            "⚠️ <b>Прокси временно недоступен</b>\n\nМы уже в курсе и работаем над этим.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💬 Поддержка", url=f"https://t.me/{support_username}")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="user:home")],
            ]),
        )
        await callback.answer()
        return

    lines = [
        "🔒 <b>Прокси для Telegram</b>\n",
        "Добавьте <b>все серверы</b> сразу и включите <b>автопереключение</b> — если один упадёт, Telegram сам переключится на следующий.\n",
        "<b>Доступные серверы:</b>",
    ]
    for idx, proxy in enumerate(proxies):
        lines.append(f"{idx + 1}. <b>{proxy.name}</b>  <code>{proxy.server}:{proxy.port}</code>")
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
    tme_link = proxy.tme_link
    text = (
        "📤 <b>Поделитесь прокси с друзьями</b>\n\n"
        "Друг получит ссылку и подключится за одно нажатие — без регистрации и настроек.\n\n"
        "<i>Нужен VPN для всех сайтов? → @noctovpn_bot</i>"
    )
    await _safe_edit(
        callback,
        text,
        reply_markup=build_share_actions_keyboard(tme_link),
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
        "👥 <b>Пригласить друга</b>\n\n"
        "Отправьте ссылку другу — он получит бесплатный прокси за одно нажатие:\n\n"
        f"<code>{ref_link}</code>\n\n"
        f"Приглашено друзей: <b>{ref_count}</b>"
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


@router.callback_query(F.data == "user:proxy_ok")
async def cb_proxy_ok(
    callback: CallbackQuery,
    storage: Storage,
    channel_url: str | None,
    vpn_promo_code: str,
) -> None:
    user = callback.from_user
    await storage.set_user_proxy_connected(user.id, connected=True)

    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass

    me = await callback.bot.get_me()
    ref_link = f"https://t.me/{me.username}?start=ref_{user.id}"
    share_text = "Бесплатный MTProto прокси для Telegram — подключается за одно нажатие."
    share_url = (
        f"https://t.me/share/url?url={quote(ref_link, safe='')}"
        f"&text={quote(share_text, safe='')}"
    )
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="🚀 Попробовать NoctoVPN бесплатно", url=VPN_BOT_URL)],
        [InlineKeyboardButton(text="📤 Поделиться прокси с другом", url=share_url)],
    ]
    if channel_url:
        rows.append([InlineKeyboardButton(text="📣 Подписаться на наш канал", url=channel_url)])
    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)

    g = _greet(user.first_name or "")
    await callback.message.answer(
        f"🎉 {g}отлично, прокси работает!\n\n"
        "Раз Telegram открывается — Instagram и YouTube тоже хотите?\n"
        "@noctovpn_bot по ссылке даёт <b>3 дня доступа бесплатно</b>, без карты 👆",
        reply_markup=keyboard,
    )
    await callback.answer("Отлично! 🎉")


@router.callback_query(F.data == "user:proxy_fail")
async def cb_proxy_fail(
    callback: CallbackQuery,
    storage: Storage,
    support_username: str,
) -> None:
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📖 Инструкция", callback_data="user:instruction")],
            [InlineKeyboardButton(text="💬 Написать в поддержку", url=f"https://t.me/{support_username}")],
        ]
    )
    await callback.message.answer(
        "Жаль, разберёмся 🛠\n\n"
        "Попробуйте инструкцию — там по шагам показано как подключить. "
        "Если не поможет — напишите в поддержку, поможем быстро.",
        reply_markup=keyboard,
    )
    await callback.answer()
