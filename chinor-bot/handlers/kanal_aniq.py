"""Kanalni AVTOMATIK aniqlash — bot qaysi kanalga qo'shilganini eslab qoladi.

NEGA KERAK. Kanal id'si `.env` da qo'lda yozilgan. Kanal almashsa
(masalan egasi yangisini ochsa — 2026-10-23 da aynan shunday bo'ldi:
eski kanal yo'qolgan akkauntda qolib ketdi) uni qo'lda topish kerak:
id hech qayerda ko'rinmaydi, faqat API orqali bilinadi.

Endi bot kanalga admin qilib qo'shilganda yoki kanalda biror post
paydo bo'lganda, uning id'si eslab qolinadi va bosh adminga xabar
beriladi — `.env` ni tahrirlash uchun id ni qidirib yurish shart emas.

HECH NARSA AVTOMATIK ALMASHTIRILMAYDI: kanal id'si — e'lonlar qayerga
ketishini belgilaydigan narsa, uni bot o'zicha o'zgartirmasligi kerak.
Faqat aytadi, qaror egasiniki.
"""

import logging

from aiogram import F, Router
from aiogram.types import ChatMemberUpdated, Message

from bot.config import CHANNEL_ID, GLAVNIY_ADMIN_ID
from database.channel_db import db

logger = logging.getLogger(__name__)
router = Router()


async def _eslab_qol(bot, chat, sabab: str) -> None:
    cid = str(chat.id)
    nom = chat.title or "—"
    uname = f"@{chat.username}" if chat.username else "—"

    oldingi = db.get_setting("kanal_aniq_oxirgi", "")
    db.set_setting("kanal_aniq_oxirgi", cid)
    db.set_setting("kanal_aniq_nom", nom)
    logger.info("📢 Kanal aniqlandi (%s): %s %s %s", sabab, cid, nom, uname)

    # Joriy sozlamadagi kanal bilan bir xil bo'lsa — jim o'tamiz.
    if cid == str(CHANNEL_ID) or uname == str(CHANNEL_ID):
        return
    if oldingi == cid:
        return                       # allaqachon aytilgan
    try:
        await bot.send_message(
            GLAVNIY_ADMIN_ID,
            f"📢 <b>Yangi kanal aniqlandi</b>\n\n"
            f"🏷 {nom}  {uname}\n"
            f"🆔 <code>{cid}</code>\n\n"
            f"Hozir e'lonlar <code>{CHANNEL_ID or '—'}</code> ga ketyapti.\n"
            f"Agar e'lonlar SHU kanalga chiqishi kerak bo'lsa, "
            f"yuqoridagi id ni ayting — men sozlamani almashtiraman.",
            parse_mode="HTML")
    except Exception as e:
        logger.info("📢 Kanal haqida xabar yuborilmadi: %s", e)


@router.my_chat_member()
async def bot_qoshildi(event: ChatMemberUpdated):
    """Bot kanal/guruhga qo'shilganda yoki huquqi o'zgarganda."""
    chat = event.chat
    if chat.type not in ("channel", "supergroup", "group"):
        return
    holat = (event.new_chat_member.status or "").lower()
    if holat not in ("administrator", "member"):
        return
    await _eslab_qol(event.bot, chat, f"bot {holat} bo'ldi")


@router.channel_post(F.chat)
async def kanal_posti(message: Message):
    """Kanalda post paydo bo'lsa — id shu yerdan ham bilinadi."""
    await _eslab_qol(message.bot, message.chat, "kanal posti")
