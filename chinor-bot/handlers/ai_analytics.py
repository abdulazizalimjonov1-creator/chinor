"""🤖 AI Analitika — Google Gemini orqali biznes-maslahat.

Bot egasi (admin) menyudan '🤖 AI Analitika' tugmasini bossa — preset
savollardan birini yoki erkin savol berishi mumkin. Bot bazadan kerakli
ma'lumotlarni yig'ib (database/_analytics.py:gather_ai_context), Geminiga
yuboradi va o'zbek tilida amaliy tavsiyalar qaytaradi.

Talab:
  • .env → GEMINI_API_KEY=...
  • pip install google-generativeai
  • ⚙️ Sozlamalar → 🧩 Funksiyalar da 'AI Analitika' yoqilgan bo'lsin
  • Adminda 'ai_analytics' ruxsati bo'lsin (bosh admin har doim)
"""

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from bot.keyboards import ai_menu_kb, cancel_kb
from bot.states import AIChatStates
from bot.permissions import (
    has_permission, is_admin_or_glavniy, get_user_menu, deny,
)
from bot.gemini_analyzer import (
    analyze as ai_analyze, is_available as ai_is_available,
    QUICK_QUESTIONS,
)
from database.channel_db import db

router = Router()

CANCEL = "❌ Bekor qilish"


async def _guard(target) -> bool:
    """Admin + funksiya yoqilgan + ruxsat — uchchalasini tekshiradi."""
    uid = target.from_user.id
    if not await is_admin_or_glavniy(db, uid):
        return False
    if not db.is_ai_analytics_enabled():
        msg = ("ℹ️ AI Analitika funksiyasi o'chirilgan.\n"
               "Bosh admin ⚙️ Sozlamalar → 🧩 Funksiyalar dan yoqishi mumkin.")
        if isinstance(target, CallbackQuery):
            await target.answer(msg, show_alert=True)
        else:
            await target.answer(msg, reply_markup=await get_user_menu(db, uid))
        return False
    if not await has_permission(db, uid, "ai_analytics"):
        await deny(target, db)
        return False
    return True


def _intro_text() -> str:
    return (
        "🤖 <b>AI Analitika</b> (Google Gemini)\n\n"
        "Men sizning do'koningiz ma'lumotlari asosida biznes-maslahat beraman:\n"
        "• 📈 Top sotuvlar va nimani buyurtma qilish kerak\n"
        "• ❓ Mijozlar qidirgan, lekin yo'q tovarlar\n"
        "• 📉 Sotilmayotgan tovarlar — ortiqcha keltirmaslik\n"
        "• 💡 Umumiy biznes maslahat\n\n"
        "<i>Quyidan tanlang yoki o'zingiz savol berisngiz mumkin.</i>"
    )


@router.message(F.text == "🤖 AI Analitika")
async def ai_menu(message: Message, state: FSMContext):
    if not await _guard(message):
        return
    await state.clear()
    # SDK/kalit yo'qligini darrov ko'rsatamiz — adminga sozlash bo'yicha xabar
    ok, why = ai_is_available()
    if not ok:
        await message.answer(why, parse_mode="HTML",
                              reply_markup=await get_user_menu(db, message.from_user.id))
        return
    await message.answer(_intro_text(), reply_markup=ai_menu_kb(), parse_mode="HTML")


async def _run_ai(target, question: str, days: int = 30):
    """Kontekstni yig'ib, Geminidan javob olib, foydalanuvchiga ko'rsatadi."""
    msg = target.message if isinstance(target, CallbackQuery) else target
    # Tezkor 'kutib turing' xabari
    wait_msg = await msg.answer("⏳ Tahlil qilinmoqda… (5–20 sekund)")
    try:
        context = await db.gather_ai_context(days=days)
    except Exception as e:
        await wait_msg.edit_text(f"⚠️ Ma'lumotlarni yig'ishda xato: <code>{e}</code>",
                                  parse_mode="HTML")
        return
    answer = await ai_analyze(question, context)
    # Telegram xabari ~4096 belgi. Kerak bo'lsa bo'laklab yuboramiz.
    try:
        await wait_msg.edit_text(answer, parse_mode="HTML",
                                  disable_web_page_preview=True)
    except Exception:
        # parse_mode xato yoki HTML buzilgan bo'lsa — oddiy matn
        try:
            await wait_msg.edit_text(answer)
        except Exception:
            await msg.answer(answer[:4000])


@router.callback_query(F.data == "ai_top")
async def ai_top(cb: CallbackQuery):
    if not await _guard(cb):
        return
    await cb.answer()
    await _run_ai(cb, QUICK_QUESTIONS["top"])


@router.callback_query(F.data == "ai_misses")
async def ai_misses(cb: CallbackQuery):
    if not await _guard(cb):
        return
    await cb.answer()
    await _run_ai(cb, QUICK_QUESTIONS["misses"])


@router.callback_query(F.data == "ai_slow")
async def ai_slow(cb: CallbackQuery):
    if not await _guard(cb):
        return
    await cb.answer()
    await _run_ai(cb, QUICK_QUESTIONS["slow"])


@router.callback_query(F.data == "ai_general")
async def ai_general(cb: CallbackQuery):
    if not await _guard(cb):
        return
    await cb.answer()
    await _run_ai(cb, QUICK_QUESTIONS["general"])


@router.callback_query(F.data == "ai_ask")
async def ai_ask_start(cb: CallbackQuery, state: FSMContext):
    if not await _guard(cb):
        return
    await state.set_state(AIChatStates.question)
    await cb.message.answer(
        "💬 <b>Savolingizni yozing</b>\n\n"
        "Masalan:\n"
        "• <i>«Qaysi tovarlardan eng ko'p foyda ko'ryapman?»</i>\n"
        "• <i>«Qaysi mijozlarga qarzni qaytarish bo'yicha urg'u berishim kerak?»</i>\n"
        "• <i>«Yangi yil oldidan qaysi tovarlardan ko'proq olib kelishim kerak?»</i>",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )
    await cb.answer()


@router.message(AIChatStates.question)
async def ai_ask_input(message: Message, state: FSMContext):
    if message.text == CANCEL:
        await state.clear()
        await message.answer("Bekor.", reply_markup=await get_user_menu(db, message.from_user.id))
        return
    q = (message.text or "").strip()
    if not q:
        await message.answer("⚠️ Savol bo'sh bo'lmasin. Qaytadan yozing:")
        return
    if len(q) > 500:
        await message.answer("⚠️ Savol juda uzun — 500 belgigacha qisqartiring.")
        return
    await state.clear()
    await _run_ai(message, q)
    # Yana savol berishi uchun menyu chiqaramiz
    await message.answer(
        "Yana savolingiz bormi?",
        reply_markup=ai_menu_kb()
    )
