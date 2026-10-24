"""Instagram izohi / lichkasiga admin javobi.

Oqim: bot adminga xabar yuboradi → admin O'SHA xabarga reply qilib javob
yozadi → javob Instagram'ga ketadi. «🤖 AI javob» tugmasi javobni AI ga
yozdiradi, lekin YUBORMAYDI — avval admin ko'radi va tasdiqlaydi.
"""

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot import ig_social
from database.channel_db import db

router = Router()


async def _is_admin(uid: int) -> bool:
    from bot.config import GLAVNIY_ADMIN_ID
    return uid == GLAVNIY_ADMIN_ID or await db.is_admin(uid)


@router.callback_query(F.data.startswith("igs_"))
async def igs_action(cb: CallbackQuery):
    if not await _is_admin(cb.from_user.id):
        await cb.answer("Faqat admin uchun", show_alert=True)
        return
    try:
        _, action, eid = cb.data.split("_", 2)
        eid = int(eid)
    except ValueError:
        await cb.answer()
        return
    ev = ig_social.get(eid)
    if not ev:
        await cb.answer("Yozuv topilmadi", show_alert=True)
        return

    if action == "ai":
        await cb.answer("AI javob yozmoqda…")
        import asyncio
        javob = await asyncio.get_event_loop().run_in_executor(
            None, lambda: ig_social.ai_reply(ev.get("text") or "", ev.get("kind") or ""))
        if not javob:
            await cb.message.reply("🤖 AI javob bera olmadi — qo'lda yozing "
                                   "(shu xabarga reply qiling).")
            return
        ig_social._set(eid, draft=javob)
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Yuborish", callback_data=f"igs_send_{eid}"),
            InlineKeyboardButton(text="❌ Bekor", callback_data=f"igs_drop_{eid}"),
        ]])
        await cb.message.reply(f"🤖 <b>AI javobi:</b>\n\n{javob}\n\n"
                               f"Yuboraymi? O'zgartirmoqchi bo'lsangiz — "
                               f"yuqoridagi xabarga reply qilib o'zingiz yozing.",
                               parse_mode="HTML", reply_markup=kb)
        return

    if action == "send":
        javob = (ev.get("draft") or "").strip()
        if not javob:
            await cb.answer("Javob matni yo'q", show_alert=True)
            return
        err = await ig_social.answer(db, eid, javob)
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.message.reply("❌ " + err[:200] if err else "✅ Instagram'ga yuborildi")
        await cb.answer()
        return

    if action == "drop":
        ig_social._set(eid, draft="")
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.message.reply("❌ Bekor qilindi")
        await cb.answer()
        return

    if action == "hide":
        token = dict(ig_social.accounts()).get(ev["ig_user"], "")
        if not token:
            await cb.answer("Token yo'q", show_alert=True)
            return
        import asyncio
        err = await asyncio.get_event_loop().run_in_executor(
            None, lambda: ig_social.hide_comment(ev["ext_id"], token))
        await cb.message.reply("❌ " + err[:200] if err else "🙈 Izoh yashirildi")
        await cb.answer()
        return

    await cb.answer()


def _is_ig_reply(msg: Message) -> bool:
    """Shu xabar bizning Instagram bildirishnomamizga javobmi?

    Tekshiruv FILTR ichida bo'lishi SHART: aiogram'da ishlovchi ishga
    tushsa, xabar keyingi routerlarga O'TMAYDI. Ichkarida `return` qilsak,
    adminning har qanday reply xabari (masalan mijoz chatiga javobi) shu
    yerda yutilib ketardi."""
    r = msg.reply_to_message
    return bool(r and msg.text and ig_social.by_tg_msg(r.message_id))


@router.message(_is_ig_reply)
async def igs_reply(msg: Message):
    """Admin bot xabariga reply qilsa — o'sha izoh/lichkaga javob ketadi."""
    if not await _is_admin(msg.from_user.id):
        return
    ev = ig_social.by_tg_msg(msg.reply_to_message.message_id)
    err = await ig_social.answer(db, ev["id"], msg.text)
    await msg.reply("❌ " + err[:200] if err else "✅ Instagram'ga yuborildi")
