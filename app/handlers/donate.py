from __future__ import annotations

from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram import Router

from app.services.storage import Storage

router = Router()


@router.message(Command("donate"))
async def cmd_donate(
    message: Message,
    tribute_url: str | None,
    storage: Storage,
) -> None:
    user = message.from_user
    await storage.touch_user(
        tg_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )

    if not tribute_url:
        await message.answer("Донаты пока не настроены. Напишите в поддержку, если хотите помочь проекту.")
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❤️ Поддержать через Tribute", url=tribute_url)]]
    )
    await message.answer(
        "Спасибо за поддержку проекта. Нажмите кнопку ниже:",
        reply_markup=keyboard,
    )
