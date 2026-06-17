from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
import logging

from bot.config import GLAVNIY_ADMIN_ID
from bot.keyboards import (
    client_menu, client_products_kb, cancel_kb, order_client_confirm_kb,
    _price_for, _price_pair_for,
    client_product_card_kb, client_search_results_kb,
    client_consult_followup_kb, RESERVED_MENU,
)
from bot.states import OrderStates, ClientSearchStates, ClientConsultStates
from bot.barcode import decode_barcode
from bot.gemini_analyzer import (
    find_similar_products, expand_query_keywords, generate_product_pitch,
    consult_client, extract_product_ids_from_text,
    is_available as ai_is_available,
)
from database.channel_db import db, now_local, fmt_usd, fmt_sum, usd_to_sum

logger = logging.getLogger(__name__)

def _ctype(c: dict) -> str:
    return (c.get("client_type") or "dona").lower()

router = Router()


def _debt_block(c: dict) -> str:
    debt_usd = float(c.get("debt_usd", 0) or 0)
    debt_sum = float(c.get("debt", 0) or 0)
    if debt_usd > 0:
        return f"💰 Qarz: <b>{fmt_usd(debt_usd)}</b>  (≈ {fmt_sum(debt_sum)})"
    if debt_sum > 0:
        return f"💰 Qarz: <b>{fmt_sum(debt_sum)}</b>"
    return "✅ Qarzsiz"


@router.message(F.text == "ℹ️ Ma'lumotlarim")
async def my_info(message: Message):
    c = await db.get_client_by_tg(message.from_user.id)
    if not c: return
    ctype = _ctype(c)
    type_label = "🛍️ Donachi (chakana)" if ctype == "dona" else "📦 Optomchi (ulgurji)"
    await message.answer(
        f"ℹ️ <b>Ma'lumotlaringiz:</b>\n\n"
        f"👤 {c['shop_name']}\n📱 {c['phone']}\n"
        f"🆔 <code>{c['telegram_id']}</code>\n"
        f"🏷️ Turingiz: <b>{type_label}</b>\n"
        f"{_debt_block(c)}",
        parse_mode="HTML"
    )


@router.message(F.text == "💳 Qarzim")
async def my_debt(message: Message):
    c = await db.get_client_by_tg(message.from_user.id)
    if not c: return
    debt_usd = float(c.get("debt_usd", 0) or 0)
    debt_sum = float(c.get("debt", 0) or 0)
    if debt_usd <= 0 and debt_sum <= 0:
        await message.answer("✅ Sizda qarz yo'q!")
    else:
        await message.answer(
            f"{_debt_block(c)}\n\nAdmin bilan bog'laning.",
            parse_mode="HTML"
        )


@router.message(F.text == "📊 Hisobotim")
async def my_report(message: Message):
    c = await db.get_client_by_tg(message.from_user.id)
    if not c: return
    month = now_local().strftime("%Y-%m")
    rep = await db.client_monthly_report(c["id"], month)
    orders = await db.get_client_orders(c["id"], month)
    await message.answer(
        f"📊 <b>Bu oy hisoboti:</b>\n\n"
        f"🛒 Zakaz: <b>{rep['total_ordered']:,.0f} so'm</b>\n"
        f"💳 To'langan: <b>{rep['total_paid']:,.0f} so'm</b>\n"
        f"💰 Qolgan qarz: <b>{rep['current_debt']:,.0f} so'm</b>\n\n"
        f"📋 {len(orders)} ta zakaz",
        parse_mode="HTML"
    )


