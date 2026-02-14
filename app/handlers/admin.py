from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.services.proxy_links import ProxyItem, ProxyStore
from app.services.storage import Storage

router = Router()
USERS_PAGE_SIZE = 10


class AddProxyForm(StatesGroup):
    name = State()
    server = State()
    port = State()
    secret = State()


class BroadcastForm(StatesGroup):
    text = State()


class UserSearchForm(StatesGroup):
    query = State()


class UserWriteForm(StatesGroup):
    text = State()


def _is_admin(user_id: int, admin_ids: set[int]) -> bool:
    return user_id in admin_ids


def _humanize_last_seen(last_seen: str) -> str:
    try:
        dt = datetime.strptime(last_seen, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return "неизвестно"

    sec = int((datetime.now(timezone.utc) - dt).total_seconds())
    if sec < 60:
        return "только что"
    if sec < 3600:
        return f"{sec // 60} м. назад"
    if sec < 86400:
        return f"{sec // 3600} ч. назад"
    return f"{sec // 86400} дн. назад"


def _days_since(first_seen: str) -> int:
    try:
        dt = datetime.strptime(first_seen, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return 0
    return max(0, (datetime.now(timezone.utc) - dt).days)


def build_admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📋 Список прокси", callback_data="admin:list")],
            [InlineKeyboardButton(text="➕ Добавить прокси", callback_data="admin:add")],
            [InlineKeyboardButton(text="📣 Рассылка", callback_data="admin:broadcast")],
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats")],
            [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin:users")],
        ]
    )


def build_proxy_manage_keyboard(proxies: list[ProxyItem]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for idx, proxy in enumerate(proxies):
        action_text = "⛔ Выкл" if proxy.enabled else "✅ Вкл"
        rows.append(
            [
                InlineKeyboardButton(text=f"{action_text} {proxy.name}", callback_data=f"admin:toggle:{idx}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin:delete:{idx}"),
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
        label = f"{status_icon} {user_text} | {_humanize_last_seen(str(user['last_seen']))}"
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
            [InlineKeyboardButton(text="🤝 Рефералы", callback_data=f"admin:ur:{tg_id}:{page}:{source}")],
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


@router.message(Command("admin"))
async def cmd_admin(message: Message, admin_ids: set[int], state: FSMContext) -> None:
    if not _is_admin(message.from_user.id, admin_ids):
        await message.answer("Недостаточно прав.")
        return

    await state.clear()
    await message.answer("Админ-меню", reply_markup=build_admin_menu())


@router.callback_query(F.data.startswith("admin:"))
async def cb_admin_actions(
    callback: CallbackQuery,
    admin_ids: set[int],
    proxy_store: ProxyStore,
    storage: Storage,
    state: FSMContext,
) -> None:
    if not _is_admin(callback.from_user.id, admin_ids):
        await callback.answer("Недостаточно прав", show_alert=True)
        return

    action = callback.data.split(":")

    if action[1] == "menu":
        await state.clear()
        await callback.message.edit_text("Админ-меню", reply_markup=build_admin_menu())
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

        lines = ["Прокси:"]
        for proxy in proxies:
            status = "enabled" if proxy.enabled else "disabled"
            lines.append(f"- {proxy.name} ({proxy.server}:{proxy.port}) [{status}]")

        kb = build_proxy_manage_keyboard(proxies)
        await callback.message.edit_text("\n".join(lines), reply_markup=kb)
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
        await callback.answer(f"{target.name}: {'enabled' if target.enabled else 'disabled'}")

        lines = ["Прокси:"]
        for proxy in proxies:
            status = "enabled" if proxy.enabled else "disabled"
            lines.append(f"- {proxy.name} ({proxy.server}:{proxy.port}) [{status}]")

        kb = build_proxy_manage_keyboard(proxies)
        await callback.message.edit_text("\n".join(lines), reply_markup=kb)
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
            await callback.message.edit_text("Прокси удален. Список теперь пуст.", reply_markup=back)
            await callback.answer("Удалено")
            return

        lines = ["Прокси:"]
        for proxy in proxies:
            status = "enabled" if proxy.enabled else "disabled"
            lines.append(f"- {proxy.name} ({proxy.server}:{proxy.port}) [{status}]")

        kb = build_proxy_manage_keyboard(proxies)
        await callback.message.edit_text("\n".join(lines), reply_markup=kb)
        await callback.answer(f"Удален: {removed.name}")
        return

    if action[1] == "add" and len(action) == 2:
        await state.clear()
        await state.set_state(AddProxyForm.name)
        await state.update_data(name="", server="", port="")
        await _save_panel_ref(state, callback)
        await callback.message.edit_text(
            _add_step_text("name", {}),
            reply_markup=build_wizard_keyboard("admin:menu"),
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
                build_wizard_keyboard("admin:menu"),
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

    if action[1] == "broadcast" and len(action) == 2:
        await state.clear()
        await state.set_state(BroadcastForm.text)
        await _save_panel_ref(state, callback)
        await callback.message.edit_text(
            "Рассылка\n\nОтправьте текст одним сообщением.",
            reply_markup=build_wizard_keyboard("admin:menu"),
        )
        await callback.answer()
        return

    if action[1] == "stats":
        await state.clear()
        total_users = await storage.count_users()
        active_users = await storage.count_active_users_last_hours(24)
        new_users = await storage.count_new_users_last_hours(24)
        referred_users = await storage.count_users_with_referrer()

        text = (
            "Статистика пользователей:\n"
            f"- всего пользователей: {total_users}\n"
            f"- активные за 24ч: {active_users}\n"
            f"- новые за 24ч: {new_users}\n"
            f"- пришли по приглашению: {referred_users}"
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
        referred_users = await storage.count_users_with_referrer()
        text = (
            f"👥 Список пользователей (стр. {page}/{total_pages})\n\n"
            f"Всего: {total_users} | По приглашению: {referred_users}\n"
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

        invited_count = await storage.count_invited_by(tg_id)
        username = user_data["username"]
        username_text = f"@{username}" if username else "-"
        full_name = user_data["full_name"] or "-"
        invited_by = user_data["invited_by"]
        invited_by_text = str(invited_by) if invited_by is not None else "-"
        blocked = bool(user_data.get("is_blocked"))
        status_text = "⛔ Ограничен" if blocked else "✅ Активен"
        text = (
            "Профиль пользователя\n"
            f"- статус: {status_text}\n"
            f"- tg_id: {user_data['tg_id']}\n"
            f"- username: {username_text}\n"
            f"- имя: {full_name}\n"
            f"- invited_by: {invited_by_text}\n"
            f"- пригласил: {invited_count}\n"
            f"- first_seen: {user_data['first_seen']}\n"
            f"- last_seen: {_humanize_last_seen(str(user_data['last_seen']))}\n"
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

    if action[1] == "ur":
        if len(action) < 5 or not action[2].isdigit() or not action[3].isdigit():
            await callback.answer("Некорректные данные", show_alert=True)
            return

        tg_id = int(action[2])
        page = int(action[3])
        source = action[4] if action[4] in {"l", "s"} else "l"
        refs = await storage.get_referred_users(tg_id, limit=20)
        invited_count = await storage.count_invited_by(tg_id)
        lines = [f"Рефералы пользователя {tg_id}", f"Всего приглашено: {invited_count}", ""]
        if not refs:
            lines.append("Список пуст")
        else:
            for user in refs:
                uname = user["username"]
                title = f"@{uname}" if uname else str(user["tg_id"])
                lines.append(f"- {title} | {_humanize_last_seen(str(user['last_seen']))}")
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ К профилю", callback_data=f"admin:user:{tg_id}:{page}:{source}")],
                    [InlineKeyboardButton(text="🏠 В меню", callback_data="admin:menu")],
                ]
            ),
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

        invited_count = await storage.count_invited_by(tg_id)
        username = updated["username"]
        username_text = f"@{username}" if username else "-"
        full_name = updated["full_name"] or "-"
        invited_by = updated["invited_by"]
        invited_by_text = str(invited_by) if invited_by is not None else "-"
        status_text = "⛔ Ограничен" if bool(updated.get("is_blocked")) else "✅ Активен"
        text = (
            "Профиль пользователя\n"
            f"- статус: {status_text}\n"
            f"- tg_id: {updated['tg_id']}\n"
            f"- username: {username_text}\n"
            f"- имя: {full_name}\n"
            f"- invited_by: {invited_by_text}\n"
            f"- пригласил: {invited_count}\n"
            f"- first_seen: {updated['first_seen']}\n"
            f"- last_seen: {_humanize_last_seen(str(updated['last_seen']))}\n"
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
        referred_users = await storage.count_users_with_referrer()
        text = (
            f"👥 Список пользователей (стр. {page}/{total_pages})\n\n"
            f"Всего: {total_users} | По приглашению: {referred_users}\n"
            "Нажмите на пользователя для управления:"
        )
        keyboard = build_users_keyboard(users=users, page=page, total_pages=total_pages)
        await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer("Удалено")
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

    invited_count = await storage.count_invited_by(target_tg_id)
    username = user_data["username"]
    username_text = f"@{username}" if username else "-"
    full_name = user_data["full_name"] or "-"
    invited_by = user_data["invited_by"]
    invited_by_text = str(invited_by) if invited_by is not None else "-"
    profile_text = (
        "Профиль пользователя\n"
        f"- tg_id: {user_data['tg_id']}\n"
        f"- username: {username_text}\n"
        f"- имя: {full_name}\n"
        f"- invited_by: {invited_by_text}\n"
        f"- пригласил: {invited_count}\n"
        f"- first_seen: {user_data['first_seen']}\n"
        f"- last_seen: {_humanize_last_seen(str(user_data['last_seen']))}\n"
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


@router.message(BroadcastForm.text)
async def send_broadcast(
    message: Message,
    state: FSMContext,
    admin_ids: set[int],
    storage: Storage,
    bot: Bot,
) -> None:
    if not _is_admin(message.from_user.id, admin_ids):
        return

    text = (message.text or "").strip()
    await _safe_delete_message(message)
    if not text:
        await _edit_panel(
            bot,
            state,
            "Текст пустой.\n\nРассылка\n\nОтправьте текст одним сообщением.",
            build_wizard_keyboard("admin:menu"),
        )
        return

    await _edit_panel(bot, state, "Рассылка в процессе...", build_wizard_keyboard("admin:menu"))

    user_ids = await storage.get_all_user_ids()
    success = 0
    failed = 0

    for tg_id in user_ids:
        try:
            await bot.send_message(chat_id=tg_id, text=text, disable_web_page_preview=True)
            success += 1
        except (TelegramForbiddenError, TelegramBadRequest):
            failed += 1

    data = await state.get_data()
    panel_chat_id = data.get("panel_chat_id")
    panel_message_id = data.get("panel_message_id")
    await state.clear()
    if panel_chat_id and panel_message_id:
        await bot.edit_message_text(
            chat_id=panel_chat_id,
            message_id=panel_message_id,
            text=(
                "Рассылка завершена.\n"
                f"Получателей в базе: {len(user_ids)}\n"
                f"Успешно отправлено: {success}\n"
                f"Ошибок: {failed}"
            ),
            reply_markup=build_admin_menu(),
        )
        return
    await message.answer("Рассылка завершена.", reply_markup=build_admin_menu())
