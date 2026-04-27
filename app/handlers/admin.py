from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, quote

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.services.proxy_links import ProxyItem, ProxyStore
from app.services.storage import Storage

router = Router()
USERS_PAGE_SIZE = 10
VPN_BOT_URL = "https://t.me/noctovpn_bot?start=pxbt"
LOGGER = logging.getLogger(__name__)


class AddProxyForm(StatesGroup):
    name = State()
    server = State()
    port = State()
    secret = State()


class BroadcastForm(StatesGroup):
    media = State()
    confirm = State()
    buttons = State()


class UserSearchForm(StatesGroup):
    query = State()


class UserWriteForm(StatesGroup):
    text = State()


class ChannelInviteForm(StatesGroup):
    text = State()


class EditProxyNameForm(StatesGroup):
    name = State()


class AddProxyFromLinkForm(StatesGroup):
    link = State()
    name = State()


def _is_admin(user_id: int, admin_ids: set[int]) -> bool:
    return user_id in admin_ids


def _days_since(first_seen: str) -> int:
    try:
        dt = datetime.strptime(first_seen, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return 0
    return max(0, (datetime.now(timezone.utc) - dt).days)


def _humanize_first_seen(first_seen: str) -> str:
    try:
        dt = datetime.strptime(first_seen, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return "неизвестно"

    sec = int((datetime.now(timezone.utc) - dt).total_seconds())
    if sec < 60:
        return "только что"
    if sec < 3600:
        return f"{sec // 60} м. назад"
    if sec < 86400:
        return f"{sec // 3600} ч. назад"
    if sec < 172800:
        return "вчера"
    return f"{sec // 86400} дн. назад"


def _growth_percent(current: int, previous: int) -> str:
    if previous <= 0:
        if current <= 0:
            return "0%"
        return "+100%"
    delta = ((current - previous) / previous) * 100
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta:.1f}%"


def _is_subscribed_status(status: str) -> bool:
    return status in {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.CREATOR,
    }


def build_channel_invite_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Запустить рассылку", callback_data="admin:channel_invite_run")],
            [InlineKeyboardButton(text="✏️ Изменить текст", callback_data="admin:channel_invite_edit")],
            [InlineKeyboardButton(text="📊 Статистика кампании", callback_data="admin:channel_invite_stats")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:menu")],
        ]
    )


def _cut_text(text: str, limit: int = 500) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "..."


def build_channel_invite_screen_text(text: str) -> str:
    return (
        "<b>Кампания приглашения в канал</b>\n\n"
        "Рассылка отправляется только тем пользователям, которые не подписаны на канал.\n\n"
        "<b>Текущий текст:</b>\n"
        f"{_cut_text(text)}"
    )


def build_admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📋 Список прокси", callback_data="admin:list")],
            [InlineKeyboardButton(text="➕ Добавить прокси", callback_data="admin:add")],
            [InlineKeyboardButton(text="📣 Рассылка", callback_data="admin:broadcast")],
            [InlineKeyboardButton(text="🧪 Тест авто-сообщений", callback_data="admin:test_cta")],
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats")],
            [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin:users")],
            [InlineKeyboardButton(text="⬅️ В главное меню", callback_data="user:home")],
        ]
    )


def build_test_cta_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="1️⃣ VPN промо #1 (~10 мин)", callback_data="admin:test_cta:vpn1")],
            [InlineKeyboardButton(text="2️⃣ VPN промо #2 (~24 ч)", callback_data="admin:test_cta:vpn2")],
            [InlineKeyboardButton(text="3️⃣ VPN промо #3 — дожим (~3 дня)", callback_data="admin:test_cta:vpn3")],
            [InlineKeyboardButton(text="📣 Напоминание о канале", callback_data="admin:test_cta:channel")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:menu")],
        ]
    )


def build_admin_dashboard_text(
    total_users: int,
    new_users: int,
    total_proxies: int,
    enabled_proxies: int,
) -> str:
    return (
        "<b>Панель администратора</b>\n\n"
        "<b>Пользователи</b>\n"
        f"• Всего: <b>{total_users}</b>\n"
        f"• Новые за 24ч: <b>{new_users}</b>\n"
        "\n"
        "<b>Прокси</b>\n"
        f"• Всего: <b>{total_proxies}</b>\n"
        f"• Включено: <b>{enabled_proxies}</b>\n\n"
        "Выберите действие:"
    )


def build_proxy_list_keyboard(proxies: list[ProxyItem]) -> InlineKeyboardMarkup:
    """One button per proxy — opens the proxy card."""
    rows: list[list[InlineKeyboardButton]] = []
    first_enabled_seen = False
    for idx, proxy in enumerate(proxies):
        icon = "✅" if proxy.enabled else "⛔"
        label = f"{icon} {proxy.name}"
        if proxy.enabled and not first_enabled_seen:
            label += " 👑"
            first_enabled_seen = True
        rows.append([InlineKeyboardButton(text=label, callback_data=f"admin:proxy:{idx}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_proxy_card_keyboard(idx: int, proxy: ProxyItem, is_main: bool) -> InlineKeyboardMarkup:
    """Card actions for a single proxy."""
    toggle_text = "⛔ Выключить" if proxy.enabled else "✅ Включить"
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=toggle_text, callback_data=f"admin:toggle:{idx}")],
    ]
    if not is_main:
        rows.append([InlineKeyboardButton(text="⬆️ Сделать главным", callback_data=f"admin:proxy_main:{idx}")])
    rows += [
        [InlineKeyboardButton(text="✏️ Переименовать", callback_data=f"admin:proxy_edit_name:{idx}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin:delete:{idx}")],
        [InlineKeyboardButton(text="⬅️ К списку", callback_data="admin:list")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_add_method_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Вставить ссылку t.me/proxy", callback_data="admin:add:link")],
            [InlineKeyboardButton(text="✏️ Ввести вручную (4 шага)", callback_data="admin:add:manual")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:menu")],
        ]
    )


def build_wizard_keyboard(back_to: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=back_to)],
            [InlineKeyboardButton(text="✖️ Отмена", callback_data="admin:menu")],
        ]
    )


def build_users_keyboard(
    users: list[dict[str, str | int | None]],
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    for user in users:
        tg_id = int(user["tg_id"])
        username = user["username"]
        full_name = (user.get("full_name") or "").strip()
        blocked = bool(user.get("is_blocked"))
        status_icon = "⛔" if blocked else "✅"
        if username:
            user_text = f"@{username}"
        elif full_name:
            user_text = full_name
        else:
            user_text = str(tg_id)
        label = f"{status_icon} {user_text} | зашел: {_humanize_first_seen(str(user['first_seen']))}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"admin:user:{tg_id}:{page}:l")])

    nav_row: list[InlineKeyboardButton] = []
    if page > 1:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"admin:users:{page - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="admin:users:noop"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"admin:users:{page + 1}"))
    rows.append(nav_row)
    rows.append(
        [
            InlineKeyboardButton(text="🔎 Поиск", callback_data="admin:users_search"),
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats"),
        ]
    )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_user_profile_keyboard(tg_id: int, page: int, source: str) -> InlineKeyboardMarkup:
    back_callback = "admin:users_search" if source == "s" else f"admin:users:{page}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✉️ Отправить сообщение", callback_data=f"admin:uw:{tg_id}:{page}:{source}")],
            [InlineKeyboardButton(text="⚠️ Ограничить / Разблокировать", callback_data=f"admin:ub:{tg_id}:{page}:{source}")],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin:ud:{tg_id}:{page}:{source}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback)],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="admin:menu")],
        ]
    )


