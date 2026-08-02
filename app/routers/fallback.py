"""Останній роутер: усе, що ніхто не впіймав."""

from aiogram import Router
from aiogram.types import Message

from app import texts

router = Router(name="fallback")


@router.message()
async def unknown(message: Message) -> None:
    await message.answer(texts.UNKNOWN)
