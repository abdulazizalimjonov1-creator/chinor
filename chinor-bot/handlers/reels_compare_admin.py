"""Kunlik taqqoslash videosi — admin tasdig'i (✅ Kanalga / ❌ Bekor)."""

from aiogram import Router, F
from aiogram.types import CallbackQuery

from bot import reels_compare
from database.channel_db import db

router = Router()


async def _is_admin(uid: int) -> bool:
    from bot.config import GLAVNIY_ADMIN_ID
    return uid == GLAVNIY_ADMIN_ID or await db.is_admin(uid)


@router.callback_query(F.data.startswith("rcmp_"))
async def rcmp_decide(cb: CallbackQuery):
    if not await _is_admin(cb.from_user.id):
        await cb.answer("Faqat admin uchun", show_alert=True)
        return
    _, action, rid = cb.data.split("_", 2)
    rid = int(rid)
    if action == "ok":
        err = await reels_compare.publish(db, rid)
        if err:
            await cb.answer(err[:190], show_alert=True)
            return
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.message.reply("✅ Kanalga joylandi")
        await cb.answer("Joylandi")
    else:
        reels_compare._set(rid, status="rejected")
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.message.reply("❌ Bekor qilindi — kanalga chiqmaydi")
        await cb.answer()