def build_user_search_results_keyboard(users: list[dict[str, str | int | None]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for user in users:
        tg_id = int(user["tg_id"])
        username = user["username"]
        if username:
            label = f"👤 @{username} ({tg_id})"
        else:
            label = f"👤 {tg_id}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"admin:user:{tg_id}:1:s")])
    rows.append([InlineKeyboardButton(text="🔎 Новый поиск", callback_data="admin:users_search")])
    rows.append([InlineKeyboardButton(text="👥 К списку пользователей", callback_data="admin:users:1")])
    rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _proxy_card_text(idx: int, proxy: ProxyItem, is_main: bool) -> str:
    status = "✅ Включён" if proxy.enabled else "⛔ Выключен"
    main_note = " 👑 (главный)" if is_main else ""
    secret_preview = proxy.secret[:8] + "…" if len(proxy.secret) > 8 else proxy.secret
    return (
        f"<b>📡 {proxy.name}</b>{main_note}\n\n"
        f"• Статус: {status}\n"
        f"• Сервер: <code>{proxy.server}</code>\n"
        f"• Порт: <code>{proxy.port}</code>\n"
        f"• Secret: <code>{secret_preview}</code>\n\n"
        f"Ссылка: {proxy.tme_link}"
    )


def _add_step_text(step: str, data: dict) -> str:
    name = data.get("name", "—")
    server = data.get("server", "—")
    port = data.get("port", "—")

    if step == "name":
        return "Добавление прокси\n\nШаг 1/4: отправьте название (например: Резерв #2)."
    if step == "server":
        return (
            "Добавление прокси\n"
            f"name: {name}\n\n"
            "Шаг 2/4: отправьте server (например: proxy.example.com)."
        )
    if step == "port":
        return (
            "Добавление прокси\n"
            f"name: {name}\n"
            f"server: {server}\n\n"
            "Шаг 3/4: отправьте port (число от 1 до 65535)."
        )
    return (
        "Добавление прокси\n"
        f"name: {name}\n"
        f"server: {server}\n"
        f"port: {port}\n\n"
        "Шаг 4/4: отправьте secret."
    )


async def _save_panel_ref(state: FSMContext, callback: CallbackQuery) -> None:
    await state.update_data(
        panel_chat_id=callback.message.chat.id,
        panel_message_id=callback.message.message_id,
    )


async def _edit_panel(
    bot: Bot,
    state: FSMContext,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    data = await state.get_data()
    chat_id = data.get("panel_chat_id")
    message_id = data.get("panel_message_id")
    if not chat_id or not message_id:
        return
    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )


async def _safe_delete_message(message: Message) -> None:
    try:
        await message.delete()
    except TelegramBadRequest:
        return


async def _send_broadcast_message(
    bot: Bot,
    tg_id: int,
    text: str,
    photo_id: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    for _ in range(2):
        try:
            if photo_id:
                await bot.send_photo(
                    chat_id=tg_id,
                    photo=photo_id,
                    caption=text or None,
                    reply_markup=reply_markup,
                )
            else:
                await bot.send_message(
                    chat_id=tg_id,
                    text=text,
                    reply_markup=reply_markup,
                    disable_web_page_preview=True,
                )
            return True
        except TelegramRetryAfter as exc:
            await asyncio.sleep(float(exc.retry_after) + 0.5)
        except (TelegramForbiddenError, TelegramBadRequest):
            return False
        except Exception:
            LOGGER.exception("Unexpected broadcast error for tg_id=%s", tg_id)
            return False
    return False


def build_broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Всем пользователям", callback_data="admin:bc_send_all")],
            [InlineKeyboardButton(text="📵 Только не подписанным на канал", callback_data="admin:bc_send_unsub")],
            [InlineKeyboardButton(text="🔘 Добавить кнопки", callback_data="admin:bc_buttons")],
            [InlineKeyboardButton(text="✖️ Отмена", callback_data="admin:menu")],
        ]
    )


async def _execute_broadcast(
    bot: Bot,
    storage: Storage,
    broadcast_workers: int,
    panel_chat_id: int,
    panel_message_id: int,
    text: str,
    photo_id: str | None,
    keyboard: InlineKeyboardMarkup | None,
    filter_unsub: bool = False,
    channel_id: str | None = None,
) -> None:
    audience_label = "не подписанным на канал" if filter_unsub else "всем пользователям"
    try:
        await bot.edit_message_text(
            chat_id=panel_chat_id,
            message_id=panel_message_id,
            text=f"⏳ Рассылка в процессе ({audience_label})...",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ В меню", callback_data="admin:menu")]]
            ),
        )
    except TelegramBadRequest:
        pass

    user_ids = await storage.get_all_user_ids()
    success = 0
    failed = 0
    skipped = 0
    worker_count = max(1, int(broadcast_workers))
    queue: asyncio.Queue[int | None] = asyncio.Queue()

    for tg_id in user_ids:
        queue.put_nowait(int(tg_id))
    for _ in range(worker_count):
        queue.put_nowait(None)

    lock = asyncio.Lock()

    async def worker() -> None:
        nonlocal success, failed, skipped
        while True:
            tg_id = await queue.get()
            if tg_id is None:
                return

            if filter_unsub and channel_id:
                try:
                    member = await bot.get_chat_member(chat_id=channel_id, user_id=tg_id)
                    is_sub = _is_subscribed_status(member.status)
                except TelegramRetryAfter as exc:
                    await asyncio.sleep(float(exc.retry_after) + 0.5)
                    try:
                        member = await bot.get_chat_member(chat_id=channel_id, user_id=tg_id)
                        is_sub = _is_subscribed_status(member.status)
                    except Exception:
                        is_sub = False
                except Exception:
                    is_sub = False
                if is_sub:
                    async with lock:
                        skipped += 1
                    continue

            ok = await _send_broadcast_message(bot, tg_id, text, photo_id=photo_id, reply_markup=keyboard)
            async with lock:
                if ok:
                    success += 1
                else:
                    failed += 1

    await asyncio.gather(*(worker() for _ in range(worker_count)))

    result_lines = [
        "<b>✅ Рассылка завершена</b>\n",
        f"• Получателей в базе: <b>{len(user_ids)}</b>",
    ]
    if filter_unsub:
        result_lines.append(f"• Уже подписаны (пропущено): <b>{skipped}</b>")
    result_lines += [
        f"• Доставлено: <b>{success}</b>",
        f"• Ошибок: <b>{failed}</b>",
    ]

    try:
        await bot.edit_message_text(
            chat_id=panel_chat_id,
            message_id=panel_message_id,
            text="\n".join(result_lines),
            reply_markup=build_admin_menu(),
        )
    except TelegramBadRequest:
        pass


async def _send_channel_invite_message(
    bot: Bot,
    tg_id: int,
    text: str,
    channel_url: str,
) -> bool:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подписаться на канал", url=channel_url)],
        ]
    )
    for _ in range(2):
        try:
            await bot.send_message(
                chat_id=tg_id,
                text=text,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
            return True
        except TelegramRetryAfter as exc:
            await asyncio.sleep(float(exc.retry_after) + 0.5)
        except (TelegramForbiddenError, TelegramBadRequest):
            return False
        except Exception:
            LOGGER.exception("Unexpected channel invite error for tg_id=%s", tg_id)
            return False
    return False


def _plain_text(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value or "")).strip()


def _parse_proxy_link(link: str) -> tuple[str, int, str] | None:
    """Parse server/port/secret from a https://t.me/proxy?... or tg://proxy?... URL."""
    try:
        link = link.strip()
        if "t.me/proxy?" in link:
            query_str = link.split("t.me/proxy?", 1)[1]
        elif link.lower().startswith("tg://proxy?"):
            query_str = link[len("tg://proxy?"):]
        else:
            return None
        params = parse_qs(query_str)
        server = params.get("server", [""])[0].strip()
        port_str = params.get("port", [""])[0].strip()
        secret = params.get("secret", [""])[0].strip()
        if not server or not port_str.isdigit() or not secret:
            return None
        port = int(port_str)
        if port < 1 or port > 65535:
            return None
        return server, port, secret
    except Exception:
        return None


