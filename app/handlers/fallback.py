from __future__ import annotations

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from app.handlers.admin import build_admin_menu
from app.handlers.start import _main_menu_text, build_start_keyboard
from app.services.proxy_links import ProxyStore
from app.services.storage import Storage

router = Router()


async def _safe_edit(callback: CallbackQuery, text: str, reply_markup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise


@router.callback_query()
async def cb_fallback(
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

    data = callback.data or ""

    if data.startswith("admin:") and user.id in admin_ids:
        await _safe_edit(callback, "Админ-меню", build_admin_menu())
        await callback.answer("Кнопка устарела, открыл актуальное меню")
        return

    enabled = proxy_store.load_enabled()
    if not enabled:
        await _safe_edit(
            callback,
            "Сейчас прокси временно недоступен.\n"
            f"Поддержка: https://t.me/{support_username}",
            None,
        )
        await callback.answer("Кнопка устарела")
        return

    main_proxy = enabled[0]
    keyboard = build_start_keyboard(
        main_proxy.tme_link,
        support_username,
        channel_url,
        show_admin_panel=user.id in admin_ids,
    )
    await _safe_edit(callback, _main_menu_text(), keyboard)
    await callback.answer("Кнопка устарела, открыл актуальное меню")
