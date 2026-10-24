"""G'oyadan rolik: egasi ovozli xabar yuboradi yoki /goya deb yozadi.

Oqim: ovoz → matn → reja («qanday rolik + qaysi tovar») → admin bir
tugma bilan tasdiqlaydi → rolik yasaladi.

Nega tasdiq: ovoz noto'g'ri eshitilishi mumkin, rolik esa bir necha
daqiqa va AI kvotasi sarflaydi. Bir tugma ikkalasini ham tejaydi.
"""

import asyncio
import logging
import time

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (CallbackQuery, Message, InlineKeyboardMarkup,
                           InlineKeyboardButton)

from bot import idea_reel
from database.channel_db import db

logger = logging.getLogger(__name__)
router = Router()

# Tasdiq kutayotgan rejalar: {id: (reja, vaqt)}. Qisqa umrli — bot qayta
# ishga tushsa yo'qoladi, bu muammo emas: g'oyani qayta aytish bir soniya.
_PLANS = {}
_PLAN_TTL = 3600

TURI_NOMI = {"ad": "🎬 Reklama roliki", "compare": "⚖️ Taqqoslash",
             "talk": "🗣 Suhbat roliki"}


async def _is_admin(uid: int) -> bool:
    from bot.config import GLAVNIY_ADMIN_ID
    return uid == GLAVNIY_ADMIN_ID or await db.is_admin(uid)


def _clean() -> None:
    eski = [k for k, (_p, t) in _PLANS.items() if time.time() - t > _PLAN_TTL]
    for k in eski:
        _PLANS.pop(k, None)


async def _show_plan(msg: Message, text: str) -> None:
    """Matn → reja → tasdiq kartasi."""
    plan = await idea_reel.understand(db, text)
    if not plan.get("pids"):
        await msg.reply(f"🤔 Tushunmadim: «{text}»\n\n"
                        f"{plan.get('warn') or 'Tovar nomini aniqroq ayting.'}\n\n"
                        f"Masalan: «Ariel bilan Tide ni solishtir» yoki "
                        f"«Arielni reklama qil».")
        return
    _clean()
    pid = str(int(time.time() * 1000))
    _PLANS[pid] = (plan, time.time())
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Boshla", callback_data=f"idea_go_{pid}"),
        InlineKeyboardButton(text="❌ Bekor", callback_data=f"idea_no_{pid}"),
    ]])
    tovarlar = "\n".join(f"   • {n}" for n in plan.get("names") or [])
    qosh = f"\n\n⚠️ {plan['warn']}" if plan.get("warn") else ""
    urgu = f"\n\n✍️ Urg'u: {plan['note']}" if plan.get("note") else ""
    await msg.reply(
        f"🎧 <b>Eshitdim:</b> «{text}»\n\n"
        f"<b>{TURI_NOMI.get(plan['type'], plan['type'])}</b>\n{tovarlar}{urgu}{qosh}\n\n"
        f"Boshlaymi?", parse_mode="HTML", reply_markup=kb)


def _is_admin_voice(msg: Message) -> bool:
    """Ovozli xabar — faqat egasining o'zidan va faqat shaxsiy chatda.

    Tekshiruv filtrda: aks holda mijozning ovozli xabari ham shu yerda
    yutilib, keyingi ishlovchilarga yetib bormasdi."""
    from bot.config import GLAVNIY_ADMIN_ID
    return bool(msg.voice and msg.chat.type == "private"
                and msg.from_user and msg.from_user.id == GLAVNIY_ADMIN_ID)


@router.message(_is_admin_voice)
async def idea_voice(msg: Message):
    wait = await msg.reply("🎧 Eshityapman…")
    text = await idea_reel.voice_to_text(msg.bot, msg.voice)
    if not text:
        await wait.edit_text("🎧 Ovozni matnga o'gira olmadim (AI kvotasi?). "
                             "Matn qilib yozib yuboring: /goya …")
        return
    try:
        await wait.delete()
    except Exception:
        pass
    await _show_plan(msg, text)


@router.message(Command("goya"))
async def idea_text(msg: Message):
    if not await _is_admin(msg.from_user.id):
        return
    text = (msg.text or "").partition(" ")[2].strip()
    if not text:
        await msg.reply("G'oyani yozing: <code>/goya Arielni reklama qil</code>\n"
                        "Yoki shunchaki ovozli xabar yuboring.", parse_mode="HTML")
        return
    await _show_plan(msg, text)


@router.callback_query(F.data.startswith("idea_"))
async def idea_decide(cb: CallbackQuery):
    if not await _is_admin(cb.from_user.id):
        await cb.answer("Faqat admin uchun", show_alert=True)
        return
    _, action, pid = cb.data.split("_", 2)
    rec = _PLANS.pop(pid, None)
    if action == "no":
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.message.reply("❌ Bekor qilindi")
        await cb.answer()
        return
    if not rec:
        await cb.answer("Reja eskirdi — g'oyani qayta ayting", show_alert=True)
        return
    plan = rec[0]
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("Boshladim")
    kutish = await cb.message.reply("⏳ Rolik tayyorlanmoqda… (2–5 daqiqa)")

    async def _work():
        try:
            res = await idea_reel.run(db, plan)
            await kutish.edit_text(("✅ " if res.get("ok") else "❌ ") + res.get("text", ""))
        except Exception as e:
            logger.exception("💡 G'oyadan rolik yasalmadi")
            try:
                await kutish.edit_text(f"❌ Xato: {str(e)[:200]}")
            except Exception:
                pass

    asyncio.create_task(_work())