def _build_share_url(bot_username: str | None, share_text: str) -> str:
    invite_link = f"https://t.me/{bot_username}" if bot_username else "https://t.me"
    safe_text = share_text.strip() or "Подключай бесплатный Proxy для Telegram"
    return (
        f"https://t.me/share/url?url={quote(invite_link, safe='')}"
        f"&text={quote(safe_text, safe='')}"
    )


def _parse_broadcast_buttons(raw: str, share_url: str) -> tuple[InlineKeyboardMarkup | None, str | None]:
    text = raw.strip()
    if text.lower() in {"", "-", "нет", "skip"}:
        return None, None

    rows: list[list[InlineKeyboardButton]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" not in line:
            return None, "Неверный формат. Используйте: Текст кнопки | URL"

        label, target = line.split("|", 1)
        label = label.strip()
        target = target.strip()
        if not label or not target:
            return None, "Неверный формат. У кнопки должны быть текст и URL."

        if target.lower() == "share":
            target = share_url

        if not (
            target.startswith("https://")
            or target.startswith("http://")
            or target.startswith("tg://")
        ):
            return None, f"Неверный URL у кнопки: {label}"

        rows.append([InlineKeyboardButton(text=label, url=target)])

    if not rows:
        return None, None
    return InlineKeyboardMarkup(inline_keyboard=rows), None


@router.message(Command("admin"))
async def cmd_admin(
    message: Message,
    admin_ids: set[int],
    state: FSMContext,
    storage: Storage,
    proxy_store: ProxyStore,
) -> None:
    if not _is_admin(message.from_user.id, admin_ids):
        await message.answer("Недостаточно прав.")
        return

    await state.clear()
    total_users = await storage.count_users()
    new_users = await storage.count_new_users_last_hours(24)
    proxies = proxy_store.load_all()
    enabled_proxies = len([proxy for proxy in proxies if proxy.enabled])
    text = build_admin_dashboard_text(
        total_users=total_users,
        new_users=new_users,
        total_proxies=len(proxies),
        enabled_proxies=enabled_proxies,
    )
    await message.answer(text, reply_markup=build_admin_menu())


@router.callback_query(F.data.startswith("admin:"))
async def cb_admin_actions(
    callback: CallbackQuery,
    admin_ids: set[int],
    proxy_store: ProxyStore,
    storage: Storage,
    state: FSMContext,
    channel_url: str | None,
    channel_id: str | None,
    channel_campaign_workers: int,
    broadcast_workers: int,
    vpn_promo_code: str,
    vpn_promo_bonus_days: int,
) -> None:
    if not _is_admin(callback.from_user.id, admin_ids):
        await callback.answer("Недостаточно прав", show_alert=True)
        return

    action = callback.data.split(":")

    if action[1] == "menu":
        await state.clear()
        total_users = await storage.count_users()
        new_users = await storage.count_new_users_last_hours(24)
        proxies = proxy_store.load_all()
        enabled_proxies = len([proxy for proxy in proxies if proxy.enabled])
        text = build_admin_dashboard_text(
            total_users=total_users,
            new_users=new_users,
            total_proxies=len(proxies),
            enabled_proxies=enabled_proxies,
        )
        await callback.message.edit_text(text, reply_markup=build_admin_menu())
        await callback.answer()
        return

    if action[1] == "list":
        await state.clear()
        proxies = proxy_store.load_all()
        if not proxies:
            back = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:menu")]]
            )
            await callback.message.edit_text("Список прокси пуст.", reply_markup=back)
            await callback.answer()
            return

        enabled_count = sum(1 for p in proxies if p.enabled)
        lines = [f"<b>📋 Прокси ({len(proxies)} шт., {enabled_count} включено)</b>\n"]
        for idx, proxy in enumerate(proxies):
            icon = "✅" if proxy.enabled else "⛔"
            crown = " 👑" if proxy.enabled and idx == next((i for i, p in enumerate(proxies) if p.enabled), -1) else ""
            lines.append(f"{icon} <b>{proxy.name}</b>{crown} — <code>{proxy.server}:{proxy.port}</code>")
        lines.append("\nНажмите на прокси для управления:")

        await callback.message.edit_text("\n".join(lines), reply_markup=build_proxy_list_keyboard(proxies))
        await callback.answer()
        return

    if action[1] == "toggle":
        idx = int(action[2])
        proxies = proxy_store.load_all()
        if idx < 0 or idx >= len(proxies):
            await callback.answer("Неверный индекс", show_alert=True)
            return

        target = proxies[idx]
        target.enabled = not target.enabled
        proxy_store.save_all(proxies)
        status_label = "включён" if target.enabled else "выключен"
        await callback.answer(f"{target.name}: {status_label}")

        first_enabled_idx = next((i for i, p in enumerate(proxies) if p.enabled), -1)
        is_main = (idx == first_enabled_idx)
        text = _proxy_card_text(idx, target, is_main)
        await callback.message.edit_text(text, reply_markup=build_proxy_card_keyboard(idx, target, is_main))
        return

    if action[1] == "delete":
        idx = int(action[2])
        proxies = proxy_store.load_all()
        if idx < 0 or idx >= len(proxies):
            await callback.answer("Неверный индекс", show_alert=True)
            return

        removed = proxies.pop(idx)
        proxy_store.save_all(proxies)

        if not proxies:
            back = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:menu")]]
            )
            await callback.message.edit_text("Прокси удалён. Список пуст.", reply_markup=back)
            await callback.answer("Удалено")
            return

        enabled_count = sum(1 for p in proxies if p.enabled)
        lines = [f"<b>📋 Прокси ({len(proxies)} шт., {enabled_count} включено)</b>\n"]
        for i, proxy in enumerate(proxies):
            icon = "✅" if proxy.enabled else "⛔"
            crown = " 👑" if proxy.enabled and i == next((j for j, p in enumerate(proxies) if p.enabled), -1) else ""
            lines.append(f"{icon} <b>{proxy.name}</b>{crown} — <code>{proxy.server}:{proxy.port}</code>")
        lines.append("\nНажмите на прокси для управления:")

        await callback.message.edit_text("\n".join(lines), reply_markup=build_proxy_list_keyboard(proxies))
        await callback.answer(f"Удалён: {removed.name}")
        return

    if action[1] == "add" and len(action) == 2:
        await state.clear()
        await callback.message.edit_text(
            "<b>Добавление прокси</b>\n\n"
            "Выберите способ добавления:",
            reply_markup=build_add_method_keyboard(),
        )
        await callback.answer()
        return

    if action[1] == "add" and len(action) > 2 and action[2] == "link":
        await state.clear()
        await state.set_state(AddProxyFromLinkForm.link)
        await _save_panel_ref(state, callback)
        await callback.message.edit_text(
            "<b>Добавление по ссылке</b>\n\n"
            "Отправьте ссылку t.me/proxy или tg://proxy:\n\n"
            "<code>https://t.me/proxy?server=...&port=...&secret=...</code>",
            reply_markup=build_wizard_keyboard("admin:add"),
        )
        await callback.answer()
        return

    if action[1] == "add" and len(action) > 2 and action[2] == "manual":
        await state.clear()
        await state.set_state(AddProxyForm.name)
        await state.update_data(name="", server="", port="")
        await _save_panel_ref(state, callback)
        await callback.message.edit_text(
            _add_step_text("name", {}),
            reply_markup=build_wizard_keyboard("admin:add"),
        )
        await callback.answer()
        return

    if action[1] == "add" and len(action) > 2 and action[2] == "back":
        current_state = await state.get_state()
        data = await state.get_data()

        if current_state == AddProxyForm.server.state:
            await state.set_state(AddProxyForm.name)
            await _edit_panel(
                callback.bot,
                state,
                _add_step_text("name", data),
                build_wizard_keyboard("admin:add"),
            )
            await callback.answer()
            return

        if current_state == AddProxyForm.port.state:
            await state.set_state(AddProxyForm.server)
            await _edit_panel(
                callback.bot,
                state,
                _add_step_text("server", data),
                build_wizard_keyboard("admin:add:back"),
            )
            await callback.answer()
            return

        if current_state == AddProxyForm.secret.state:
            await state.set_state(AddProxyForm.port)
            await _edit_panel(
                callback.bot,
                state,
                _add_step_text("port", data),
                build_wizard_keyboard("admin:add:back"),
            )
            await callback.answer()
            return

        await callback.answer("Назад недоступно", show_alert=True)
        return

    if action[1] == "proxy" and len(action) == 3 and action[2].isdigit():
        await state.clear()
        idx = int(action[2])
        proxies = proxy_store.load_all()
        if idx < 0 or idx >= len(proxies):
            await callback.answer("Прокси не найден", show_alert=True)
            return
        proxy = proxies[idx]
        first_enabled_idx = next((i for i, p in enumerate(proxies) if p.enabled), -1)
        is_main = (idx == first_enabled_idx)
        text = _proxy_card_text(idx, proxy, is_main)
        await callback.message.edit_text(text, reply_markup=build_proxy_card_keyboard(idx, proxy, is_main))
        await callback.answer()
        return

    if action[1] == "proxy_main" and len(action) == 3 and action[2].isdigit():
        idx = int(action[2])
        proxies = proxy_store.load_all()
        if idx < 0 or idx >= len(proxies):
            await callback.answer("Прокси не найден", show_alert=True)
            return
        proxy = proxies.pop(idx)
        proxies.insert(0, proxy)
        proxy_store.save_all(proxies)
        await callback.answer(f"«{proxy.name}» теперь главный")
        # Show card at new index (0)
        first_enabled_idx = next((i for i, p in enumerate(proxies) if p.enabled), -1)
        is_main = (0 == first_enabled_idx)
        text = _proxy_card_text(0, proxy, is_main)
        await callback.message.edit_text(text, reply_markup=build_proxy_card_keyboard(0, proxy, is_main))
        return

    if action[1] == "proxy_edit_name" and len(action) == 3 and action[2].isdigit():
        idx = int(action[2])
        proxies = proxy_store.load_all()
        if idx < 0 or idx >= len(proxies):
            await callback.answer("Прокси не найден", show_alert=True)
            return
        proxy = proxies[idx]
        await state.clear()
        await state.set_state(EditProxyNameForm.name)
        await _save_panel_ref(state, callback)
        await state.update_data(edit_proxy_idx=idx)
        await callback.message.edit_text(
            f"<b>Переименование</b>\n\n"
            f"Текущее название: <b>{proxy.name}</b>\n\n"
            "Отправьте новое название:",
            reply_markup=build_wizard_keyboard(f"admin:proxy:{idx}"),
        )
        await callback.answer()
        return

    if action[1] == "broadcast" and len(action) == 2:
        await state.clear()
        await state.set_state(BroadcastForm.media)
        await _save_panel_ref(state, callback)
        await callback.message.edit_text(
            (
                "<b>Новая рассылка</b>\n\n"
                "Отправьте текст или фото с подписью.\n\n"
                "Поддерживается HTML: <code>&lt;b&gt;</code> <code>&lt;i&gt;</code> "
                "<code>&lt;code&gt;</code> <code>&lt;a href='...'&gt;</code>"
            ),
            reply_markup=build_wizard_keyboard("admin:menu"),
        )
        await callback.answer()
        return

    if action[1] in {"bc_send_all", "bc_send_unsub"}:
        data = await state.get_data()
        text = str(data.get("broadcast_text", "")).strip()
        photo_id = data.get("broadcast_photo_id")
        panel_chat_id = int(data.get("panel_chat_id") or 0)
        panel_message_id = int(data.get("panel_message_id") or 0)
        raw_buttons = str(data.get("broadcast_buttons_raw") or "нет")
        if not text and not photo_id:
            await callback.answer("Нет контента для рассылки", show_alert=True)
            return
        if not panel_chat_id or not panel_message_id:
            await callback.answer("Ошибка панели, запустите рассылку заново", show_alert=True)
            return
        filter_unsub = action[1] == "bc_send_unsub"
        if filter_unsub and not channel_id:
            await callback.answer("CHANNEL_ID не задан в .env — фильтрация невозможна", show_alert=True)
            return
        me = await callback.bot.get_me()
        share_url = _build_share_url(me.username, _plain_text(text))
        broadcast_keyboard, _ = _parse_broadcast_buttons(raw_buttons, share_url)
        await state.clear()
        await callback.answer()
        await _execute_broadcast(
            callback.bot, storage, broadcast_workers,
            panel_chat_id, panel_message_id, text, photo_id, broadcast_keyboard,
            filter_unsub=filter_unsub, channel_id=channel_id,
        )
        return

    if action[1] == "bc_buttons":
        data = await state.get_data()
        await state.set_state(BroadcastForm.buttons)
        preview = _cut_text(str(data.get("broadcast_text", "")), 200)
        photo_id = data.get("broadcast_photo_id")
        media_label = "📷 Фото + подпись" if photo_id else "📝 Текст"
        await callback.message.edit_text(
            (
                "<b>Добавление кнопок</b>\n\n"
                "Введите кнопки, каждую с новой строки:\n"
                "<code>Текст кнопки | URL</code>\n\n"
                "Спец-URL <code>share</code> создаст кнопку «Поделиться ботом».\n"
                "Если кнопки не нужны — отправьте <code>нет</code>.\n\n"
                f"<b>Контент ({media_label}):</b>\n{preview}"
            ),
            reply_markup=build_wizard_keyboard("admin:menu"),
        )
        await callback.answer()
        return

    if action[1] == "channel_invite":
        await state.clear()
        template_text = await storage.get_channel_invite_text()
        await callback.message.edit_text(
            build_channel_invite_screen_text(template_text),
            reply_markup=build_channel_invite_menu(),
            disable_web_page_preview=True,
        )
        await callback.answer()
        return

    if action[1] == "channel_invite_edit":
        await state.clear()
        await state.set_state(ChannelInviteForm.text)
        await _save_panel_ref(state, callback)
        current_text = await storage.get_channel_invite_text()
        await callback.message.edit_text(
            (
                "<b>Изменение текста приглашения</b>\n\n"
                "Отправьте новый текст одним сообщением.\n\n"
                "<b>Текущий текст:</b>\n"
                f"{_cut_text(current_text, 350)}"
            ),
            reply_markup=build_wizard_keyboard("admin:channel_invite"),
            disable_web_page_preview=True,
        )
        await callback.answer()
        return

    if action[1] == "channel_invite_stats":
        await state.clear()
        stats = await storage.get_channel_invite_stats()
        if stats["runs_count"] == 0:
            text = (
                "<b>Статистика кампании</b>\n\n"
                "Запусков еще не было."
            )
        else:
            text = (
                "<b>Статистика кампании</b>\n\n"
                f"• Запусков: <b>{stats['runs_count']}</b>\n"
                f"• Всего отправлено: <b>{stats['sent_ok_total']}</b>\n"
                f"• Всего ошибок: <b>{stats['sent_failed_total']}</b>\n\n"
                "<b>Последний запуск:</b>\n"
                f"• Дата: <b>{stats['last_created_at']}</b>\n"
                f"• Пользователей в базе: <b>{stats['last_total_users']}</b>\n"
                f"• Уже подписаны: <b>{stats['last_subscribed_users']}</b>\n"
                f"• Целевые (без подписки): <b>{stats['last_target_users']}</b>\n"
                f"• Доставлено: <b>{stats['last_sent_ok']}</b>\n"
                f"• Ошибок: <b>{stats['last_sent_failed']}</b>"
            )
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:channel_invite")]]
            ),
        )
        await callback.answer()
        return

    if action[1] == "channel_invite_run":
        await state.clear()
        if not channel_url or not channel_id:
            await callback.answer("Нужно задать CHANNEL_URL и CHANNEL_ID в .env", show_alert=True)
            return

        await callback.message.edit_text(
            "Кампания в процессе...\nПроверяю подписку и отправляю приглашения.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:channel_invite")]]
            ),
        )
        await callback.answer()

        user_ids = await storage.get_all_user_ids()
        total_users = len(user_ids)
        subscribed_users = 0
        target_users = 0
        sent_ok = 0
        sent_failed = 0
        template_text = await storage.get_channel_invite_text()
        workers = max(1, int(channel_campaign_workers))

        queue: asyncio.Queue[int | None] = asyncio.Queue()
        for tg_id in user_ids:
            queue.put_nowait(int(tg_id))
        for _ in range(workers):
            queue.put_nowait(None)

        lock = asyncio.Lock()

        async def worker() -> None:
            nonlocal subscribed_users, target_users, sent_ok, sent_failed
            while True:
                tg_id = await queue.get()
                if tg_id is None:
                    return

                is_subscribed = False
                try:
                    member = await callback.bot.get_chat_member(chat_id=channel_id, user_id=tg_id)
                    is_subscribed = _is_subscribed_status(member.status)
                except TelegramRetryAfter as exc:
                    await asyncio.sleep(float(exc.retry_after) + 0.5)
                    try:
                        member = await callback.bot.get_chat_member(chat_id=channel_id, user_id=tg_id)
                        is_subscribed = _is_subscribed_status(member.status)
                    except Exception:
                        is_subscribed = False
                except TelegramBadRequest:
                    is_subscribed = False
                except Exception:
                    LOGGER.exception("Failed to check chat member for tg_id=%s", tg_id)
                    is_subscribed = False

                if is_subscribed:
                    async with lock:
                        subscribed_users += 1
                    continue

                async with lock:
                    target_users += 1

                ok = await _send_channel_invite_message(
                    callback.bot,
                    tg_id,
                    template_text,
                    channel_url,
                )
                async with lock:
                    if ok:
                        sent_ok += 1
                    else:
                        sent_failed += 1

        await asyncio.gather(*(worker() for _ in range(workers)))

        await storage.add_channel_invite_run(
            total_users=total_users,
            subscribed_users=subscribed_users,
            target_users=target_users,
            sent_ok=sent_ok,
            sent_failed=sent_failed,
            template_text=template_text,
        )

        await callback.message.edit_text(
            (
                "<b>Кампания завершена</b>\n\n"
                f"• Пользователей в базе: <b>{total_users}</b>\n"
                f"• Уже подписаны: <b>{subscribed_users}</b>\n"
                f"• Без подписки: <b>{target_users}</b>\n"
                f"• Доставлено: <b>{sent_ok}</b>\n"
                f"• Ошибок: <b>{sent_failed}</b>"
            ),
            reply_markup=build_channel_invite_menu(),
        )
        return

    if action[1] == "stats":
        await state.clear()
        total_users = await storage.count_users()
        new_today = await storage.count_new_users_last_hours(24)
        new_week = await storage.count_new_users_last_hours(24 * 7)
        new_month = await storage.count_new_users_last_hours(24 * 30)

        prev_today = max(0, await storage.count_new_users_last_hours(24 * 2) - new_today)
        prev_week = max(0, await storage.count_new_users_last_hours(24 * 14) - new_week)
        prev_month = max(0, await storage.count_new_users_last_hours(24 * 60) - new_month)

        growth_today = _growth_percent(new_today, prev_today)
        growth_week = _growth_percent(new_week, prev_week)
        growth_month = _growth_percent(new_month, prev_month)

        proxies = proxy_store.load_all()
        enabled_proxies = len([proxy for proxy in proxies if proxy.enabled])

        text = (
            "<b>Статистика бота</b>\n\n"
            "<b>Пользователи</b>\n"
            f"• Всего: <b>{total_users}</b>\n"
            "\n"
            "<b>📈 Новые пользователи</b>\n"
            f"• Сегодня: <b>{new_today}</b> ({growth_today})\n"
            f"• За неделю: <b>{new_week}</b> ({growth_week})\n"
            f"• За месяц: <b>{new_month}</b> ({growth_month})\n"
            "\n"
            "<b>Прокси</b>\n"
            f"• Всего: <b>{len(proxies)}</b>\n"
            f"• Включено: <b>{enabled_proxies}</b>"
        )
        back = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:menu")]]
        )
        await callback.message.edit_text(text, reply_markup=back)
        await callback.answer()
        return

    if action[1] == "users":
        await state.clear()
        if len(action) >= 3 and action[2] == "noop":
            await callback.answer()
            return

        page = 1
        if len(action) >= 3 and action[2].isdigit():
            page = max(1, int(action[2]))

        total_users = await storage.count_users()
        total_pages = max(1, (total_users + USERS_PAGE_SIZE - 1) // USERS_PAGE_SIZE)
        if page > total_pages:
            page = total_pages

        users = await storage.get_users_page(page=page, page_size=USERS_PAGE_SIZE)
        text = (
            f"👥 Список пользователей (стр. {page}/{total_pages})\n\n"
            f"Всего: {total_users}\n"
            "Иконки: ✅/⛔ статус\n"
            "Нажмите на пользователя для управления:"
        )
        keyboard = build_users_keyboard(users=users, page=page, total_pages=total_pages)
        await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer()
        return

    if action[1] == "users_search":
        await state.clear()
        await state.set_state(UserSearchForm.query)
        await _save_panel_ref(state, callback)
        await callback.message.edit_text(
            "Поиск пользователя\n\n"
            "Отправьте tg_id, @username или часть имени.\n"
            "Например: 123456789 или @username",
            reply_markup=build_wizard_keyboard("admin:users:1"),
        )
        await callback.answer()
        return

    if action[1] == "user":
        await state.clear()
        if len(action) < 5:
            await callback.answer("Некорректные данные", show_alert=True)
            return

        if not action[2].isdigit() or not action[3].isdigit():
            await callback.answer("Некорректные данные", show_alert=True)
            return

        tg_id = int(action[2])
        page = max(1, int(action[3]))
        source = action[4] if action[4] in {"l", "s"} else "l"
        user_data = await storage.get_user_by_tg_id(tg_id)
        if user_data is None:
            await callback.answer("Пользователь не найден", show_alert=True)
            return

        username = user_data["username"]
        username_text = f"@{username}" if username else "-"
        full_name = user_data["full_name"] or "-"
        blocked = bool(user_data.get("is_blocked"))
        status_text = "⛔ Ограничен" if blocked else "✅ Активен"
        text = (
            "Профиль пользователя\n"
            f"- статус: {status_text}\n"
            f"- tg_id: {user_data['tg_id']}\n"
            f"- username: {username_text}\n"
            f"- имя: {full_name}\n"
            f"- first_seen: {user_data['first_seen']}\n"
            f"- дней в боте: {_days_since(str(user_data['first_seen']))}"
        )
        await callback.message.edit_text(text, reply_markup=build_user_profile_keyboard(tg_id, page, source))
        await callback.answer()
        return

    if action[1] == "uw":
        if len(action) < 5 or not action[2].isdigit() or not action[3].isdigit():
            await callback.answer("Некорректные данные", show_alert=True)
            return

        tg_id = int(action[2])
        page = int(action[3])
        source = action[4] if action[4] in {"l", "s"} else "l"
        user_data = await storage.get_user_by_tg_id(tg_id)
        if user_data is None:
            await callback.answer("Пользователь не найден", show_alert=True)
            return

        await state.clear()
        await state.set_state(UserWriteForm.text)
        await _save_panel_ref(state, callback)
        await state.update_data(write_target_tg_id=tg_id, write_back_page=page, write_source=source)
        await callback.message.edit_text(
            "Сообщение пользователю\n\n"
            f"Получатель: {tg_id}\n"
            "Отправьте текст одним сообщением.",
            reply_markup=build_wizard_keyboard(f"admin:user:{tg_id}:{page}:{source}"),
        )
        await callback.answer()
        return

    if action[1] == "ub":
        if len(action) < 5 or not action[2].isdigit() or not action[3].isdigit():
            await callback.answer("Некорректные данные", show_alert=True)
            return

        tg_id = int(action[2])
        page = int(action[3])
        source = action[4] if action[4] in {"l", "s"} else "l"
        user_data = await storage.get_user_by_tg_id(tg_id)
        if user_data is None:
            await callback.answer("Пользователь не найден", show_alert=True)
            return

        new_blocked = not bool(user_data.get("is_blocked"))
        await storage.set_user_blocked(tg_id, new_blocked)
        updated = await storage.get_user_by_tg_id(tg_id)
        if updated is None:
            await callback.answer("Пользователь не найден", show_alert=True)
            return

        username = updated["username"]
        username_text = f"@{username}" if username else "-"
        full_name = updated["full_name"] or "-"
        status_text = "⛔ Ограничен" if bool(updated.get("is_blocked")) else "✅ Активен"
        text = (
            "Профиль пользователя\n"
            f"- статус: {status_text}\n"
            f"- tg_id: {updated['tg_id']}\n"
            f"- username: {username_text}\n"
            f"- имя: {full_name}\n"
            f"- first_seen: {updated['first_seen']}\n"
            f"- дней в боте: {_days_since(str(updated['first_seen']))}"
        )
        await callback.message.edit_text(text, reply_markup=build_user_profile_keyboard(tg_id, page, source))
        await callback.answer("Ограничение обновлено")
        return

    if action[1] == "ud":
        if len(action) < 5 or not action[2].isdigit() or not action[3].isdigit():
            await callback.answer("Некорректные данные", show_alert=True)
            return

        tg_id = int(action[2])
        page = int(action[3])
        source = action[4] if action[4] in {"l", "s"} else "l"
        deleted = await storage.delete_user_by_tg_id(tg_id)
        if not deleted:
            await callback.answer("Пользователь не найден", show_alert=True)
            return
        if source == "s":
            await callback.message.edit_text(
                f"Пользователь {tg_id} удален.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="🔎 Новый поиск", callback_data="admin:users_search")],
                        [InlineKeyboardButton(text="👥 К списку", callback_data="admin:users:1")],
                    ]
                ),
            )
            await callback.answer("Удалено")
            return

        total_users = await storage.count_users()
        total_pages = max(1, (total_users + USERS_PAGE_SIZE - 1) // USERS_PAGE_SIZE)
        if page > total_pages:
            page = total_pages
        users = await storage.get_users_page(page=page, page_size=USERS_PAGE_SIZE)
        text = (
            f"👥 Список пользователей (стр. {page}/{total_pages})\n\n"
            f"Всего: {total_users}\n"
            "Иконки: ✅/⛔ статус\n"
            "Нажмите на пользователя для управления:"
        )
        keyboard = build_users_keyboard(users=users, page=page, total_pages=total_pages)
        await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer("Удалено")
        return

    if action[1] == "test_cta" and len(action) == 2:
        await state.clear()
        await callback.message.edit_text(
            "<b>🧪 Тест авто-сообщений</b>\n\n"
            "Выберите сообщение — бот пришлёт его вам прямо сейчас, "
            "как оно выглядит для пользователя (без задержки).",
            reply_markup=build_test_cta_keyboard(),
        )
        await callback.answer()
        return

    if action[1] == "test_cta" and len(action) == 3:
        cta_type = action[2]
        admin_id = callback.from_user.id
        bot = callback.bot

        if cta_type == "vpn1":
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🚀 Попробовать VPN — 3 дня бесплатно", url=VPN_BOT_URL),
            ]])
            await bot.send_message(
                admin_id,
                (
                    "Кстати — Instagram, YouTube у вас открываются? 🤔\n\n"
                    "Прокси работает <b>только внутри Telegram</b>. "
                    "Для всех остальных приложений и сайтов нужен VPN.\n\n"
                    "У нас есть @noctovpn_bot — по ссылке дадут <b>3 дня доступа бесплатно</b>, без карты.\n\n"
                    "Попробуйте — терять нечего 👇"
                ),
                reply_markup=kb,
            )

        elif cta_type == "vpn2":
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🚀 Попробовать NoctoVPN бесплатно", url=VPN_BOT_URL),
            ]])
            await bot.send_message(
                admin_id,
                (
                    "Всё ещё пользуетесь нашим прокси? 👍\n\n"
                    "Напоминаем: для Instagram, YouTube и любых сайтов нужен <b>полный VPN</b> — прокси там не поможет.\n\n"
                    "@noctovpn_bot:\n"
                    "• <b>3 дня доступа бесплатно</b>\n"
                    "• Всего <b>179 ₽/мес</b>\n"
                    "• Без карты и лишней регистрации\n\n"
                    "Попробуйте прямо сейчас 👇"
                ),
                reply_markup=kb,
            )

        elif cta_type == "vpn3":
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔥 Забрать 3 дня бесплатно", url=VPN_BOT_URL),
            ]])
            await bot.send_message(
                admin_id,
                (
                    "Последнее напоминание про VPN 🙏\n\n"
                    "@noctovpn_bot по ссылке даёт <b>3 дня доступа бесплатно</b>.\n"
                    "После — <b>179 ₽/мес</b>. Если не понравится — просто не продлевайте.\n\n"
                    "Это дешевле одной поездки на такси, а работает везде:\n"
                    "Instagram, YouTube, любые сайты и приложения 🌍"
                ),
                reply_markup=kb,
            )

        elif cta_type == "channel":
            if not channel_url:
                await callback.answer("CHANNEL_URL не задан в .env", show_alert=True)
                return
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="📣 Подписаться на канал", url=channel_url),
            ]])
            await bot.send_message(
                admin_id,
                (
                    "💡 Знаете ли вы, что прокси-серверы иногда меняются?\n\n"
                    "Чтобы не гадать «почему вдруг перестало работать» — подпишитесь на наш канал. "
                    "Там мы сразу сообщаем о смене серверов, падениях и даём новые адреса.\n\n"
                    "Одна подписка — и прокси всегда будет работать 👇"
                ),
                reply_markup=kb,
                disable_web_page_preview=True,
            )

        else:
            await callback.answer("Неизвестный тип", show_alert=True)
            return

        await callback.answer("✅ Сообщение отправлено — проверьте чат", show_alert=True)
        return

    await callback.answer()


