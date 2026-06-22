import os
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message, FSInputFile

from bot.config import GLAVNIY_ADMIN_ID
from bot.keyboards import admin_menu, orders_list_kb, stats_kb, export_kb, low_stock_kb
from bot.exporter import (
    export_sales, export_clients, export_debtors, export_products
)
from bot.backup import send_backup
from bot.permissions import (
    has_permission, get_user_menu, is_admin_or_glavniy, deny, require,
)
from database.channel_db import db, now_local, fmt_usd, fmt_sum

router = Router()


async def _is_admin(uid: int) -> bool:
    # Markaziy helperga ko'prik
    return await is_admin_or_glavniy(db, uid)


async def _check_perm(target, perm: str) -> bool:
    # Markaziy `require` helperiga ko'prik (eski chaqiriqlar buzilmasin)
    return await require(target, db, perm)


@router.message(F.text == "📊 Statistika")
async def stats_menu(message: Message):
    if not await _is_admin(message.from_user.id):
        return
    if not await _check_perm(message, "stats"):
        return
    await message.answer(
        "📊 <b>Statistika</b>\n\nQaysi davrni ko'rmoqchisiz?",
        reply_markup=stats_kb(), parse_mode="HTML"
    )


def _money_pair(sum_val: float, usd_val: float) -> str:
    """'$12.50  (≈ 156,250 so'm)' yoki USD bo'lmasa faqat so'm."""
    sum_val = float(sum_val or 0)
    usd_val = float(usd_val or 0)
    if usd_val > 0:
        return f"<b>{fmt_usd(usd_val)}</b>  (≈ {fmt_sum(sum_val)})"
    return f"<b>{fmt_sum(sum_val)}</b>"


def _stats_text(header: str, s: dict, prods: list, clients: list = None) -> str:
    pay_lines = ""
    if s.get("cash_total", 0) > 0 or s.get("cash_total_usd", 0) > 0:
        pay_lines += f"  💵 Naqd: {_money_pair(s.get('cash_total',0), s.get('cash_total_usd',0))}\n"
    if s.get("card_total", 0) > 0 or s.get("card_total_usd", 0) > 0:
        pay_lines += f"  💳 Karta: {_money_pair(s.get('card_total',0), s.get('card_total_usd',0))}\n"
    if s.get("nasiya_total", 0) > 0 or s.get("nasiya_total_usd", 0) > 0:
        pay_lines += f"  🤝 Nasiya: {_money_pair(s.get('nasiya_total',0), s.get('nasiya_total_usd',0))}\n"
    discount_line = ""
    if s.get("discount_total", 0) > 0 or s.get("discount_total_usd", 0) > 0:
        discount_line = (f"💸 Berilgan chegirma: "
                         f"{_money_pair(s.get('discount_total',0), s.get('discount_total_usd',0))}\n")
    text = (
        f"{header}\n\n"
        f"🧾 Kassa sotuvi: <b>{s.get('sale_count', 0)} ta</b>\n"
        f"🚚 Buyurtmalar: <b>{s.get('order_count', 0)} ta</b>\n"
        f"─────────────────────\n"
        f"💰 Umumiy tushum: {_money_pair(s.get('revenue',0), s.get('revenue_usd',0))}\n"
        f"{pay_lines}"
        f"{discount_line}"
        f"─────────────────────\n"
        f"📦 Xarajat (tannarx): {_money_pair(s.get('cost',0), s.get('cost_usd',0))}\n"
        f"✅ Sof foyda: {_money_pair(s.get('profit',0), s.get('profit_usd',0))}\n"
    )
    if prods:
        text += "\n📦 <b>Top mahsulotlar:</b>\n"
        for p in prods[:5]:
            usd = float(p.get("revenue_usd", 0) or 0)
            sum_ = float(p.get("revenue", 0) or 0)
            if usd > 0:
                line = f"{fmt_usd(usd)} (≈ {fmt_sum(sum_)})"
            else:
                line = fmt_sum(sum_)
            text += f"  • {p['name']}: {p['qty']:g} dona — {line}\n"
    if clients:
        text += "\n🏆 <b>Top mijozlar:</b>\n"
        for c in clients[:3]:
            text += (f"  • {c['shop_name']}: {c['total_spent']:,.0f} so'm "
                     f"({c['order_count']} buyurtma)\n")
    return text


@router.callback_query(F.data == "stats_today")
async def stats_today(cb: CallbackQuery):
    if not await _is_admin(cb.from_user.id):
        return
    if not await _check_perm(cb, "stats"):
        return
    today = now_local().strftime("%Y-%m-%d")
    s = await db.stats_day(today)
    prods = await db.top_products(today)
    rate = db.get_usd_rate()
    header = f"📅 <b>Bugun ({today}):</b>\n💱 Kurs: 1$ = {rate:,.0f} so'm"
    await cb.message.answer(_stats_text(header, s, prods), parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "stats_month")
async def stats_month_handler(cb: CallbackQuery):
    if not await _is_admin(cb.from_user.id):
        return
    if not await _check_perm(cb, "stats"):
        return
    month = now_local().strftime("%Y-%m")
    s = await db.stats_month(month)
    prods = await db.top_products(month)
    clients = await db.top_clients(5)
    rate = db.get_usd_rate()
    header = (f"📆 <b>{now_local().strftime('%B %Y')}:</b>\n"
              f"💱 Kurs: 1$ = {rate:,.0f} so'm")
    await cb.message.answer(_stats_text(header, s, prods, clients), parse_mode="HTML")
    await cb.answer()


