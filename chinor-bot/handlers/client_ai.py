"""Mijoz uchun erkin AI suhbat — botga yozilgan har qanday matnga javob.

Ilgari AI konsultant «💬 AI sotuvchi» tugmasi ortida, alohida holatda (FSM)
turardi: mijoz avval tugmani bosishi, keyin savol yozishi kerak edi. Endi
suhbat STANDART holat — mijoz shunchaki yozadi va javob oladi, xuddi odam
bilan gaplashgandek.

Bu router ENG OXIRIDA ro'yxatdan o'tadi (main.py), shuning uchun barcha aniq
ishlovchilar (tugmalar, holatlar, buyruqlar) avval ishlaydi; bu yerga faqat
"boshqa hech kim olmagan" matn tushadi.

MAXFIYLIK: AI tashqi xizmat, shuning uchun unga do'konning ichki ma'lumoti
YUBORILMAYDI. Faqat mijozning o'zi javonda ko'radigan narsa ketadi —
tovar nomi, sotuv narxi, bor-yo'qligi. Tannarx, foyda, yetkazib beruvchi,
boshqa mijozlar, qarzlar, savdo tarixi — hech qachon.
"""

import logging

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.config import GLAVNIY_ADMIN_ID
from bot.keyboards import RESERVED_MENU
from database import db
from handlers.client import client_consult_input

logger = logging.getLogger(__name__)
router = Router()

# Juda qisqa yoki juda uzun matnlarni AI ga yubormaymiz.
_MIN_LEN = 2
_MAX_LEN = 500


@router.message(StateFilter(None), F.text)
async def client_free_chat(message: Message, state: FSMContext):
    """Mijozning erkin matni → AI sotuvchi javobi.

    Faqat holatsiz (StateFilter(None)) matnlarga ishlaydi — ya'ni mijoz biror
    jarayon o'rtasida bo'lsa (buyurtma, qidiruv) bu yerga tushmaydi.
    """
    q = (message.text or "").strip()
    if not q or q.startswith("/"):
        return
    if q in RESERVED_MENU:
        return
    if len(q) < _MIN_LEN or len(q) > _MAX_LEN:
        return

    uid = message.from_user.id

    # Adminlar bu yerga tushmaydi — ularda o'z oqimlari bor.
    if uid == GLAVNIY_ADMIN_ID or await db.is_admin(uid):
        return

    # Faqat ro'yxatdan o'tgan mijoz. O'tmagani /start orqali kontakt
    # yuborish bosqichiga boradi (glavniy.py), bu yerga kelmaydi.
    client = await db.get_client_by_tg(uid)
    if not client:
        return

    if not db.is_ai_consult_enabled():
        return

    try:
        await client_consult_input(message, state)
    except Exception as e:
        logger.warning("client_free_chat xato (uid=%s): %s", uid, e)
        await message.answer(
            "⚠️ Hozir javob bera olmadim. Birozdan keyin qaytadan yozing."
        )