@router.message(Command("cancel"), StateFilter("*"))
async def cancel_admin_state(message: Message, state: FSMContext, admin_ids: set[int], bot: Bot) -> None:
    if not _is_admin(message.from_user.id, admin_ids):
        return

    current = await state.get_state()
    if not current:
        await message.answer("Нет активного действия.")
        return

    data = await state.get_data()
    panel_chat_id = data.get("panel_chat_id")
    panel_message_id = data.get("panel_message_id")
    await _safe_delete_message(message)
    await state.clear()
    if panel_chat_id and panel_message_id:
        await bot.edit_message_text(
            chat_id=panel_chat_id,
            message_id=panel_message_id,
            text="Действие отменено.\n\nАдмин-меню",
            reply_markup=build_admin_menu(),
        )
        return
    await message.answer("Действие отменено.", reply_markup=build_admin_menu())


@router.message(UserSearchForm.query)
async def user_search_query(
    message: Message,
    state: FSMContext,
    admin_ids: set[int],
    storage: Storage,
    bot: Bot,
) -> None:
    if not _is_admin(message.from_user.id, admin_ids):
        return

    query = (message.text or "").strip()
    await _safe_delete_message(message)
    if not query:
        await _edit_panel(
            bot,
            state,
            "Поиск пользователя\n\n"
            "Запрос пустой. Отправьте tg_id, @username или часть имени.",
            build_wizard_keyboard("admin:users:1"),
        )
        return

    normalized = query[1:] if query.startswith("@") else query
    users = await storage.search_users(normalized, limit=10)
    if not users:
        await _edit_panel(
            bot,
            state,
            "Ничего не найдено.",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🔎 Новый поиск", callback_data="admin:users_search")],
                    [InlineKeyboardButton(text="👥 К списку", callback_data="admin:users:1")],
                ]
            ),
        )
        await state.clear()
        return

    text = f"Результаты поиска по запросу: {query}\nНайдено: {len(users)}"
    await _edit_panel(bot, state, text, build_user_search_results_keyboard(users))
    await state.clear()


