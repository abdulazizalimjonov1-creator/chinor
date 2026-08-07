"""Muhokama guruhi — kanal posti ostidagi IZOHLAR uchun.

Kanalga muhokama guruhi bog'langan bo'lsa, har bir post o'sha guruhga
avtomatik ko'chiriladi (`is_automatic_forward`). Aynan shu ko'chirmaga javob
yozilsa, u kanalda post ostida IZOH bo'lib ko'rinadi — bizga «qanday
ishlatiladi» qo'llanmasini shu tarzda joylash uchun kerak.

Bu router faqat SHU bitta xabar turini tutadi va boshqa hech narsaga
aralashmaydi. Bot guruhda bo'lmasa — hech qachon ishlamaydi, zarari yo'q.
"""

from aiogram import F, Router
from aiogram.types import Message

from bot import comments

router = Router(name="discussion")


@router.message(F.is_automatic_forward.is_(True))
async def on_channel_post_forwarded(message: Message):
    await comments.on_auto_forward(message)
