"""Mijozga xabar va fayl yuborish — u qaysi botda bo'lsa, o'shandan.

NEGA IKKI TOKEN. Do'kon mijozlarining ko'pchiligi @cheklars_bot ga
/start bosgan (ilgari o'sha bot kassa kompyuterida ishlardi), qolgani
esa asosiy Chinor botiga. Telegram botga faqat O'ZIGA /start bosgan
odamga yozishga ruxsat beradi: asosiy bot tokeni bilan @cheklars_bot
mijoziga yozilsa «403 Forbidden: bot can't initiate conversation»
keladi va xabar JIM yo'qoladi — aynan shuning uchun «chek yubordim,
lekin mijozga bormadi» holati chiqardi.

Shuning uchun har bir yuborish mavjud TOKENLARNING hammasida sinaladi
va qaysi biri ishlagani eslab qolinadi — keyingi safar birinchi o'sha
sinaladi (bir xil ishni ikki marta qilmaslik uchun).

Bu modul `aiogram` Bot nusxasiga bog'lanmagan: web API bot'siz alohida
jarayon sifatida ham ishlaydi, o'sha paytda ham fayl yuborilishi kerak.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/{method}"

# chat_id → shu chat uchun ishlagan token (keshda, jarayon umri davomida)
_WORKS: dict[str, str] = {}


def _tokens(chat_id: str) -> list:
    """Sinab ko'rish tartibi: avval shu chat uchun ilgari ishlagani."""
    from bot.config import BOT_TOKEN, CHEK_BOT_TOKEN

    hamma = [t for t in (BOT_TOKEN, CHEK_BOT_TOKEN) if t]
    yaxshi = _WORKS.get(str(chat_id))
    if yaxshi and yaxshi in hamma:
        hamma.remove(yaxshi)
        hamma.insert(0, yaxshi)
    return hamma


def _ok(token: str, chat_id: str, data: dict) -> bool:
    if data.get("ok"):
        _WORKS[str(chat_id)] = token
        return True
    return False


async def send_text(chat_id, text: str, reply_markup: Optional[dict] = None,
                    html: bool = True) -> bool:
    """Matnli xabar. Hech qaysi token ishlamasa — False (chaqiruvchi sanaydi)."""
    import aiohttp

    chat_id = str(chat_id or "")
    if not chat_id:
        return False
    payload = {"chat_id": chat_id, "text": text,
               "disable_web_page_preview": True}
    if html:
        payload["parse_mode"] = "HTML"
    if reply_markup:
        payload["reply_markup"] = reply_markup

    oxirgi = ""
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        for token in _tokens(chat_id):
            try:
                async with s.post(_API.format(token=token, method="sendMessage"),
                                  json=payload) as r:
                    data = await r.json()
                if _ok(token, chat_id, data):
                    return True
                oxirgi = str(data.get("description") or r.status)
            except Exception as e:          # tarmoq uzilishi va h.k.
                oxirgi = str(e)
    if oxirgi:
        logger.info("✉️ Xabar yuborilmadi (%s): %s", chat_id, oxirgi)
    return False


async def send_file(chat_id, path: str, caption: str = "",
                    as_photo: bool = False) -> bool:
    """Fayl (hujjat yoki rasm). Rasm `sendPhoto` bilan ketadi — galereyada
    darrov ochiladi; PDF va Excel esa hujjat sifatida."""
    import aiohttp

    chat_id = str(chat_id or "")
    if not chat_id or not path or not os.path.exists(path):
        return False
    if as_photo:
        # Juda uzun rasmni Telegram «photo» qilib qabul qilmaydi —
        # o'shanda hujjat qilib yuboramiz, aks holda xabar umuman
        # yetib bormasdi.
        from bot import debt_export

        as_photo = debt_export.photo_yaroqli(path)
    method = "sendPhoto" if as_photo else "sendDocument"
    field = "photo" if as_photo else "document"
    with open(path, "rb") as f:
        blob = f.read()

    oxirgi = ""
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        for token in _tokens(chat_id):
            form = aiohttp.FormData()
            form.add_field("chat_id", chat_id)
            if caption:
                form.add_field("caption", caption[:1000])
                form.add_field("parse_mode", "HTML")
            form.add_field(field, blob, filename=os.path.basename(path))
            try:
                async with s.post(_API.format(token=token, method=method),
                                  data=form) as r:
                    data = await r.json()
                if _ok(token, chat_id, data):
                    return True
                oxirgi = str(data.get("description") or r.status)
            except Exception as e:
                oxirgi = str(e)
    if oxirgi:
        logger.info("📎 Fayl yuborilmadi (%s): %s", chat_id, oxirgi)
    return False