@router.message(UserWriteForm.text)
async def user_write_message(
    message: Message,
    state: FSMContext,
    admin_ids: set[int],
    storage: Storage,
    bot: Bot,
) -> None:
    if not _is_admin(message.from_user.id, admin_ids):
        return

    text_to_send = (message.text or "").strip()
    await _safe_delete_message(message)
    data = await state.get_data()
    target_tg_id = int(data.get("write_target_tg_id", 0))
    page = int(data.get("write_back_page", 1))
    source = str(data.get("write_source", "l"))
    if not target_tg_id:
        await state.clear()
        await message.answer("Не удалось определить получателя.", reply_markup=build_admin_menu())
        return

    if not text_to_send:
        await _edit_panel(
            bot,
            state,
            "Сообщение пользователю\n\nТекст пустой. Отправьте текст одним сообщением.",
            build_wizard_keyboard(f"admin:user:{target_tg_id}:{page}:{source}"),
        )
        return

    user_data = await storage.get_user_by_tg_id(target_tg_id)
    if user_data is None:
        await _edit_panel(
            bot,
            state,
            "Пользователь не найден.",
            InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ В меню", callback_data="admin:menu")]]
            ),
        )
        await state.clear()
        return

    try:
        await bot.send_message(chat_id=target_tg_id, text=text_to_send, disable_web_page_preview=True)
        result_text = f"Сообщение отправлено пользователю {target_tg_id}."
    except (TelegramForbiddenError, TelegramBadRequest):
        result_text = f"Не удалось отправить сообщение пользователю {target_tg_id}."

    username = user_data["username"]
    username_text = f"@{username}" if username else "-"
    full_name = user_data["full_name"] or "-"
    profile_text = (
        "Профиль пользователя\n"
        f"- tg_id: {user_data['tg_id']}\n"
        f"- username: {username_text}\n"
        f"- имя: {full_name}\n"
        f"- first_seen: {user_data['first_seen']}\n"
        f"- дней в боте: {_days_since(str(user_data['first_seen']))}\n\n"
        f"{result_text}"
    )
    await _edit_panel(
        bot,
        state,
        profile_text,
        build_user_profile_keyboard(target_tg_id, page, source),
    )
    await state.clear()


