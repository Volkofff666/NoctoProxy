from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery

from app.services.storage import Storage

router = Router()


@router.message(Command("help"))
async def cmd_help(
    message: Message,
    support_username: str,
) -> None:
    text = (
        "<b>Это полностью бесплатный MTProto прокси для Telegram.</b>\n"
        "Он помогает подключаться только к Telegram, это не VPN и не влияет на другие приложения.\n"
        "<b>Поделиться прокси:</b> /share\n"
        "Чтобы пригласить друзей по своей ссылке: /invite.\n"
        "Если хотите поддержать проект, используйте /donate.\n"
        f"<b>Поддержка:</b> https://t.me/{support_username}"
    )
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="💬 Поддержка", callback_data="support_click")],
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="user:home")],
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == "support_click")
async def cb_support_click(
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

    url = f"https://t.me/{support_username}"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Открыть поддержку", url=url)]]
    )
    await callback.message.answer(f"Связаться с поддержкой: {url}", reply_markup=keyboard)
    await callback.answer()
