"""
Adminlarga ogohlantirish yuborish yordamchilari.
"""

from aiogram import Bot

from bot.config import GLAVNIY_ADMIN_ID
from database.channel_db import db


async def notify_admins(bot: Bot, text: str, parse_mode: str = "HTML"):
    """Bosh admin va barcha qo'shimcha adminlarga xabar yuboradi."""
    sent = set()
    try:
        await bot.send_message(GLAVNIY_ADMIN_ID, text, parse_mode=parse_mode)
        sent.add(GLAVNIY_ADMIN_ID)
    except Exception:
        pass
    for a in await db.get_all_admins():
        tg = a.get("telegram_id")
        if tg and tg not in sent:
            try:
                await bot.send_message(tg, text, parse_mode=parse_mode)
                sent.add(tg)
            except Exception:
                pass


def make_low_stock_handler(bot: Bot):
    """Tovar qoldig'i ostonadan past tushganida chaqiriladi."""
    async def handler(p: dict):
        unit = p.get("unit", "dona")
        qty = p.get("qty", 0)
        if qty <= 0:
            head = "❌ <b>TOVAR TUGADI</b>"
            tail = "Yangi partiya keltiring."
        else:
            head = "⚠️ <b>OZ QOLDI — OGOHLANTIRISH</b>"
            tail = "Tezroq to'ldirib qo'ying."
        usd = float(p.get("sell_price_usd", 0) or 0)
        summ = float(p.get("sell_price", 0) or 0)
        if usd > 0:
            price_line = f"💰 Sotish narxi: <b>${usd:,.2f}/{unit}</b> (≈ {summ:,.0f} so'm/{unit})"
        else:
            price_line = f"💰 Sotish narxi: {summ:,.0f} so'm/{unit}"
        text = (
            f"{head}\n\n"
            f"📦 <b>{p['name']}</b>\n"
            f"🔴 Qoldiq: <b>{qty:g} {unit}</b>\n"
            f"{price_line}\n"
            f"🆔 ID: <code>{p['id']}</code>\n\n"
            f"{tail}"
        )
        await notify_admins(bot, text)
    return handler