@router.message(ChannelInviteForm.text)
async def channel_invite_update_text(
    message: Message,
    state: FSMContext,
    admin_ids: set[int],
    storage: Storage,
    bot: Bot,
) -> None:
    if not _is_admin(message.from_user.id, admin_ids):
        return

    new_text = (message.html_text or message.text or "").strip()
    await _safe_delete_message(message)
    if not new_text:
        await _edit_panel(
            bot,
            state,
            "<b>Текст не может быть пустым.</b>\n\nОтправьте новый текст приглашения.",
            build_wizard_keyboard("admin:channel_invite"),
        )
        return

    await storage.set_channel_invite_text(new_text)
    await _edit_panel(
        bot,
        state,
        build_channel_invite_screen_text(new_text),
        build_channel_invite_menu(),
    )
    await state.clear()


@router.message(AddProxyForm.name)
async def add_proxy_name(message: Message, state: FSMContext, admin_ids: set[int], bot: Bot) -> None:
    if not _is_admin(message.from_user.id, admin_ids):
        return

    name = (message.text or "").strip()
    await _safe_delete_message(message)
    if not name:
        await _edit_panel(
            bot,
            state,
            "Название не может быть пустым.\n\n" + _add_step_text("name", await state.get_data()),
            build_wizard_keyboard("admin:menu"),
        )
        return

    await state.update_data(name=name)
    data = await state.get_data()
    await state.set_state(AddProxyForm.server)
    await _edit_panel(
        bot,
        state,
        _add_step_text("server", data),
        build_wizard_keyboard("admin:add:back"),
    )


