from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.services.proxy_links import ProxyItem, ProxyStore
from app.services.storage import Storage

router = Router()


class AddProxyForm(StatesGroup):
    name = State()
    server = State()
    port = State()
    secret = State()


class BroadcastForm(StatesGroup):
    text = State()


def _is_admin(user_id: int, admin_ids: set[int]) -> bool:
    return user_id in admin_ids


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
        total_users = await storage.count_users()
        top_referrers = await storage.get_top_referrers(10)
        recent_users = await storage.get_recent_users(12)

        lines = [f"Пользователи: {total_users}", ""]
        lines.append("Топ пригласивших:")
        if not top_referrers:
            lines.append("- пока нет приглашений")
        else:
            for ref_id, invited_count in top_referrers:
                lines.append(f"- {ref_id}: {invited_count}")

        lines.append("")
        lines.append("Последние активные:")
        if not recent_users:
            lines.append("- список пуст")
        else:
            for user_row in recent_users:
                username = user_row["username"]
                username_text = f"@{username}" if username else "без username"
                invited_by = user_row["invited_by"]
                invited_by_text = str(invited_by) if invited_by is not None else "-"
                invited_count = await storage.count_invited_by(int(user_row["tg_id"]))
                lines.append(
                    f"- {user_row['tg_id']} ({username_text}) | invited_by: {invited_by_text} | invited: {invited_count}"
                )

        back = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:menu")]]
        )
        await callback.message.edit_text("\n".join(lines), reply_markup=back)
        await callback.answer()
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