@router.message(F.text == "📋 Buyurtmalar")
async def show_orders(message: Message):
    if not await _is_admin(message.from_user.id):
        return
    if not await _check_perm(message, "orders"):
        return
    all_orders = await db.get_recent_orders(20)
    if not all_orders:
        await message.answer("📋 Buyurtmalar yo'q.")
        return
    await message.answer(
        f"📋 <b>So'nggi buyurtmalar ({len(all_orders)} ta):</b>",
        reply_markup=orders_list_kb(all_orders), parse_mode="HTML"
    )


# ─── Excel eksport ─────────────────────────────────────────────────────────────

@router.message(F.text == "📥 Eksport")
async def export_menu(message: Message):
    if not await _is_admin(message.from_user.id):
        return
    if not await _check_perm(message, "export"):
        return
    await message.answer(
        "📥 <b>Excel hisobotlar</b>\n\nQaysi hisobotni yuklab olasiz?",
        reply_markup=export_kb(), parse_mode="HTML"
    )


async def _send_xlsx(bot: Bot, chat_id: int, path: str, caption: str):
    if not path or not os.path.exists(path):
        await bot.send_message(chat_id, "❌ Fayl yaratilmadi.")
        return
    await bot.send_document(
        chat_id, FSInputFile(path), caption=caption, parse_mode="HTML"
    )


@router.callback_query(F.data == "export_sales_month")
async def export_sales_month(cb: CallbackQuery, bot: Bot):
    if not await _is_admin(cb.from_user.id):
        return
    if not await _check_perm(cb, "export"):
        return
    await cb.answer("⏳ Tayyorlanmoqda...")
    month = now_local().strftime("%Y-%m")
    path = await export_sales(month)
    await _send_xlsx(bot, cb.from_user.id, path,
                     f"📊 <b>Sotuvlar — {month}</b>")


@router.callback_query(F.data == "export_sales_all")
async def export_sales_all(cb: CallbackQuery, bot: Bot):
    if not await _is_admin(cb.from_user.id):
        return
    if not await _check_perm(cb, "export"):
        return
    await cb.answer("⏳ Tayyorlanmoqda...")
    path = await export_sales(None)
    await _send_xlsx(bot, cb.from_user.id, path, "📊 <b>Barcha sotuvlar</b>")


@router.callback_query(F.data == "export_clients")
async def export_clients_h(cb: CallbackQuery, bot: Bot):
    if not await _is_admin(cb.from_user.id):
        return
    if not await _check_perm(cb, "export"):
        return
    await cb.answer("⏳ Tayyorlanmoqda...")
    path = await export_clients()
    await _send_xlsx(bot, cb.from_user.id, path, "👤 <b>Mijozlar ro'yxati</b>")


@router.callback_query(F.data == "export_debtors")
async def export_debtors_h(cb: CallbackQuery, bot: Bot):
    if not await _is_admin(cb.from_user.id):
        return
    if not await _check_perm(cb, "export"):
        return
    await cb.answer("⏳ Tayyorlanmoqda...")
    path = await export_debtors()
    await _send_xlsx(bot, cb.from_user.id, path, "💳 <b>Qarzdorlar ro'yxati</b>")


@router.callback_query(F.data == "export_products")
async def export_products_h(cb: CallbackQuery, bot: Bot):
    if not await _is_admin(cb.from_user.id):
        return
    if not await _check_perm(cb, "export"):
        return
    await cb.answer("⏳ Tayyorlanmoqda...")
    path = await export_products()
    await _send_xlsx(bot, cb.from_user.id, path, "📦 <b>Ombor holati</b>")


@router.callback_query(F.data == "export_backup")
async def export_backup_h(cb: CallbackQuery, bot: Bot):
    # Faqat bosh admin
    if cb.from_user.id != GLAVNIY_ADMIN_ID:
        await cb.answer("⛔ Faqat bosh admin", show_alert=True)
        return
    await cb.answer("⏳ Backup tayyorlanmoqda...")
    ok = await send_backup(bot)
    if not ok:
        await bot.send_message(cb.from_user.id, "❌ Backup yaratilmadi.")


# ─── Past qoldiq ro'yxati ─────────────────────────────────────────────────────

@router.message(F.text == "⚠️ Past qoldiq")
async def low_stock_list(message: Message):
    if not await _is_admin(message.from_user.id):
        return
    if not await _check_perm(message, "low_stock"):
        return
    items = await db.get_low_stock()
    if not items:
        await message.answer("✅ Hech qanday tovar past qoldiqda emas.")
        return
    text = f"⚠️ <b>Past qoldiqdagi tovarlar ({len(items)} ta):</b>\n\n"
    for p in items[:50]:
        unit = p.get("unit", "dona")
        text += f"🔴 <b>{p['name']}</b> — {p.get('qty', 0):g} {unit} (ID: {p['id']})\n"
    if len(items) > 50:
        text += f"\n…va yana {len(items) - 50} ta"
    # Tezkor to'ldirish funksiyasi yoqilgan bo'lsa va prixod ruxsati bor bo'lsa —
    # har bir tovarga «⚡» tugmasi qo'shamiz (qrs_* handler → catalog.py).
    kb = None
    if db.is_quick_restock_enabled() and \
       await has_permission(db, message.from_user.id, "products_qty"):
        kb = low_stock_kb(items)
        text += "\n\n⚡ <i>Tovarni tezkor to'ldirish uchun tugmasini bosing.</i>"
    await message.answer(text, reply_markup=kb, parse_mode="HTML")