@router.message(F.text == "🛒 Buyurtma berish")
async def start_order(message: Message, state: FSMContext):
    # 'Mijoz buyurtmalari' funksiyasi o'chirilgan bo'lsa — ishlamaydi
    if not db.is_client_orders_enabled():
        await message.answer(
            "ℹ️ Bot orqali buyurtma berish hozircha o'chirilgan.\n"
            "Iltimos, do'kon bilan to'g'ridan-to'g'ri bog'laning."
        )
        return
    c = await db.get_client_by_tg(message.from_user.id)
    if not c:
        await message.answer("⚠️ Siz ro'yxatdan o'tmagansiz!")
        return
    # Sotuv reytingi bo'yicha tartiblangan, faqat qoldig'i bor mahsulotlar.
    # 1-sahifa = 6 ta eng ko'p sotiladigan. ◀️▶️ bilan varaqlanadi.
    avail = await db.top_selling_products()
    if not avail:
        await message.answer("⚠️ Hozirda mahsulotlar yo'q.")
        return
    ctype = _ctype(c)
    await state.update_data(cart={}, client_id=c["id"], client_type=ctype, order_page=0)
    await state.set_state(OrderStates.browsing)
    type_label = "🛍️ Donachi (chakana)" if ctype == "dona" else "📦 Optomchi (ulgurji)"
    await message.answer(
        f"🛒 <b>Mahsulotni tanlang:</b>\n"
        f"🏷️ Sizga ko'rsatilayotgan narxlar: <b>{type_label}</b>\n"
        f"<i>Eng ko'p sotiladiganlar yuqorida — ◀️▶️ bilan varaqlang.</i>",
        reply_markup=client_products_kb(avail, {}, ctype, page=0), parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("order_add_"), OrderStates.browsing)
async def order_add(cb: CallbackQuery, state: FSMContext):
    pid = int(cb.data.split("_")[2])
    p = await db.get_product(pid)
    if not p:
        await cb.answer("Topilmadi!")
        return
    if p.get("qty", 0) <= 0:
        await cb.answer("⚠️ Qolmagan!")
        return
    data = await state.get_data()
    ctype = (data.get("client_type") or "dona").lower()
    usd, summ = _price_pair_for(p, ctype)
    unit = p.get("unit", "dona")
    if usd > 0:
        price_line = f"💰 <b>${usd:,.2f}/{unit}</b>  (≈ {summ:,.0f} so'm/{unit})"
    else:
        price_line = f"💰 {summ:,.0f} so'm/{unit}"
    await state.update_data(order_pid=pid)
    await state.set_state(OrderStates.qty)
    text = (
        f"📦 <b>{p['name']}</b>\n"
        f"{price_line}\n"
        f"📦 Mavjud: {p.get('qty', 0):g} {unit}\n\nNechta?"
    )
    if p.get("image_file_id"):
        try:
            await cb.message.answer_photo(p["image_file_id"], caption=text,
                                           reply_markup=cancel_kb(), parse_mode="HTML")
            await cb.answer()
            return
        except Exception:
            pass
    await cb.message.answer(text, reply_markup=cancel_kb(), parse_mode="HTML")
    await cb.answer()


@router.message(OrderStates.qty)
async def order_qty(message: Message, state: FSMContext):
    data = await state.get_data()
    ctype = (data.get("client_type") or "dona").lower()
    if message.text == "❌ Bekor qilish":
        await state.set_state(OrderStates.browsing)
        avail = await db.top_selling_products()
        page = data.get("order_page", 0)
        await message.answer(
            "Tanlang:",
            reply_markup=client_products_kb(avail, data.get("cart", {}), ctype, page=page)
        )
        return
    try:
        qty_raw = message.text.strip().replace(",", ".")
        qty = float(qty_raw)
        if qty <= 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Musbat son kiriting:")
        return
    pid = data["order_pid"]
    p = await db.get_product(pid)
    if not p:
        await state.set_state(OrderStates.browsing)
        return
    if qty > p.get("qty", 0):
        await message.answer(f"⚠️ Faqat {p['qty']:g} {p.get('unit','dona')} mavjud!")
        return
    usd, summ = _price_pair_for(p, ctype)
    # Mijoz buyurtmasi — qarz so'mda yuritiladi, shuning uchun price = so'm
    price = summ if summ > 0 else usd_to_sum(usd, db.get_usd_rate())
    unit = p.get("unit", "dona")
    cart = data.get("cart", {})
    key = str(pid)
    if key in cart:
        cart[key]["qty"] += qty
    else:
        cart[key] = {
            "product_id": pid, "name": p["name"],
            "qty": qty, "price": price, "price_usd": usd, "unit": unit
        }
    await state.update_data(cart=cart)
    await state.set_state(OrderStates.browsing)
    avail = await db.top_selling_products()
    page = data.get("order_page", 0)
    total_sum = sum(v["qty"] * v["price"] for v in cart.values())
    total_usd = sum(v["qty"] * v.get("price_usd", 0) for v in cart.values())
    if total_usd > 0:
        lines = "".join(
            f"• {v['name']}: {v['qty']:g} {v.get('unit','dona')} "
            f"= ${v['qty']*v.get('price_usd',0):,.2f}  ({v['qty']*v['price']:,.0f} so'm)\n"
            for v in cart.values()
        )
        total_line = f"💰 <b>Jami: ${total_usd:,.2f}</b>  (≈ {total_sum:,.0f} so'm)"
    else:
        lines = "".join(
            f"• {v['name']}: {v['qty']:g} {v.get('unit','dona')} = {v['qty']*v['price']:,.0f} so'm\n"
            for v in cart.values()
        )
        total_line = f"💰 <b>Jami: {total_sum:,.0f} so'm</b>"
    type_label = "🛍️ Donachi" if ctype == "dona" else "📦 Optomchi"
    await message.answer(
        f"🛒 <b>Savat ({type_label}):</b>\n{lines}\n{total_line}",
        reply_markup=client_products_kb(avail, cart, ctype, page=page), parse_mode="HTML"
    )