@router.message(AddProxyForm.server)
async def add_proxy_server(message: Message, state: FSMContext, admin_ids: set[int], bot: Bot) -> None:
    if not _is_admin(message.from_user.id, admin_ids):
        return

    server = (message.text or "").strip()
    await _safe_delete_message(message)
    if not server:
        await _edit_panel(
            bot,
            state,
            "Server не может быть пустым.\n\n" + _add_step_text("server", await state.get_data()),
            build_wizard_keyboard("admin:add:back"),
        )
        return

    await state.update_data(server=server)
    data = await state.get_data()
    await state.set_state(AddProxyForm.port)
    await _edit_panel(
        bot,
        state,
        _add_step_text("port", data),
        build_wizard_keyboard("admin:add:back"),
    )


@router.message(AddProxyForm.port)
async def add_proxy_port(message: Message, state: FSMContext, admin_ids: set[int], bot: Bot) -> None:
    if not _is_admin(message.from_user.id, admin_ids):
        return

    raw_port = (message.text or "").strip()
    await _safe_delete_message(message)
    if not raw_port.isdigit():
        await _edit_panel(
            bot,
            state,
            "Порт должен быть числом.\n\n" + _add_step_text("port", await state.get_data()),
            build_wizard_keyboard("admin:add:back"),
        )
        return

    port = int(raw_port)
    if port < 1 or port > 65535:
        await _edit_panel(
            bot,
            state,
            "Порт должен быть от 1 до 65535.\n\n" + _add_step_text("port", await state.get_data()),
            build_wizard_keyboard("admin:add:back"),
        )
        return

    await state.update_data(port=port)
    data = await state.get_data()
    await state.set_state(AddProxyForm.secret)
    await _edit_panel(
        bot,
        state,
        _add_step_text("secret", data),
        build_wizard_keyboard("admin:add:back"),
    )


@router.message(AddProxyForm.secret)
async def add_proxy_secret(
    message: Message,
    state: FSMContext,
    admin_ids: set[int],
    proxy_store: ProxyStore,
    bot: Bot,
) -> None:
    if not _is_admin(message.from_user.id, admin_ids):
        return

    secret = (message.text or "").strip()
    await _safe_delete_message(message)
    if not secret:
        await _edit_panel(
            bot,
            state,
            "Secret не может быть пустым.\n\n" + _add_step_text("secret", await state.get_data()),
            build_wizard_keyboard("admin:add:back"),
        )
        return

    data = await state.get_data()
    proxies = proxy_store.load_all()
    new_proxy = ProxyItem(
        name=data["name"],
        server=data["server"],
        port=int(data["port"]),
        secret=secret,
        enabled=True,
    )
    proxies.append(new_proxy)
    proxy_store.save_all(proxies)

    panel_chat_id = data.get("panel_chat_id")
    panel_message_id = data.get("panel_message_id")
    await state.clear()
    if panel_chat_id and panel_message_id:
        await bot.edit_message_text(
            chat_id=panel_chat_id,
            message_id=panel_message_id,
            text=(
                "Прокси добавлен и включен.\n"
                f"Название: {new_proxy.name}\n"
                f"Подключить: {new_proxy.tme_link}\n"
                f"tg://: {new_proxy.tg_link}"
            ),
            reply_markup=build_admin_menu(),
            disable_web_page_preview=True,
        )
        return
    await message.answer("Прокси добавлен.", reply_markup=build_admin_menu())


@router.message(EditProxyNameForm.name)
async def edit_proxy_name(
    message: Message,
    state: FSMContext,
    admin_ids: set[int],
    proxy_store: ProxyStore,
    bot: Bot,
) -> None:
    if not _is_admin(message.from_user.id, admin_ids):
        return

    new_name = (message.text or "").strip()
    await _safe_delete_message(message)
    data = await state.get_data()
    idx = int(data.get("edit_proxy_idx", -1))

    if not new_name:
        await _edit_panel(
            bot, state,
            "Название не может быть пустым. Отправьте новое название:",
            build_wizard_keyboard(f"admin:proxy:{idx}"),
        )
        return

    proxies = proxy_store.load_all()
    if idx < 0 or idx >= len(proxies):
        await _edit_panel(bot, state, "Прокси не найден.", InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ В меню", callback_data="admin:menu")]]
        ))
        await state.clear()
        return

    proxies[idx].name = new_name
    proxy_store.save_all(proxies)

    proxy = proxies[idx]
    first_enabled_idx = next((i for i, p in enumerate(proxies) if p.enabled), -1)
    is_main = (idx == first_enabled_idx)
    await _edit_panel(bot, state, _proxy_card_text(idx, proxy, is_main), build_proxy_card_keyboard(idx, proxy, is_main))
    await state.clear()


@router.message(AddProxyFromLinkForm.link)
async def add_proxy_from_link_step(
    message: Message,
    state: FSMContext,
    admin_ids: set[int],
    bot: Bot,
) -> None:
    if not _is_admin(message.from_user.id, admin_ids):
        return

    raw = (message.text or "").strip()
    await _safe_delete_message(message)

    parsed = _parse_proxy_link(raw)
    if not parsed:
        await _edit_panel(
            bot, state,
            "<b>Не удалось распознать ссылку.</b>\n\n"
            "Ожидается формат:\n"
            "<code>https://t.me/proxy?server=...&port=...&secret=...</code>\n\n"
            "Попробуйте ещё раз:",
            build_wizard_keyboard("admin:add"),
        )
        return

    server, port, secret = parsed
    await state.update_data(link_server=server, link_port=port, link_secret=secret)
    await state.set_state(AddProxyFromLinkForm.name)
    await _edit_panel(
        bot, state,
        f"<b>Ссылка принята ✅</b>\n\n"
        f"• Сервер: <code>{server}</code>\n"
        f"• Порт: <code>{port}</code>\n\n"
        "Теперь отправьте название для этого прокси (например: <code>Сервер #1</code>):",
        build_wizard_keyboard("admin:add"),
    )


@router.message(AddProxyFromLinkForm.name)
async def add_proxy_from_link_name(
    message: Message,
    state: FSMContext,
    admin_ids: set[int],
    proxy_store: ProxyStore,
    bot: Bot,
) -> None:
    if not _is_admin(message.from_user.id, admin_ids):
        return

    name = (message.text or "").strip()
    await _safe_delete_message(message)

    if not name:
        data = await state.get_data()
        server = data.get("link_server", "?")
        port = data.get("link_port", "?")
        await _edit_panel(
            bot, state,
            f"Название не может быть пустым.\n\nСервер: <code>{server}:{port}</code>\n\nОтправьте название:",
            build_wizard_keyboard("admin:add"),
        )
        return

    data = await state.get_data()
    new_proxy = ProxyItem(
        name=name,
        server=str(data["link_server"]),
        port=int(data["link_port"]),
        secret=str(data["link_secret"]),
        enabled=True,
    )
    proxies = proxy_store.load_all()
    proxies.append(new_proxy)
    proxy_store.save_all(proxies)

    panel_chat_id = data.get("panel_chat_id")
    panel_message_id = data.get("panel_message_id")
    idx = len(proxies) - 1
    first_enabled_idx = next((i for i, p in enumerate(proxies) if p.enabled), -1)
    is_main = (idx == first_enabled_idx)
    await state.clear()
    if panel_chat_id and panel_message_id:
        await bot.edit_message_text(
            chat_id=panel_chat_id,
            message_id=panel_message_id,
            text=_proxy_card_text(idx, new_proxy, is_main),
            reply_markup=build_proxy_card_keyboard(idx, new_proxy, is_main),
            disable_web_page_preview=True,
        )
        return
    await message.answer("Прокси добавлен.", reply_markup=build_admin_menu())


@router.message(BroadcastForm.media)
async def prepare_broadcast(
    message: Message,
    state: FSMContext,
    admin_ids: set[int],
    bot: Bot,
) -> None:
    if not _is_admin(message.from_user.id, admin_ids):
        return

    photo_id: str | None = None
    if message.photo:
        photo_id = message.photo[-1].file_id
        text = (message.caption or "").strip()
    else:
        text = (message.text or "").strip()

    await _safe_delete_message(message)

    if not text and not photo_id:
        await _edit_panel(
            bot, state,
            "<b>Новая рассылка</b>\n\nТекст или фото не найдены. Отправьте текст или фото с подписью.",
            build_wizard_keyboard("admin:menu"),
        )
        return

    await state.update_data(broadcast_text=text, broadcast_photo_id=photo_id)
    await state.set_state(BroadcastForm.confirm)

    media_label = "📷 Фото" + (" + подпись" if text else "") if photo_id else "📝 Текст"
    preview = _cut_text(text, 300) if text else "(без текста)"
    await _edit_panel(
        bot, state,
        (
            f"<b>Предпросмотр рассылки</b>\n\n"
            f"{media_label}:\n{preview}\n\n"
            "Выберите действие:"
        ),
        build_broadcast_confirm_keyboard(),
    )


@router.message(BroadcastForm.buttons)
async def receive_broadcast_buttons(
    message: Message,
    state: FSMContext,
    admin_ids: set[int],
    storage: Storage,
    bot: Bot,
    broadcast_workers: int,
) -> None:
    if not _is_admin(message.from_user.id, admin_ids):
        return

    raw_buttons = (message.text or "").strip()
    await _safe_delete_message(message)
    data = await state.get_data()
    text = str(data.get("broadcast_text", "")).strip()
    photo_id = data.get("broadcast_photo_id")
    panel_chat_id = int(data.get("panel_chat_id") or 0)
    panel_message_id = int(data.get("panel_message_id") or 0)

    if not text and not photo_id:
        await state.clear()
        try:
            await bot.edit_message_text(
                chat_id=panel_chat_id,
                message_id=panel_message_id,
                text="Данные рассылки потеряны. Запустите рассылку заново.",
                reply_markup=build_admin_menu(),
            )
        except TelegramBadRequest:
            pass
        return

    me = await bot.get_me()
    share_url = _build_share_url(me.username, _plain_text(text))
    keyboard, parse_error = _parse_broadcast_buttons(raw_buttons, share_url)
    if parse_error:
        await _edit_panel(
            bot, state,
            (
                f"<b>Ошибка в кнопках</b>\n{parse_error}\n\n"
                "Формат: <code>Текст кнопки | URL</code>\n"
                "Или отправьте <code>нет</code>, чтобы отправить без кнопок."
            ),
            build_wizard_keyboard("admin:menu"),
        )
        return

    await state.update_data(broadcast_buttons_raw=raw_buttons)
    await state.set_state(BroadcastForm.confirm)

    has_buttons = keyboard is not None
    media_label = "📷 Фото" + (" + подпись" if text else "") if photo_id else "📝 Текст"
    if has_buttons:
        media_label += " + кнопки ✅"
    preview = _cut_text(text, 300) if text else "(без текста)"
    await _edit_panel(
        bot, state,
        (
            f"<b>Предпросмотр рассылки</b>\n\n"
            f"{media_label}:\n{preview}\n\n"
            "Выберите аудиторию:"
        ),
        build_broadcast_confirm_keyboard(),
    )