@router.callback_query(F.data == "order_clear")
async def order_clear(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    ctype = (data.get("client_type") or "dona").lower()
    await state.update_data(cart={})
    await cb.answer("🗑️ Tozalandi!")
    avail = await db.top_selling_products()
    page = data.get("order_page", 0)
    await cb.message.edit_reply_markup(
        reply_markup=client_products_kb(avail, {}, ctype, page=page)
    )


@router.callback_query(F.data.startswith("order_page_"), OrderStates.browsing)
async def order_page_nav(cb: CallbackQuery, state: FSMContext):
    """Mijoz buyurtma oynasida ◀️ / ▶️ sahifa navigatsiyasi."""
    arg = cb.data[len("order_page_"):]
    if arg == "noop":
        await cb.answer()
        return
    try:
        page = int(arg)
    except ValueError:
        await cb.answer()
        return
    await state.update_data(order_page=page)
    data = await state.get_data()
    ctype = (data.get("client_type") or "dona").lower()
    cart = data.get("cart", {})
    avail = await db.top_selling_products()
    try:
        await cb.message.edit_reply_markup(
            reply_markup=client_products_kb(avail, cart, ctype, page=page)
        )
    except Exception:
        pass
    await cb.answer()


@router.callback_query(F.data == "order_confirm")
async def order_confirm(cb: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    cart = data.get("cart", {})
    if not cart:
        await cb.answer("⚠️ Savat bo'sh!")
        return
    items = [{"product_id": v["product_id"], "qty": v["qty"], "price": v["price"]} for v in cart.values()]
    oid, total = await db.create_order(data["client_id"], items)
    await state.clear()
    lines = "".join(
        f"• {v['name']}: {v['qty']:g} {v.get('unit','dona')}\n"
        for v in cart.values()
    )
    await cb.message.answer(
        f"✅ <b>Buyurtma #{oid} qabul qilindi!</b>\n\n{lines}\n💰 <b>Jami: {total:,.0f} so'm</b>",
        reply_markup=client_menu(), parse_mode="HTML"
    )
    # Adminlarga xabarnoma
    c = await db.get_client_by_id(data["client_id"])
    notif = (
        f"🔔 <b>Yangi buyurtma #{oid}!</b>\n\n"
        f"👤 {c['shop_name']}  📱 {c['phone']}\n\n{lines}\n"
        f"💰 <b>Jami: {total:,.0f} so'm</b>"
    )
    from bot.keyboards import order_status_kb
    kb = order_status_kb(oid, "accepted")
    try: await bot.send_message(GLAVNIY_ADMIN_ID, notif, reply_markup=kb, parse_mode="HTML")
    except Exception: pass
    for a in await db.get_all_admins():
        try: await bot.send_message(a["telegram_id"], notif, reply_markup=kb, parse_mode="HTML")
        except Exception: pass
    await cb.answer()


@router.callback_query(F.data.startswith("cconfirm_"))
async def client_confirm(cb: CallbackQuery, bot: Bot):
    oid = int(cb.data.split("_")[1])
    await db.update_order_status(oid, "confirmed")
    await cb.message.edit_text(f"✅ Tasdiqlandi! Rahmat 🙏\nBuyurtma #{oid}")
    await cb.answer("✅")
    o = await db.get_order(oid)
    c = await db.get_client_by_tg(cb.from_user.id)
    msg = f"✅ Buyurtma #{oid} klient tasdiqladi!\n👤 {c['shop_name'] if c else '?'}\n💰 {o.get('total', 0):,.0f} so'm" if o else f"✅ Buyurtma #{oid} tasdiqlandi"
    try: await bot.send_message(GLAVNIY_ADMIN_ID, msg, parse_mode="HTML")
    except Exception: pass


@router.callback_query(F.data.startswith("cnotyet_"))
async def client_not_yet(cb: CallbackQuery, bot: Bot):
    oid = int(cb.data.split("_")[1])
    await cb.message.edit_text(f"⚠️ Buyurtma #{oid} — hali kelmadi deb belgilandi.\nAdmin xabardor qilindi.")
    await cb.answer()
    c = await db.get_client_by_tg(cb.from_user.id)
    msg = f"⚠️ Buyurtma #{oid} — {c['shop_name'] if c else '?'} HALI YETMAGAN dedi!"
    # (eslatma: shop_name ichki ustun nomi — endi mijoz ismini saqlaydi)
    try: await bot.send_message(GLAVNIY_ADMIN_ID, msg)
    except Exception: pass


# ═══════════════════════════════════════════════════════════════════════════
#  🔎 Mijoz uchun mahsulot qidirish (hibrid: DB + Gemini AI fallback)
# ═══════════════════════════════════════════════════════════════════════════

CLIENT_CANCEL = "❌ Bekor qilish"


def _format_product_card(p: dict, ctype: str) -> str:
    """Mijozga ko'rsatiladigan mahsulot kartasi matni."""
    unit = p.get("unit", "dona")
    usd, summ = _price_pair_for(p, ctype)
    if usd > 0:
        price_line = f"💰 Narxi: <b>${usd:,.2f}/{unit}</b>  (≈ {summ:,.0f} so'm/{unit})"
    else:
        price_line = f"💰 Narxi: <b>{summ:,.0f} so'm/{unit}</b>"
    qty = float(p.get("qty", 0) or 0)
    if qty > 0:
        qty_line = f"✅ Mavjud: <b>{qty:g} {unit}</b>"
    else:
        qty_line = "🔴 <b>Hozircha tugagan</b>"
    desc = (p.get("description") or "").strip()
    desc_line = f"\n📝 {desc}" if desc else ""
    return (
        f"📦 <b>{p['name']}</b>{desc_line}\n\n"
        f"{price_line}\n{qty_line}"
    )


async def _send_product_card(message: Message, p: dict, ctype: str,
                              ai_hint: bool = False):
    """Mahsulot kartasini chiroyli ko'rinishda yuboradi (rasm bo'lsa rasm bilan)."""
    qty = float(p.get("qty", 0) or 0)
    can_order = qty > 0 and db.is_client_orders_enabled()
    text = _format_product_card(p, ctype)
    if ai_hint:
        text = "🤖 <i>AI sizga shularni topdi:</i>\n\n" + text
    # AI tugmasi — faqat Gemini sozlangan bo'lsa
    ai_on, _ = ai_is_available()
    kb = client_product_card_kb(p["id"], can_order, ai_on=ai_on)
    if p.get("image_file_id"):
        try:
            await message.answer_photo(p["image_file_id"], caption=text,
                                        reply_markup=kb, parse_mode="HTML")
            return
        except Exception:
            pass
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


async def _ask_search_query(message: Message):
    bc_hint = ""
    if db.is_barcode_enabled():
        bc_hint = ("\n• 📷 Mahsulot <b>shtrix-kod rasmini</b> yuborsangiz ham bo'ladi")
    await message.answer(
        "🔎 <b>Mahsulot qidirish</b>\n\n"
        "Tovar nomini yoki shtrix-kodini yozing:\n"
        "• 📝 Mahsulot nomi (masalan: <code>kola</code>, <code>non</code>)\n"
        "• 🔢 Shtrix-kod raqami"
        f"{bc_hint}\n\n"
        "<i>Topa olmasak — AI yaqin variantlarni topib beradi.</i>",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )


@router.message(F.text == "🔎 Mahsulot qidirish")
async def client_search_start(message: Message, state: FSMContext):
    if not db.is_client_search_enabled():
        await message.answer(
            "ℹ️ Mahsulot qidirish hozircha o'chirilgan.",
            reply_markup=client_menu()
        )
        return
    c = await db.get_client_by_tg(message.from_user.id)
    if not c:
        return  # ro'yxatdan o'tmagan — /start oqimi avtorizatsiya so'raydi
    await state.set_state(ClientSearchStates.searching)
    await state.update_data(client_id=c["id"])
    await _ask_search_query(message)


@router.callback_query(F.data == "cs_again")
async def client_search_again(cb: CallbackQuery, state: FSMContext):
    if not db.is_client_search_enabled():
        await cb.answer("Qidiruv o'chirilgan.", show_alert=True)
        return
    await state.set_state(ClientSearchStates.searching)
    await _ask_search_query(cb.message)
    await cb.answer()


async def _client_run_search(message: Message, state: FSMContext, query: str):
    """3 bosqichli hibrid qidiruv:
    1) DB da to'g'ridan-to'g'ri so'rov bo'yicha (eng tez)
    2) Topilmasa — AI dan kalit so'zlarni so'rab, har birini DB da qidiramiz
       (Misol: «kompyuter» → «noutbuk, laptop, macbook, pc, monoblok»)
    3) U ham ish bermasa — semantik moslashtirgich (qisqartirilgan katalog)
    """
    c = await db.get_client_by_tg(message.from_user.id)
    if not c:
        return
    ctype = _ctype(c)

    # 1) DB ning standart qidiruvi
    results = await db.search_products(query, limit=10)
    await db.log_search(message.from_user.id, query, len(results),
                        source="client_search")

    ai_hint = False        # AI yordami ishlatildimi?
    matched_kw = []        # AI taklif qilgan kalit so'zlardan qaysilari topilgan

    # 2) Topilmagan bo'lsa va so'rov 'normal so'z' bo'lsa — AI kengaytirish
    use_ai = (not results
              and len(query) >= 2
              and not query.isdigit())   # raqam bo'lsa AI kerakmas
    if use_ai:
        keywords = await expand_query_keywords(query)
        if keywords:
            seen_ids = set()
            combined = []
            for kw in keywords:
                if len(combined) >= 10:
                    break
                kw_res = await db.search_products(kw, limit=5)
                if kw_res:
                    matched_kw.append(kw)
                for p in kw_res:
                    if p["id"] not in seen_ids:
                        seen_ids.add(p["id"])
                        combined.append(p)
                if len(combined) >= 10:
                    break
            if combined:
                results = combined[:10]
                ai_hint = True

    # 3) Hali ham bo'sh — semantik moslashtirgich (oxirgi chora)
    if not results and use_ai:
        all_prods = await db.get_all_products(active_only=True)
        ids = await find_similar_products(query, all_prods, max_results=5)
        if ids:
            id_to_prod = {p["id"]: p for p in all_prods}
            results = [id_to_prod[i] for i in ids if i in id_to_prod]
            ai_hint = True

    if not results:
        await state.set_state(ClientSearchStates.searching)
        await message.answer(
            f"❌ <b>«{query}»</b> bo'yicha mahsulot topilmadi.\n\n"
            f"Iltimos, admin bilan bog'laning yoki boshqa nom bilan qidiring.",
            reply_markup=cancel_kb(), parse_mode="HTML"
        )
        return

    # Natijalarni ko'rsatish
    await state.set_state(ClientSearchStates.viewing)
    intro = ""
    if ai_hint:
        if matched_kw:
            kw_show = ", ".join(matched_kw[:5])
            intro = (
                f"🤖 <i>«{query}» degan to'g'ri mos topilmadi, lekin sizga "
                f"yaqin variantlar:</i>  <code>{kw_show}</code>\n\n"
            )
        else:
            intro = "🤖 <i>AI sizga yaqin variantlarni topdi:</i>\n\n"

    if len(results) == 1:
        if intro:
            await message.answer(intro, parse_mode="HTML")
        await _send_product_card(message, results[0], ctype, ai_hint=False)
        return
    await message.answer(
        f"{intro}🔎 <b>«{query}»</b> bo'yicha <b>{len(results)}</b> ta natija. "
        "Birini tanlang:",
        reply_markup=client_search_results_kb(results), parse_mode="HTML"
    )


@router.message(ClientSearchStates.searching, F.photo)
async def client_search_photo(message: Message, state: FSMContext, bot: Bot):
    """Mijoz shtrix-kod rasmini yuborsa — kodni o'qib qidiradi."""
    if not db.is_barcode_enabled():
        await message.answer("⚠️ Iltimos, mahsulot nomini matn qilib yozing.")
        return
    try:
        f = await bot.get_file(message.photo[-1].file_id)
        buf = await bot.download_file(f.file_path)
        image_bytes = buf.read() if hasattr(buf, "read") else bytes(buf)
        scanned = decode_barcode(image_bytes) or ""
    except Exception:
        scanned = ""
    if not scanned:
        await message.answer(
            "⚠️ Shtrix-kodni o'qib bo'lmadi. Yaqinroqdan yana suratga oling "
            "yoki nomini yozib yuboring."
        )
        return
    await message.answer(f"📷 Shtrix-kod: <code>{scanned}</code>", parse_mode="HTML")
    await _client_run_search(message, state, scanned)


@router.message(ClientSearchStates.searching, F.text & ~F.text.in_(RESERVED_MENU))
async def client_search_text(message: Message, state: FSMContext):
    query = (message.text or "").strip()
    if not query:
        return
    if len(query) < 2:
        await message.answer("⚠️ Kamida 2 ta belgi yozing.")
        return
    await _client_run_search(message, state, query)


# Viewing holatida ham yozsa darrov yangi qidiruvga o'tkazamiz
@router.message(ClientSearchStates.viewing, F.photo)
async def client_search_photo_viewing(message: Message, state: FSMContext, bot: Bot):
    await state.set_state(ClientSearchStates.searching)
    await client_search_photo(message, state, bot)


@router.message(ClientSearchStates.viewing, F.text & ~F.text.in_(RESERVED_MENU))
async def client_search_text_viewing(message: Message, state: FSMContext):
    await state.set_state(ClientSearchStates.searching)
    await client_search_text(message, state)


@router.callback_query(F.data.startswith("cs_view_"))
async def client_search_view(cb: CallbackQuery, state: FSMContext):
    """Qidiruv natijalari ro'yxatidan mahsulot tanlash."""
    pid = int(cb.data.rsplit("_", 1)[1])
    p = await db.get_product(pid)
    if not p:
        await cb.answer("Topilmadi.", show_alert=True)
        return
    c = await db.get_client_by_tg(cb.from_user.id)
    if not c:
        await cb.answer()
        return
    ctype = _ctype(c)
    await state.set_state(ClientSearchStates.viewing)
    await _send_product_card(cb.message, p, ctype)
    await cb.answer()


@router.callback_query(F.data.startswith("cs_pitch_"))
async def client_search_ai_pitch(cb: CallbackQuery):
    """✨ AI dan tovar haqida tushuntirish so'rash."""
    pid = int(cb.data.rsplit("_", 1)[1])
    p = await db.get_product(pid)
    if not p:
        await cb.answer("Topilmadi.", show_alert=True)
        return
    ok, why = ai_is_available()
    if not ok:
        await cb.answer(why.replace("<b>", "").replace("</b>", "")[:200],
                         show_alert=True)
        return
    await cb.answer("⏳ AI o'ylab javob yozmoqda...")
    pitch = await generate_product_pitch(p)
    if not pitch:
        await cb.message.answer("⚠️ AI hech narsa qaytarmadi. Keyinroq urinib ko'ring.")
        return
    name = p.get("name", "")
    text = f"✨ <b>{name}</b> haqida:\n\n{pitch}"
    try:
        await cb.message.answer(text, parse_mode="HTML")
    except Exception:
        await cb.message.answer(pitch[:4000])


# ═══════════════════════════════════════════════════════════════════════════
#  💬 AI sotuvchi-konsultant — mijoz murakkab savol berishi mumkin
# ═══════════════════════════════════════════════════════════════════════════

CONSULT_INTRO = (
    "💬 <b>AI sotuvchi-konsultant</b>\n\n"
    "Menga to'liq savol bering — men <b>tovar tanlashga yordamlashaman</b>:\n\n"
    "🧮 <i>«15 m² uy uchun qancha bo'yoq kerak va qaysinisini olay?»</i>\n"
    "🛠 <i>«Devorni bo'yashga zarur asboblar nimalar?»</i>\n"
    "💡 <i>«Bolaga tug'ilgan kunga 10$ ichida sovg'a tavsiya qiling»</i>\n"
    "🎨 <i>«Yog'och uchun mos lak bormi?»</i>\n\n"
    "<b>Savolingizni yozing</b> 👇"
)


@router.message(F.text == "💬 AI sotuvchi")
async def client_consult_start(message: Message, state: FSMContext):
    if not db.is_ai_consult_enabled():
        await message.answer(
            "ℹ️ AI sotuvchi-konsultant hozircha o'chirilgan.",
            reply_markup=client_menu()
        )
        return
    ok, why = ai_is_available()
    if not ok:
        await message.answer(
            f"{why}\n\nIltimos, admin bilan bog'laning.",
            reply_markup=client_menu(), parse_mode="HTML"
        )
        return
    c = await db.get_client_by_tg(message.from_user.id)
    if not c:
        return
    await state.set_state(ClientConsultStates.asking)
    await state.update_data(client_id=c["id"])
    await message.answer(CONSULT_INTRO, reply_markup=cancel_kb(), parse_mode="HTML")


@router.callback_query(F.data == "cc_again")
async def client_consult_again(cb: CallbackQuery, state: FSMContext):
    if not db.is_ai_consult_enabled():
        await cb.answer("O'chirilgan.", show_alert=True)
        return
    await state.set_state(ClientConsultStates.asking)
    await cb.message.answer(CONSULT_INTRO, reply_markup=cancel_kb(),
                              parse_mode="HTML")
    await cb.answer()


@router.message(ClientConsultStates.asking, F.text & ~F.text.in_(RESERVED_MENU))
async def client_consult_input(message: Message, state: FSMContext):
    """Mijoz savoliga AI dan to'liq konsultatsiya javob olamiz."""
    q = (message.text or "").strip()
    if not q:
        return
    if len(q) < 4:
        await message.answer(
            "⚠️ Iltimos, savolingizni to'liqroq yozing (kamida 4 ta belgi)."
        )
        return
    if len(q) > 500:
        await message.answer(
            "⚠️ Savol juda uzun — 500 belgigacha qisqartiring."
        )
        return

    wait_msg = await message.answer(
        "⏳ AI sotuvchi o'ylab, sizning savolingizga javob tayyorlamoqda…"
    )

    # 1) Kalit so'zlarni ajratish (kompyuter → noutbuk, macbook, …)
    keywords = await expand_query_keywords(q)
    # Asl so'rovni ham qo'shamiz (yuqorida qo'shilgan, lekin xavfsizlik uchun)
    if q.lower() not in {k.lower() for k in keywords}:
        keywords = [q] + keywords

    # 2) Har bir kalit so'z bo'yicha DB qidiruv — birlashtirib, dublikatsiz
    candidates = []
    seen_ids = set()
    for kw in keywords[:8]:
        if len(candidates) >= 30:
            break
        try:
            rows = await db.search_products(kw, limit=8)
        except Exception as e:
            logger.warning(f"search_products failed for kw={kw}: {e}")
            rows = []
        for p in rows:
            if p["id"] not in seen_ids:
                seen_ids.add(p["id"])
                candidates.append(p)
        if len(candidates) >= 30:
            break

    # Log: mijoz konsultatsiya so'radi (AI analitika uchun)
    try:
        await db.log_search(message.from_user.id, q, len(candidates),
                             source="ai_consult")
    except Exception as e:
        logger.warning(f"log_search failed for user {message.from_user.id}: {e}")

    # 3) AI ga savolni va topilgan tovarlarni yuborib, javob olamiz
    answer = await consult_client(q, candidates)

    # 4) AI javobidagi #ID larni ajratib, mahsulot tugmalarini taklif qilamiz
    mentioned_ids = extract_product_ids_from_text(answer)
    mentioned_products = []
    for pid in mentioned_ids[:6]:
        p = await db.get_product_any(pid)
        if p and p.get("is_active", 1):
            mentioned_products.append(p)

    # Javobni chiqaramiz (eski "kutib turing" xabarini yangilash)
    try:
        await wait_msg.delete()
    except Exception:
        pass

    header = f"💬 <b>AI sotuvchi javobi:</b>\n\n"
    full = header + answer
    if mentioned_products:
        kb = client_consult_followup_kb(mentioned_products)
        try:
            await message.answer(full, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await message.answer(full[:4000], reply_markup=kb)
    else:
        # Tugma uchun mahsulot yo'q — oddiy reply
        try:
            await message.answer(full, parse_mode="HTML")
        except Exception:
            await message.answer(full[:4000])
        # Yana savol berish taklifini reply tugmasi bilan
        await message.answer(
            "Yana savolingiz bormi? Yozing yoki ❌ Bekor qilish tugmasini bosing.",
            reply_markup=cancel_kb()
        )


@router.message(ClientConsultStates.asking, F.text == "❌ Bekor qilish")
async def client_consult_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Bekor.", reply_markup=client_menu())


@router.callback_query(F.data.startswith("cs_order_"))
async def client_search_order(cb: CallbackQuery, state: FSMContext):
    """Karta tagidagi '🛒 Buyurtma qilish' — mavjud OrderStates oqimiga ulanamiz."""
    if not db.is_client_orders_enabled():
        await cb.answer("Buyurtma berish o'chirilgan.", show_alert=True)
        return
    pid = int(cb.data.rsplit("_", 1)[1])
    p = await db.get_product(pid)
    if not p:
        await cb.answer("Topilmadi.", show_alert=True)
        return
    if (p.get("qty", 0) or 0) <= 0:
        await cb.answer("Hozircha tugagan!", show_alert=True)
        return
    c = await db.get_client_by_tg(cb.from_user.id)
    if not c:
        await cb.answer()
        return
    ctype = _ctype(c)
    # OrderStates.qty oqimiga o'tamiz — order_qty handler savatga qo'shadi.
    data = await state.get_data()
    cart = data.get("cart", {})
    await state.update_data(
        cart=cart, client_id=c["id"], client_type=ctype,
        order_pid=pid, order_page=0,
    )
    await state.set_state(OrderStates.qty)
    unit = p.get("unit", "dona")
    usd, summ = _price_pair_for(p, ctype)
    if usd > 0:
        price_line = f"💰 <b>${usd:,.2f}/{unit}</b>  (≈ {summ:,.0f} so'm/{unit})"
    else:
        price_line = f"💰 {summ:,.0f} so'm/{unit}"
    await cb.message.answer(
        f"🛒 <b>{p['name']}</b>\n"
        f"{price_line}\n"
        f"📦 Mavjud: {p.get('qty', 0):g} {unit}\n\nNechta {unit} kerak?",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )
    await cb.answer()
