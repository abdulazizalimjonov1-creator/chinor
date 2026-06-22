from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from bot.barcode import decode_barcode

from bot.keyboards import (
    admin_menu, glavniy_menu, sale_products_kb, sale_variants_kb,
    sale_payment_kb, sale_confirm_kb, sale_nasiya_clients_kb, cancel_kb,
    sale_choose_client_kb, sale_client_search_results_kb,
    sale_price_kb, sale_paid_kb,
    _price_pair_for, _price_usd_for,
    cart_edit_kb, cart_item_kb, category_filter_kb, RESERVED_MENU,
)
from bot.states import SaleStates
from bot.permissions import (
    has_permission, get_currency_mode, show_usd, show_uzs, effective_permissions,
    is_admin_or_glavniy, get_user_menu, deny, require,
)
from database.channel_db import db, fmt_usd, fmt_sum, usd_to_sum, sum_to_usd


async def _menu_for_user(uid: int):
    # Markaziy helperga ko'prik (sale.py ichida ko'p ishlatiladi)
    return await get_user_menu(db, uid)


def _parse_money(txt: str) -> tuple:
    """Foydalanuvchi kiritgan summani tahlil qiladi.
    Qaytaradi: (amount, currency) — currency = 'usd' yoki 'sum'.
    '$1.25', '1.25$', 'usd 1.25' → (1.25, 'usd')
    '150000', '150,000' → (150000, 'sum')
    Xato bo'lsa ValueError."""
    s = (txt or "").strip().lower()
    if not s:
        raise ValueError("bo'sh")
    is_usd = False
    if "$" in s or "usd" in s or "dollar" in s:
        is_usd = True
        s = s.replace("$", "").replace("usd", "").replace("dollar", "")
    s = s.replace(" ", "").replace(",", ".")
    # Agar ko'p nuqta bor bo'lsa — oxirgisi kasr ajratuvchi, qolganlari mingliklar
    if s.count(".") > 1:
        # masalan '12.500.000' → '12500000'
        parts = s.split(".")
        s = "".join(parts[:-1]) + "." + parts[-1] if len(parts[-1]) <= 2 else "".join(parts)
    val = float(s)
    if val < 0:
        raise ValueError("manfiy")
    return val, ("usd" if is_usd else "sum")

router = Router()


async def _is_admin(uid: int) -> bool:
    # Markaziy helperga ko'prik
    return await is_admin_or_glavniy(db, uid)


def _fmt_qty(qty: float) -> str:
    if qty == int(qty):
        return f"{int(qty)}"
    return f"{qty:g}"


def _cart_text(cart: dict, override_usd: float = 0,
               override_sum: float = 0) -> str:
    """Savatni ko'rsatish — har bir qator USD da; pastida JAMI USD VA so'm.
    cart[*]['price'] — USD; 'price_sum' — joriy kurs bo'yicha so'm.
    override_* > 0 bo'lsa — chegirilgan jami summa."""
    if not cart:
        return "🛒 Savat bo'sh"
    lines = ""
    total_usd = 0.0
    total_sum = 0.0
    for v in cart.values():
        usd_line = v["qty"] * v["price"]
        sum_line = v["qty"] * v.get("price_sum", 0)
        total_usd += usd_line
        total_sum += sum_line
        unit = v.get("unit", "dona")
        lines += (
            f"• {v['name']}: {_fmt_qty(v['qty'])} {unit} × ${v['price']:,.2f} "
            f"= ${usd_line:,.2f}  ({sum_line:,.0f} so'm)\n"
        )
    out = (
        f"🛒 <b>Savat:</b>\n{lines}\n"
        f"💰 <b>Jami: {fmt_usd(total_usd)}</b>\n"
        f"💴 <b>So'mda:  {fmt_sum(total_sum)}</b>"
    )
    if override_usd > 0 or override_sum > 0:
        d_usd = max(0.0, total_usd - override_usd) if override_usd > 0 else 0
        d_sum = max(0.0, total_sum - override_sum) if override_sum > 0 else 0
        out += (
            f"\n💸 <b>Chegirma:</b> "
            f"{fmt_usd(d_usd)}  ({fmt_sum(d_sum)})\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"✅ <b>To'lash kerak: {fmt_usd(override_usd)}</b>\n"
            f"💴 <b>So'mda: {fmt_sum(override_sum)}</b>"
        )
    return out


def _cart_total_usd(cart: dict) -> float:
    return sum(v["qty"] * v["price"] for v in cart.values())


def _cart_total_sum(cart: dict) -> float:
    return sum(v["qty"] * v.get("price_sum", 0) for v in cart.values())


def _fmt_pay_line(label: str, amt_sum: float, amt_usd: float, paid_currency: str) -> str:
    """'💵 Naqd: $20.00 (≈ 250,000 so'm)' yoki teskari."""
    if paid_currency == "usd" and amt_usd > 0:
        return f"{label}: <b>{fmt_usd(amt_usd)}</b>  (≈ {fmt_sum(amt_sum)})"
    return f"{label}: <b>{fmt_sum(amt_sum)}</b>  (≈ {fmt_usd(amt_usd)})" if amt_usd > 0 \
        else f"{label}: <b>{fmt_sum(amt_sum)}</b>"


def _receipt_text(sale: dict) -> str:
    rate = sale.get("usd_rate") or db.get_usd_rate()
    lines = ""
    for i in sale["items"]:
        unit = i.get("unit", "dona")
        usd_total = i.get("total_usd", 0)
        sum_total = i.get("total", 0)
        usd_price = i.get("price_usd", 0)
        if usd_price or usd_total:
            lines += (
                f"  {i['name']}\n"
                f"  {_fmt_qty(i['qty'])} {unit} × ${usd_price:,.2f} "
                f"= ${usd_total:,.2f}  ({sum_total:,.0f} so'm)\n"
            )
        else:
            lines += (
                f"  {i['name']}\n"
                f"  {_fmt_qty(i['qty'])} {unit} × {i['price']:,.0f} "
                f"= {sum_total:,.0f} so'm\n"
            )
    total_usd = sale.get("total_usd", 0)
    total_sum = sale["total"]
    sub_usd = sale.get("subtotal_usd", 0) or total_usd
    sub_sum = sale.get("subtotal", 0) or total_sum
    disc_usd = sale.get("discount_usd", 0) or 0
    disc_sum = sale.get("discount", 0) or 0
    paid_currency = (sale.get("paid_currency") or "sum").lower()
    paid_parts = []
    if sale.get("is_nasiya"):
        client_name = sale.get("client_name", "")
        paid_parts.append(
            f"🤝 Nasiya — {client_name}: <b>{fmt_usd(total_usd)}</b>  "
            f"(≈ {fmt_sum(total_sum)}) QARZGA YOZILDI"
        )
    else:
        if sale.get("paid_cash", 0) > 0 or sale.get("paid_cash_usd", 0) > 0:
            paid_parts.append(_fmt_pay_line(
                "💵 Naqd", sale.get("paid_cash", 0),
                sale.get("paid_cash_usd", 0), paid_currency
            ))
        if sale.get("paid_card", 0) > 0 or sale.get("paid_card_usd", 0) > 0:
            paid_parts.append(_fmt_pay_line(
                "💳 Karta", sale.get("paid_card", 0),
                sale.get("paid_card_usd", 0), paid_currency
            ))
        if sale.get("paid_other", 0) > 0 or sale.get("paid_other_usd", 0) > 0:
            paid_parts.append(_fmt_pay_line(
                "🔄 Boshqa", sale.get("paid_other", 0),
                sale.get("paid_other_usd", 0), paid_currency
            ))
    change_lines = []
    if sale.get("change", 0) > 0 or sale.get("change_usd", 0) > 0:
        ch_sum = sale.get("change", 0)
        ch_usd = sale.get("change_usd", 0)
        if paid_currency == "usd" and ch_usd > 0:
            change_lines.append(f"💱 Qaytim: <b>{fmt_usd(ch_usd)}</b>  (≈ {fmt_sum(ch_sum)})")
        else:
            change_lines.append(f"💱 Qaytim: <b>{fmt_sum(ch_sum)}</b>"
                                + (f"  (≈ {fmt_usd(ch_usd)})" if ch_usd > 0 else ""))
    client_line = ""
    if sale.get("client_name") and not sale.get("is_nasiya"):
        client_line = f"\n👤 Mijoz: <b>{sale['client_name']}</b>"
    discount_line = ""
    if disc_usd > 0 or disc_sum > 0:
        discount_line = (
            f"\n🧮 Subtotal: {fmt_usd(sub_usd)}  ({fmt_sum(sub_sum)})"
            f"\n💸 Chegirma: <b>−{fmt_usd(disc_usd)}</b>  (−{fmt_sum(disc_sum)})"
        )
    if total_usd > 0:
        total_block = (
            f"{discount_line}\n"
            f"💰 <b>Jami: {fmt_usd(total_usd)}</b>{client_line}\n"
            f"💴 <b>So'mda: {fmt_sum(total_sum)}</b>\n"
            f"💱 <i>Kurs: 1$ = {rate:,.0f} so'm</i>"
        )
    else:
        total_block = f"{discount_line}\n💰 <b>Jami: {fmt_sum(total_sum)}</b>{client_line}"
    body = (
        f"🧾 <b>CHEK #{sale['id']}</b>\n"
        f"{'─' * 22}\n"
        f"{lines}"
        f"{'─' * 22}"
        f"{total_block}\n"
    )
    if paid_parts:
        body += "\n".join(paid_parts) + "\n"
    if change_lines:
        body += "\n".join(change_lines) + "\n"
    body += (
        f"{'─' * 22}\n"
        f"✅ Sotuv amalga oshirildi!\n"
        f"📅 {sale['created_at'][:16]}"
    )
    return body


# ── Mijozga chek + mahsulot rasmlarini yuborish ─────────────────────────────

async def _send_sale_to_client(bot, client_id: int, sale: dict) -> tuple:
    """
    Mijozga chek matni va sotilgan mahsulotlar rasmini yuboradi.
    Qaytadi: (yuborildi: bool, sabab: str)
    """
    if not client_id:
        return False, "mijoz tanlanmagan"
    c = await db.get_client_by_id(client_id)
    if not c:
        return False, "mijoz topilmadi"
    tg_id = c.get("telegram_id", 0)
    if not tg_id:
        return False, "mijozda telegram_id yo'q"

    intro = (
        f"🧾 <b>Yangi chek sizga keldi!</b>\n"
        f"👤 {c.get('shop_name','')}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    try:
        await bot.send_message(tg_id, intro, parse_mode="HTML")
        await bot.send_message(tg_id, _receipt_text(sale), parse_mode="HTML")
    except Exception as e:
        return False, f"chek yuborilmadi: {e}"

    # Har bir mahsulot rasmi
    sent_photos = 0
    for it in sale.get("items", []):
        try:
            p = await db.get_product_any(it["product_id"])
        except Exception:
            p = None
        if not p or not p.get("image_file_id"):
            continue
        cap = (
            f"📦 <b>{it['name']}</b>\n"
            f"{_fmt_qty(it['qty'])} {it.get('unit','dona')} × "
            f"{it['price']:,.0f} = {it['total']:,.0f} so'm"
        )
        try:
            await bot.send_photo(tg_id, p["image_file_id"], caption=cap, parse_mode="HTML")
            sent_photos += 1
        except Exception:
            # rasm yuborilmasa, jim o'tkazib yuboramiz
            pass
    return True, f"chek + {sent_photos} ta rasm yuborildi"


# ── Yordamchi: mahsulot ro'yxatiga qaytish ───────────────────────────────────

async def _back_to_list(target, state: FSMContext):
    """Mahsulotlar ro'yxatini ko'rsatadi (Message yoki CallbackQuery uchun).
    Ro'yxat SOTUV REYTINGI bo'yicha tartiblangan, 6/sahifa — joriy sahifa
    holatda (prod_page) saqlanadi."""
    avail = await db.top_selling_products()   # ranked, faqat qoldig'i borlar
    data = await state.get_data()
    cart = data.get("cart", {})
    ctype = (data.get("sale_client_type") or "dona").lower()
    cmode = await get_currency_mode(db, target.from_user.id)
    page = data.get("prod_page", 0)
    await state.set_state(SaleStates.scanning)

    header = ""
    if data.get("sale_client_name"):
        type_label = "🛍️ Donachi" if ctype == "dona" else "📦 Optomchi"
        header = f"👤 Mijoz: <b>{data['sale_client_name']}</b> ({type_label})\n\n"
    text = f"{header}{_cart_text(cart)}\n\n📦 Mahsulot tanlang yoki qidiring:"
    kb = sale_products_kb(avail, cart, ctype, cmode, page=page)

    if isinstance(target, CallbackQuery):
        await target.message.answer(text, reply_markup=kb, parse_mode="HTML")
        await target.answer()
    else:
        await target.answer(text, reply_markup=kb, parse_mode="HTML")


# ── Boshlash ──────────────────────────────────────────────────────────────────

async def _show_client_picker(target, state: FSMContext):
    """Mijoz tanlash ekranini ko'rsatadi (Message/CallbackQuery uchun)."""
    clients = await db.get_all_clients()
    text = (
        "🧾 <b>Sotuv paneli</b>\n\n"
        "👤 Avval mijozni tanlang yoki <b>«Mijozsiz davom etish»</b>ni bosing.\n"
        "📨 Mijoz tanlansa — sotuv yakunida unga <b>chek va mahsulotlar rasmi</b>\n"
        "avtomatik Telegram orqali yuboriladi."
    )
    kb = sale_choose_client_kb(clients)
    await state.set_state(SaleStates.choosing_client)
    if isinstance(target, CallbackQuery):
        await target.message.answer(text, reply_markup=kb, parse_mode="HTML")
        await target.answer()
    else:
        await target.answer(text, reply_markup=kb, parse_mode="HTML")


async def _go_to_scanning(target, state: FSMContext):
    """Mahsulot tanlash ekraniga o'tadi. Ro'yxat sotuv reytingi bo'yicha
    tartiblangan — 1-sahifa = 6 ta eng ko'p sotiladigan mahsulot."""
    avail = await db.top_selling_products()   # ranked, faqat qoldig'i borlar
    data = await state.get_data()
    cart = data.get("cart", {})
    client_name = data.get("sale_client_name", "")
    ctype = (data.get("sale_client_type") or "dona").lower()
    rate = db.get_usd_rate()
    cmode = await get_currency_mode(db, target.from_user.id)
    header = "🧾 <b>Sotuv paneli</b>"
    if client_name:
        dona_on = db.is_dona_enabled()
        whs_on = db.is_wholesale_enabled()
        if dona_on and whs_on:
            # Har ikkalasi yoqilgan — mijoz turi muhim
            type_label = "🛍️ Donachi (chakana)" if ctype == "dona" else "📦 Optomchi (ulgurji) — optom narx"
            header += f"\n👤 Mijoz: <b>{client_name}</b> · {type_label}"
        elif whs_on and not dona_on:
            header += f"\n👤 Mijoz: <b>{client_name}</b> · 📦 optom narx"
        elif dona_on and not whs_on:
            header += f"\n👤 Mijoz: <b>{client_name}</b> · 🛍️ dona narx"
        else:
            header += f"\n👤 Mijoz: <b>{client_name}</b>"
    # Kurs satrini faqat 'hybrid' rejimda ko'rsatamiz (ortiqcha yozuv chiqmasin)
    if cmode == "hybrid":
        header += f"\n💱 Kurs: 1$ = {rate:,.0f} so'm"
    if not avail:
        await (target.message if isinstance(target, CallbackQuery) else target).answer(
            "⚠️ Omborda mahsulot qolmagan!", reply_markup=await _menu_for_user(target.from_user.id)
        )
        await state.clear()
        if isinstance(target, CallbackQuery):
            await target.answer()
        return
    await state.set_state(SaleStates.scanning)
    await state.update_data(prod_page=0)   # yangi sotuv — 1-sahifadan boshlaymiz
    text = (
        f"{header}\n\n"
        f"{_cart_text(cart)}\n\n"
        f"📦 Mahsulotni tanlang, ◀️▶️ bilan varaqlang yoki 🔍 orqali qidiring:"
    )
    kb = sale_products_kb(avail, cart, ctype, cmode, page=0)
    if isinstance(target, CallbackQuery):
        await target.message.answer(text, reply_markup=kb, parse_mode="HTML")
        await target.answer()
    else:
        await target.answer(text, reply_markup=kb, parse_mode="HTML")


@router.message(F.text == "🧾 Sotuv")
async def sale_start(message: Message, state: FSMContext):
    uid = message.from_user.id
    if not await _is_admin(uid):
        return
    if not await has_permission(db, uid, "sale"):
        await message.answer(
            "⛔ Sizda kassa sotuvi uchun ruxsat yo'q.",
            reply_markup=await _menu_for_user(uid)
        )
        return
    prods = await db.get_all_products()
    avail = [p for p in prods if p.get("qty", 0) > 0]
    if not avail:
        await message.answer(
            "⚠️ Omborda mahsulot qolmagan!",
            reply_markup=await _menu_for_user(uid)
        )
        return
    await state.update_data(
        cart={},
        cashier_id=message.from_user.id,
        cashier_name=message.from_user.full_name,
        sale_client_id=0,
        sale_client_name="",
        sale_client_tg_id=0,
        sale_client_type="dona",
    )
    await _show_client_picker(message, state)


# ── Mijoz tanlash bosqichi ──────────────────────────────────────────────────

@router.callback_query(F.data == "sale_no_client", SaleStates.choosing_client)
async def sale_no_client(cb: CallbackQuery, state: FSMContext):
    await state.update_data(
        sale_client_id=0, sale_client_name="", sale_client_tg_id=0,
        sale_client_type="dona",
    )
    # Mijozsiz — savatda boshqa narxlar bo'lishi mumkin, tozalaymiz
    await state.update_data(cart={})
    await _go_to_scanning(cb, state)


@router.callback_query(F.data.startswith("sale_pick_client_"))
async def sale_pick_client(cb: CallbackQuery, state: FSMContext):
    try:
        cid = int(cb.data.split("_")[3])
    except (ValueError, IndexError):
        await cb.answer("❌ Noto'g'ri mijoz!")
        return
    c = await db.get_client_by_id(cid)
    if not c:
        await cb.answer("❌ Mijoz topilmadi!", show_alert=True)
        return
    ctype = (c.get("client_type") or "dona").lower()
    await state.update_data(
        sale_client_id=cid,
        sale_client_name=c.get("shop_name", ""),
        sale_client_tg_id=c.get("telegram_id") or 0,
        sale_client_type=ctype,
        # Mijoz turi o'zgarganda savatda eski narxlar qolmasin
        cart={},
    )
    tg_note = ""
    if c.get("telegram_id"):
        tg_note = "\n📨 Sotuv tugagach unga chek va mahsulotlar rasmi yuboriladi."
    dona_on = db.is_dona_enabled()
    whs_on = db.is_wholesale_enabled()
    if dona_on and whs_on:
        type_label = "🛍️ Donachi (chakana narx)" if ctype == "dona" else "📦 Optomchi — <b>optom narx</b> qo'llaniladi"
        await cb.message.answer(
            f"✅ Tanlangan mijoz: <b>{c.get('shop_name','')}</b>\n"
            f"🏷️ Turi: {type_label}{tg_note}",
            parse_mode="HTML"
        )
    elif whs_on and not dona_on:
        await cb.message.answer(
            f"✅ Tanlangan mijoz: <b>{c.get('shop_name','')}</b>\n"
            f"🏷️ 📦 <b>Optom narx</b> qo'llaniladi{tg_note}",
            parse_mode="HTML"
        )
    elif dona_on and not whs_on:
        await cb.message.answer(
            f"✅ Tanlangan mijoz: <b>{c.get('shop_name','')}</b>\n"
            f"🏷️ 🛍️ <b>Dona narx</b> qo'llaniladi{tg_note}",
            parse_mode="HTML"
        )
    else:
        await cb.message.answer(
            f"✅ Tanlangan mijoz: <b>{c.get('shop_name','')}</b>{tg_note}",
            parse_mode="HTML"
        )
    await _go_to_scanning(cb, state)


@router.callback_query(F.data == "sale_client_search", SaleStates.choosing_client)
async def sale_client_search_start(cb: CallbackQuery, state: FSMContext):
    await state.set_state(SaleStates.client_search)
    await cb.message.answer(
        "🔍 <b>Mijozni qidirish</b>\n\n"
        "Mijozning <b>nomi</b> yoki <b>telefon raqamini</b> yozing:\n"
        "(masalan: <code>Aziz</code> yoki <code>9989</code>)",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )
    await cb.answer()


@router.callback_query(F.data == "sale_client_back")
async def sale_client_back(cb: CallbackQuery, state: FSMContext):
    await _show_client_picker(cb, state)


@router.message(SaleStates.client_search)
async def sale_client_search_input(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await _show_client_picker(message, state)
        return
    q = (message.text or "").strip().lower()
    if not q:
        await message.answer("⚠️ Iltimos, biror so'z kiriting:")
        return
    clients = await db.get_all_clients()
    results = [
        c for c in clients
        if q in (c.get("shop_name", "") or "").lower()
        or q in (c.get("phone", "") or "").lower()
        or q == str(c.get("id", ""))
        or q == str(c.get("telegram_id", ""))
    ]
    if not results:
        await message.answer(
            f"❌ <b>«{message.text}»</b> bo'yicha mijoz topilmadi.\n"
            "Qayta kiriting yoki ❌ Bekor qilish tugmasini bosing.",
            reply_markup=cancel_kb(), parse_mode="HTML"
        )
        return
    await state.set_state(SaleStates.choosing_client)
    await message.answer(
        f"🔍 <b>{len(results)} ta mijoz topildi</b>. Birini tanlang:",
        reply_markup=sale_client_search_results_kb(results), parse_mode="HTML"
    )


# ── Qidirish ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "sale_search", SaleStates.scanning)
async def sale_search_start(cb: CallbackQuery, state: FSMContext):
    await state.set_state(SaleStates.search_input)
    if db.is_barcode_enabled():
        text = (
            "🔍 <b>Qidirish</b>\n\n"
            "Quyidagi usullardan birini tanlang:\n"
            "• 📷 Mahsulotning <b>shtrix-kod rasmini</b> yuboring\n"
            "• ⌨️ Shtrix-kodning <b>raqamini</b> yozing (masalan: <code>4780000000017</code>)\n"
            "• 🆔 Mahsulot <b>ID raqami</b> ni yozing (masalan: <code>3</code>)\n"
            "• 📝 Yoki mahsulot <b>nomi</b> ni yozing (masalan: <code>qog'oz</code>)"
        )
    else:
        text = (
            "🔍 <b>Qidirish</b>\n\n"
            "• 🆔 Mahsulot <b>ID raqami</b> ni yozing (masalan: <code>3</code>)\n"
            "• 📝 Yoki mahsulot <b>nomi</b> ni yozing (masalan: <code>qog'oz</code>)"
        )
    await cb.message.answer(text, reply_markup=cancel_kb(), parse_mode="HTML")
    await cb.answer()


@router.message(SaleStates.search_input)
async def sale_search_input(message: Message, state: FSMContext, bot: Bot):
    if message.text == "❌ Bekor qilish":
        await _back_to_list(message, state)
        return

    prods = await db.get_all_products()
    avail = [p for p in prods if p.get("qty", 0) > 0]
    results = []
    query = ""

    # Agar rasm yuborilgan bo'lsa — shtrix-kodni o'qib olamiz
    if message.photo:
        if not db.is_barcode_enabled():
            await message.answer(
                "ℹ️ Shtrix-kod skaneri o'chirilgan.\n"
                "Mahsulot <b>ID raqami</b> yoki <b>nomi</b>ni yozing:",
                reply_markup=cancel_kb(), parse_mode="HTML"
            )
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
                "⚠️ Bu rasmdan shtrix-kodni o'qib bo'lmadi.\n"
                "Iltimos, yaqinroqdan, ravshanroq suratga oling yoki "
                "kod raqamini qo'lda yozing:",
                reply_markup=cancel_kb()
            )
            return
        await message.answer(
            f"📷 Shtrix-kod o'qildi: <code>{scanned}</code>",
            parse_mode="HTML"
        )
        query = scanned
        results = [p for p in avail if (p.get("barcode") or "").strip() == scanned]
    else:
        query = (message.text or "").strip()
        if not query:
            await message.answer("⚠️ Iltimos, matn yoki shtrix-kod rasmini yuboring.")
            return
        # 1) Aniq barcode
        results = [p for p in avail if (p.get("barcode") or "").strip() == query]
        # 2) ID
        if not results and query.isdigit():
            results = [p for p in avail if p["id"] == int(query)]
        # 3) Barcode qismi (raqam)
        if not results and query.isdigit():
            results = [p for p in avail if query in (p.get("barcode") or "")]
        # 4) Nom
        if not results:
            q_lower = query.lower()
            results = [p for p in avail if q_lower in p["name"].lower()]

    # AI analitika uchun har bir qidiruvni jurnalga yozamiz (sotuvchi qidirgan)
    await db.log_search(message.from_user.id, query, len(results),
                        source="sale_search")
    if not results:
        await message.answer(
            f"❌ <b>«{query}»</b> bo'yicha mahsulot topilmadi!\n\n"
            "Qayta kiriting yoki ❌ Bekor qilish tugmasini bosing.",
            reply_markup=cancel_kb(), parse_mode="HTML"
        )
        return

    data = await state.get_data()
    ctype = (data.get("sale_client_type") or "dona").lower()
    if len(results) == 1:
        # Bitta natija – to'g'ridan-to'g'ri tanlash
        p = results[0]
        usd, summ = _price_pair_for(p, ctype)
        await state.update_data(
            sale_pid=p["id"],
            sale_default_price_usd=usd,
            sale_default_price_sum=summ,
            sale_unit=p.get("unit", "dona")
        )
        await state.set_state(SaleStates.entering_qty)
        cmode = await get_currency_mode(db, message.from_user.id)
        await _ask_qty(message, p, ctype, cmode)
        return

    # Ko'p natija – variant tanlash
    cart = data.get("cart", {})
    cmode = await get_currency_mode(db, message.from_user.id)
    await state.set_state(SaleStates.variants)
    await message.answer(
        f"🔍 <b>{len(results)} ta natija topildi</b>. Birini tanlang:",
        reply_markup=sale_variants_kb(results, cart, ctype, cmode), parse_mode="HTML"
    )


# ── Variant tanlash ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "sale_back_to_list")
async def sale_back_to_list(cb: CallbackQuery, state: FSMContext):
    await _back_to_list(cb, state)


# ── Mahsulot tanlash (inline tugma) ──────────────────────────────────────────

@router.callback_query(F.data.startswith("sale_add_"))
async def sale_add(cb: CallbackQuery, state: FSMContext):
    pid = int(cb.data.split("_")[2])
    p = await db.get_product(pid)
    if not p:
        await cb.answer("❌ Topilmadi!")
        return
    if p.get("qty", 0) <= 0:
        await cb.answer("⚠️ Omborda qolmagan!")
        return

    data = await state.get_data()
    ctype = (data.get("sale_client_type") or "dona").lower()
    usd, summ = _price_pair_for(p, ctype)
    await state.update_data(
        sale_pid=pid,
        sale_default_price_usd=usd,
        sale_default_price_sum=summ,
        sale_unit=p.get("unit", "dona")
    )
    await state.set_state(SaleStates.entering_qty)
    cmode = await get_currency_mode(db, cb.from_user.id)
    await _ask_qty(cb.message, p, ctype, cmode)
    await cb.answer()


async def _ask_qty(msg, p: dict, client_type: str = "dona", cmode: str = "hybrid"):
    unit = p.get("unit", "dona")
    if unit in ("kg", "g", "l", "ml"):
        hint = f"Miqdorni kiriting ({unit}), masalan: 0.5 yoki 1.250"
    else:
        hint = "Nechta? (butun yoki kasr, masalan: 2 yoki 0.5)"
    usd, summ = _price_pair_for(p, client_type)
    # Valyuta rejimiga qarab narxni formatlash
    if cmode == "uzs_only":
        price_line = f"💰 Narxi: <b>{summ:,.0f} so'm/{unit}</b>"
    elif cmode == "usd_only":
        price_line = f"💰 Narxi: <b>${usd:,.2f}/{unit}</b>" if usd > 0 else \
                     f"💰 Narxi: <b>{summ:,.0f} so'm/{unit}</b>"
    else:  # hybrid
        if usd > 0:
            price_line = f"💰 Narxi: <b>${usd:,.2f}/{unit}</b>  (≈ {summ:,.0f} so'm/{unit})"
        else:
            price_line = f"💰 Narxi: <b>{summ:,.0f} so'm/{unit}</b>"
    type_tag = ""
    dona_on = db.is_dona_enabled()
    whs_on = db.is_wholesale_enabled()
    if dona_on and whs_on:
        if (client_type or "dona").lower().startswith("opt"):
            type_tag = "  📦 <i>(optom)</i>"
    elif whs_on and not dona_on:
        type_tag = "  📦 <i>(optom)</i>"
    elif dona_on and not whs_on:
        type_tag = "  🛍️ <i>(dona)</i>"
    text = (
        f"📦 <b>{p['name']}</b>{type_tag}\n"
        f"{price_line}\n"
        f"📦 Qoldi: {p.get('qty', 0):g} {unit}\n\n"
        f"{hint}"
    )
    if p.get("image_file_id"):
        try:
            await msg.answer_photo(p["image_file_id"], caption=text,
                                   reply_markup=cancel_kb(), parse_mode="HTML")
            return
        except Exception:
            pass
    await msg.answer(text, reply_markup=cancel_kb(), parse_mode="HTML")


# ── Miqdor kiritish ───────────────────────────────────────────────────────────

@router.message(SaleStates.entering_qty)
async def sale_qty(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await _back_to_list(message, state)
        return

    try:
        qty = float(message.text.strip().replace(",", "."))
        if qty <= 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Musbat son kiriting (masalan: 1, 0.5, 1.250):")
        return

    data = await state.get_data()
    pid = data["sale_pid"]
    unit = data.get("sale_unit", "dona")
    ctype = (data.get("sale_client_type") or "dona").lower()
    p = await db.get_product(pid)
    if not p:
        await _back_to_list(message, state)
        return

    if qty > p.get("qty", 0):
        await message.answer(f"⚠️ Faqat {p['qty']:g} {unit} bor! Qayta kiriting:")
        return

    usd, summ = _price_pair_for(p, ctype)
    await state.update_data(sale_qty=qty)
    rate = db.get_usd_rate()
    if usd > 0:
        price_line = f"${usd:,.2f}/{unit}  (≈ {summ:,.0f} so'm/{unit})"
        line_total = f"${usd*qty:,.2f}  ({summ*qty:,.0f} so'm)"
    else:
        price_line = f"{summ:,.0f} so'm/{unit}"
        line_total = f"{summ*qty:,.0f} so'm"
    await state.set_state(SaleStates.entering_price)
    await message.answer(
        f"📦 <b>{p['name']}</b>\n"
        f"🔢 Miqdori: {qty:g} {unit}\n"
        f"💰 Standart narx: <b>{price_line}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🧮 Hozirgicha: <b>{line_total}</b>\n\n"
        f"Standart narxni saqlaysizmi yoki o'zingizniki kiritasizmi?",
        reply_markup=sale_price_kb(), parse_mode="HTML"
    )


@router.callback_query(F.data == "sale_price_default", SaleStates.entering_price)
async def sale_price_default(cb: CallbackQuery, state: FSMContext):
    await _add_to_cart_and_continue(cb, state, custom_usd=None)


@router.callback_query(F.data == "sale_price_custom", SaleStates.entering_price)
async def sale_price_custom(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    p = await db.get_product(data["sale_pid"])
    unit = p.get("unit", "dona") if p else "dona"
    ctype = (data.get("sale_client_type") or "dona").lower()
    usd, summ = (_price_pair_for(p, ctype) if p else (0, 0))
    await cb.message.answer(
        f"✏️ <b>Yangi narx kiriting</b> ({unit} uchun).\n"
        f"USDda: <code>4.5</code> yoki <code>$4.50</code>\n"
        f"So'mda: <code>56000</code>\n"
        f"Standart: <b>${usd:,.2f}</b>  (≈ {summ:,.0f} so'm)",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )
    await cb.answer()


@router.message(SaleStates.entering_price)
async def sale_price_input(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await _back_to_list(message, state)
        return
    try:
        amount, cur = _parse_money(message.text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer(
            "⚠️ Narxni kiriting:\n"
            "USDda: <code>4.5</code> yoki <code>$4.50</code>\n"
            "So'mda: <code>56000</code>",
            parse_mode="HTML"
        )
        return
    rate = db.get_usd_rate()
    if cur == "usd":
        custom_usd = amount
    else:
        custom_usd = round(sum_to_usd(amount, rate), 4)
    await _add_to_cart_and_continue(message, state, custom_usd=custom_usd)


async def _add_to_cart_and_continue(target, state: FSMContext, custom_usd):
    """Savatga qo'shish va mahsulot ro'yxatiga qaytish.
    custom_usd None — standart narxni ishlatamiz."""
    data = await state.get_data()
    pid = data["sale_pid"]
    qty = float(data.get("sale_qty", 0) or 0)
    unit = data.get("sale_unit", "dona")
    ctype = (data.get("sale_client_type") or "dona").lower()
    p = await db.get_product(pid)
    if not p or qty <= 0:
        # Hech bo'lmasa orqaga qaytamiz
        if isinstance(target, CallbackQuery):
            await _back_to_list(target, state)
            await target.answer()
        else:
            await _back_to_list(target, state)
        return
    rate = db.get_usd_rate()
    default_usd, default_sum = _price_pair_for(p, ctype)
    if custom_usd is None:
        usd = default_usd
        summ = default_sum
    else:
        usd = float(custom_usd)
        summ = round(usd_to_sum(usd, rate), 2)
    cart = data.get("cart", {})
    key = str(pid)
    if key in cart:
        # Bir xil mahsulot, bir xil narx — qo'shamiz; aks holda yangi qator
        if abs(cart[key].get("price", 0) - usd) < 1e-6:
            cart[key]["qty"] += qty
        else:
            # narx farq qilsa, alohida qator (asl narx qatorini saqlab,
            # yangi narx uchun yangi key bilan saqlaymiz)
            new_key = f"{pid}_{int(usd*100)}"
            if new_key in cart:
                cart[new_key]["qty"] += qty
            else:
                cart[new_key] = {
                    "product_id": pid, "name": p["name"], "qty": qty,
                    "price": usd, "price_sum": summ, "unit": unit
                }
    else:
        cart[key] = {
            "product_id": pid, "name": p["name"], "qty": qty,
            "price": usd, "price_sum": summ, "unit": unit
        }
    await state.update_data(cart=cart, sale_qty=0)

    avail = await db.top_selling_products()   # ranked, faqat qoldig'i borlar
    await state.set_state(SaleStates.scanning)
    _d = await state.get_data()
    page = _d.get("prod_page", 0)             # joriy sahifada qolamiz
    discount_note = ""
    if custom_usd is not None and abs(custom_usd - default_usd) > 1e-6:
        diff = (default_usd - custom_usd) * qty
        if diff > 0:
            discount_note = f"\n💸 Chegirma: {fmt_usd(diff)}"
        else:
            discount_note = f"\n📈 Qo'shildi: {fmt_usd(-diff)}"
    txt = (
        f"✅ Savatga qo'shildi!{discount_note}\n\n"
        f"{_cart_text(cart)}\n\n"
        f"➕ Yana tanlang yoki kassaga o'ting:"
    )
    cmode = await get_currency_mode(db, target.from_user.id)
    if isinstance(target, CallbackQuery):
        await target.message.answer(txt, reply_markup=sale_products_kb(avail, cart, ctype, cmode, page=page),
                                    parse_mode="HTML")
        await target.answer("✅ Qo'shildi")
    else:
        await target.answer(txt, reply_markup=sale_products_kb(avail, cart, ctype, cmode, page=page),
                            parse_mode="HTML")


# ── Savat boshqaruvi ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "sale_clear")
async def sale_clear(cb: CallbackQuery, state: FSMContext):
    await state.update_data(cart={})
    await cb.answer("🗑️ Savat tozalandi!")
    data = await state.get_data()
    ctype = (data.get("sale_client_type") or "dona").lower()
    page = data.get("prod_page", 0)
    avail = await db.top_selling_products()
    cmode = await get_currency_mode(db, cb.from_user.id)
    try:
        await cb.message.edit_reply_markup(
            reply_markup=sale_products_kb(avail, {}, ctype, cmode, page=page)
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("sale_page_"), SaleStates.scanning)
async def sale_page_nav(cb: CallbackQuery, state: FSMContext):
    """Kassa mahsulot ro'yxatida ◀️ / ▶️ sahifa navigatsiyasi."""
    arg = cb.data[len("sale_page_"):]
    if arg == "noop":
        await cb.answer()
        return
    try:
        page = int(arg)
    except ValueError:
        await cb.answer()
        return
    await state.update_data(prod_page=page)
    data = await state.get_data()
    cart = data.get("cart", {})
    ctype = (data.get("sale_client_type") or "dona").lower()
    cmode = await get_currency_mode(db, cb.from_user.id)
    avail = await db.top_selling_products()
    try:
        await cb.message.edit_reply_markup(
            reply_markup=sale_products_kb(avail, cart, ctype, cmode, page=page)
        )
    except Exception:
        pass
    await cb.answer()


@router.callback_query(F.data == "sale_cancel")
async def sale_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.answer(
        "❌ Sotuv bekor qilindi.",
        reply_markup=await _menu_for_user(cb.from_user.id)
    )
    await cb.answer()


# ── Kassaga o'tish ────────────────────────────────────────────────────────────

async def _show_payment_screen(target, state: FSMContext):
    """Kassa ekrani — chegirma va to'lov turi tugmalari bilan."""
    data = await state.get_data()
    cart = data.get("cart", {})
    rate = db.get_usd_rate()
    total_usd = _cart_total_usd(cart)
    total_sum = _cart_total_sum(cart)
    ov_usd = float(data.get("override_total_usd", 0) or 0)
    ov_sum = float(data.get("override_total_sum", 0) or 0)
    has_disc = ov_usd > 0 or ov_sum > 0
    eff_usd = ov_usd if has_disc else total_usd
    eff_sum = ov_sum if has_disc else total_sum
    summary = (
        f"🧾 <b>KASSA</b>\n"
        f"💰 To'lash kerak: <b>{fmt_usd(eff_usd)}</b>\n"
        f"💴 So'mda: <b>{fmt_sum(eff_sum)}</b>\n"
        f"💱 Kurs: 1$ = {rate:,.0f} so'm\n"
    )
    text = (
        f"{_cart_text(cart, override_usd=ov_usd, override_sum=ov_sum)}\n\n"
        f"{summary}\n"
        f"💳 <b>To'lov turini tanlang:</b>"
    )
    kb = sale_payment_kb(has_discount=has_disc)
    if isinstance(target, CallbackQuery):
        await target.message.answer(text, reply_markup=kb, parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "sale_checkout")
async def sale_checkout(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cart = data.get("cart", {})
    if not cart:
        await cb.answer("⚠️ Savat bo'sh! Avval mahsulot tanlang.", show_alert=True)
        return
    await state.set_state(SaleStates.payment)
    await _show_payment_screen(cb, state)
    await cb.answer()


# ── Chegirma berish (jami summani yumaloqlash) ───────────────────────────────

@router.callback_query(F.data == "sale_discount", SaleStates.payment)
async def sale_discount_btn(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if data.get("override_total_usd") or data.get("override_total_sum"):
        # chegirmani o'chirish
        await state.update_data(override_total_usd=0, override_total_sum=0)
        await cb.answer("♻️ Chegirma o'chirildi")
        await _show_payment_screen(cb, state)
        return
    cart = data.get("cart", {})
    total_usd = _cart_total_usd(cart)
    total_sum = _cart_total_sum(cart)
    await state.set_state(SaleStates.entering_discount)
    await cb.message.answer(
        f"💸 <b>Yumaloqlangan jami summani kiriting</b>:\n\n"
        f"Hozirgi jami: <b>${total_usd:,.2f}</b>  ({total_sum:,.0f} so'm)\n\n"
        f"So'mda: masalan <code>200000</code>\n"
        f"USDda: masalan <code>15.50</code> yoki <code>$15.50</code>",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )
    await cb.answer()


@router.message(SaleStates.entering_discount)
async def sale_discount_input(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.set_state(SaleStates.payment)
        await _show_payment_screen(message, state)
        return
    try:
        amount, cur = _parse_money(message.text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer(
            "⚠️ Summani to'g'ri kiriting:\nSo'mda: <code>200000</code>\n"
            "USDda: <code>15.50</code> yoki <code>$15.50</code>",
            parse_mode="HTML"
        )
        return
    data = await state.get_data()
    cart = data.get("cart", {})
    total_usd = _cart_total_usd(cart)
    total_sum = _cart_total_sum(cart)
    rate = db.get_usd_rate()
    if cur == "usd":
        if amount > total_usd:
            await message.answer(
                f"⚠️ Yangi summa jami summadan katta. "
                f"Hozirgi jami: ${total_usd:,.2f}. Qayta kiriting:"
            )
            return
        await state.update_data(
            override_total_usd=amount,
            override_total_sum=round(usd_to_sum(amount, rate), 2),
        )
    else:
        if amount > total_sum:
            await message.answer(
                f"⚠️ Yangi summa jami summadan katta. "
                f"Hozirgi jami: {total_sum:,.0f} so'm. Qayta kiriting:"
            )
            return
        await state.update_data(
            override_total_sum=amount,
            override_total_usd=round(sum_to_usd(amount, rate), 4),
        )
    await state.set_state(SaleStates.payment)
    await message.answer("✅ Chegirma qo'llanildi!")
    await _show_payment_screen(message, state)


# ── To'lov summasini kiritish ────────────────────────────────────────────────

@router.callback_query(F.data.startswith("sale_paid_exact_"), SaleStates.payment)
async def sale_paid_exact(cb: CallbackQuery, state: FSMContext):
    method = cb.data.rsplit("_", 1)[1]  # cash | card
    data = await state.get_data()
    cart = data.get("cart", {})
    ov_usd = float(data.get("override_total_usd", 0) or 0)
    ov_sum = float(data.get("override_total_sum", 0) or 0)
    eff_sum = ov_sum if ov_sum > 0 else _cart_total_sum(cart)
    eff_usd = ov_usd if ov_usd > 0 else _cart_total_usd(cart)
    # Aynan jami — sotuvchi tilini bilmaymiz; default so'mda
    if method == "cash":
        await _finalize_sale(cb, state, pay_cash=eff_sum, pay_cash_usd=eff_usd,
                              paid_currency="sum")
    else:
        await _finalize_sale(cb, state, pay_card=eff_sum, pay_card_usd=eff_usd,
                              paid_currency="sum")


@router.callback_query(F.data.startswith("sale_paid_custom_"), SaleStates.payment)
async def sale_paid_custom(cb: CallbackQuery, state: FSMContext):
    method = cb.data.rsplit("_", 1)[1]
    data = await state.get_data()
    cart = data.get("cart", {})
    ov_usd = float(data.get("override_total_usd", 0) or 0)
    ov_sum = float(data.get("override_total_sum", 0) or 0)
    eff_sum = ov_sum if ov_sum > 0 else _cart_total_sum(cart)
    eff_usd = ov_usd if ov_usd > 0 else _cart_total_usd(cart)
    await state.update_data(paid_method=method)
    await state.set_state(SaleStates.entering_paid)
    await cb.message.answer(
        f"💵 <b>Mijoz qancha to'ladi?</b>\n\n"
        f"Jami: <b>${eff_usd:,.2f}</b>  ({eff_sum:,.0f} so'm)\n\n"
        f"So'mda: <code>200000</code>\n"
        f"USDda: <code>$20</code> yoki <code>15.50$</code>",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )
    await cb.answer()


@router.callback_query(F.data == "sale_paid_back")
async def sale_paid_back(cb: CallbackQuery, state: FSMContext):
    await state.set_state(SaleStates.payment)
    await _show_payment_screen(cb, state)
    await cb.answer()


@router.message(SaleStates.entering_paid)
async def sale_paid_input(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.set_state(SaleStates.payment)
        await _show_payment_screen(message, state)
        return
    try:
        amount, cur = _parse_money(message.text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer(
            "⚠️ Summani to'g'ri kiriting (so'mda yoki $USD):"
        )
        return
    data = await state.get_data()
    method = data.get("paid_method", "cash")
    rate = db.get_usd_rate()
    if cur == "usd":
        amount_usd = amount
        amount_sum = round(usd_to_sum(amount, rate), 2)
    else:
        amount_sum = amount
        amount_usd = round(sum_to_usd(amount, rate), 4)
    if method == "cash":
        await _finalize_sale(message, state,
                              pay_cash=amount_sum, pay_cash_usd=amount_usd,
                              paid_currency=cur)
    else:
        await _finalize_sale(message, state,
                              pay_card=amount_sum, pay_card_usd=amount_usd,
                              paid_currency=cur)


# ── Yakunlash (umumiy yordamchi) ─────────────────────────────────────────────

async def _finalize_sale(target, state: FSMContext, *,
                         pay_cash: float = 0, pay_cash_usd: float = 0,
                         pay_card: float = 0, pay_card_usd: float = 0,
                         paid_currency: str = "sum",
                         is_nasiya: bool = False, client_id: int = 0):
    """target — Message yoki CallbackQuery."""
    data = await state.get_data()
    cart = data.get("cart", {})
    if not cart:
        if isinstance(target, CallbackQuery):
            await target.answer("⚠️ Savat bo'sh!", show_alert=True)
        else:
            await target.answer("⚠️ Savat bo'sh!")
        return

    if not client_id:
        client_id = data.get("sale_client_id", 0) or 0

    if isinstance(target, CallbackQuery):
        await target.answer("⏳ Amalga oshirilmoqda...")

    user = target.from_user
    bot = target.bot if isinstance(target, CallbackQuery) else target.bot
    answer_msg = target.message if isinstance(target, CallbackQuery) else target

    items = [{"product_id": v["product_id"], "qty": v["qty"], "price": v["price"]}
             for v in cart.values()]
    ov_usd = float(data.get("override_total_usd", 0) or 0)
    ov_sum = float(data.get("override_total_sum", 0) or 0)

    try:
        sale = await db.create_sale(
            data.get("cashier_id", user.id),
            data.get("cashier_name", user.full_name),
            items,
            paid_cash=pay_cash, paid_cash_usd=pay_cash_usd,
            paid_card=pay_card, paid_card_usd=pay_card_usd,
            paid_currency=paid_currency,
            is_nasiya=is_nasiya,
            client_id=client_id,
            override_total_usd=ov_usd,
            override_total_sum=ov_sum,
        )
    except Exception as e:
        await answer_msg.answer(f"❌ Xatolik yuz berdi: {e}")
        return

    uid = user.id
    menu = await _menu_for_user(uid)
    await answer_msg.answer(_receipt_text(sale), parse_mode="HTML")

    # Mijozga chek va mahsulotlar rasmi yuborish
    if client_id:
        ok, info = await _send_sale_to_client(bot, client_id, sale)
        if ok:
            await answer_msg.answer(f"📨 Mijozga {info}.")
        else:
            await answer_msg.answer(
                f"⚠️ Mijozga yuborib bo'lmadi: <i>{info}</i>\n"
                f"(Mijoz botni ishga tushirgani va telegram_id to'g'ri ekaniga ishonch hosil qiling.)",
                parse_mode="HTML"
            )

    await state.clear()
    await answer_msg.answer(
        "🏠 Keyingi amalni tanlang:",
        reply_markup=sale_confirm_kb(sale["id"])
    )
    await answer_msg.answer("🏠 Bosh panel", reply_markup=menu)


# ── Naqd / Karta ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "sale_pay_cash", SaleStates.payment)
async def sale_pay_cash_menu(cb: CallbackQuery, state: FSMContext):
    """Naqd uchun summa va valyutani tanlash menyusi."""
    data = await state.get_data()
    cart = data.get("cart", {})
    ov_usd = float(data.get("override_total_usd", 0) or 0)
    ov_sum = float(data.get("override_total_sum", 0) or 0)
    eff_usd = ov_usd if ov_usd > 0 else _cart_total_usd(cart)
    eff_sum = ov_sum if ov_sum > 0 else _cart_total_sum(cart)
    await cb.message.answer(
        f"💵 <b>Naqd to'lov</b>\n\n"
        f"Jami: <b>${eff_usd:,.2f}</b>  ({eff_sum:,.0f} so'm)",
        reply_markup=sale_paid_kb("cash"), parse_mode="HTML"
    )
    await cb.answer()


@router.callback_query(F.data == "sale_pay_card", SaleStates.payment)
async def sale_pay_card_menu(cb: CallbackQuery, state: FSMContext):
    """Karta uchun summa va valyutani tanlash menyusi."""
    data = await state.get_data()
    cart = data.get("cart", {})
    ov_usd = float(data.get("override_total_usd", 0) or 0)
    ov_sum = float(data.get("override_total_sum", 0) or 0)
    eff_usd = ov_usd if ov_usd > 0 else _cart_total_usd(cart)
    eff_sum = ov_sum if ov_sum > 0 else _cart_total_sum(cart)
    await cb.message.answer(
        f"💳 <b>Karta to'lov</b>\n\n"
        f"Jami: <b>${eff_usd:,.2f}</b>  ({eff_sum:,.0f} so'm)",
        reply_markup=sale_paid_kb("card"), parse_mode="HTML"
    )
    await cb.answer()


# ── Nasiya (mijoz tanlash) ───────────────────────────────────────────────────

@router.callback_query(F.data == "sale_pay_nasiya")
async def sale_pay_nasiya(cb: CallbackQuery, state: FSMContext):
    # 'Nasiya' funksiyasi o'chirilgan bo'lsa — ishlamaydi (eski klaviatura himoyasi)
    if not db.is_nasiya_enabled():
        await cb.answer("ℹ️ Nasiya funksiyasi o'chirilgan.", show_alert=True)
        return
    data = await state.get_data()
    if not data.get("cart"):
        await cb.answer("⚠️ Savat bo'sh!", show_alert=True)
        return

    # Agar mijoz sotuv boshida allaqachon tanlangan bo'lsa — qayta so'ramaymiz
    pre_cid = data.get("sale_client_id", 0) or 0
    if pre_cid:
        await _finalize_sale(cb, state, is_nasiya=True, client_id=pre_cid)
        return

    clients = await db.get_all_clients()
    if not clients:
        await cb.answer("⚠️ Mijozlar ro'yxati bo'sh! Avval mijoz qo'shing.", show_alert=True)
        return
    await cb.message.answer(
        "🤝 <b>Nasiya — mijozni tanlang:</b>",
        reply_markup=sale_nasiya_clients_kb(clients), parse_mode="HTML"
    )
    await cb.answer()


@router.callback_query(F.data == "nasiya_back")
async def nasiya_back(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cart = data.get("cart", {})
    await cb.message.answer(
        f"{_cart_text(cart)}\n\n💳 To'lov turini tanlang:",
        reply_markup=sale_payment_kb(), parse_mode="HTML"
    )
    await cb.answer()


@router.callback_query(F.data.startswith("nasiya_client_"))
async def nasiya_client(cb: CallbackQuery, state: FSMContext):
    try:
        cid = int(cb.data.split("_")[2])
    except (ValueError, IndexError):
        await cb.answer("❌ Noto'g'ri mijoz!")
        return
    c = await db.get_client_by_id(cid)
    if not c:
        await cb.answer("❌ Mijoz topilmadi!", show_alert=True)
        return
    await _finalize_sale(cb, state, is_nasiya=True, client_id=cid)


# ── Chek ko'rish / yangi sotuv ────────────────────────────────────────────────

@router.callback_query(F.data.startswith("sale_receipt_"))
async def show_receipt(cb: CallbackQuery):
    sid = int(cb.data.split("_")[2])
    sale = await db.get_sale(sid)
    if not sale:
        await cb.answer("❌ Chek topilmadi!")
        return
    await cb.message.answer(_receipt_text(sale), parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "sale_new")
async def new_sale(cb: CallbackQuery, state: FSMContext):
    prods = await db.get_all_products()
    avail = [p for p in prods if p.get("qty", 0) > 0]
    if not avail:
        await cb.message.answer(
            "⚠️ Omborda mahsulot qolmagan!",
            reply_markup=await _menu_for_user(cb.from_user.id)
        )
        await cb.answer()
        return
    await state.update_data(
        cart={},
        cashier_id=cb.from_user.id,
        cashier_name=cb.from_user.full_name,
        sale_client_id=0,
        sale_client_name="",
        sale_client_tg_id=0,
        sale_client_type="dona",
    )
    await _show_client_picker(cb, state)


@router.callback_query(F.data == "sale_go_home")
async def sale_go_home(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.answer(
        "🏠 Bosh panel",
        reply_markup=await _menu_for_user(cb.from_user.id)
    )
    await cb.answer()


# ═══════════════════════════════════════════════════════════════════════════
#  «Yozsa darrov topadi» — sotuvda istalgan paytda shtrix-kod/nom/ID
# ═══════════════════════════════════════════════════════════════════════════

async def _dispatch_results(message: Message, state: FSMContext,
                             results: list, query: str):
    """Bitta natija — to'g'ridan-to'g'ri miqdor so'rash; ko'p — variantlar."""
    data = await state.get_data()
    ctype = (data.get("sale_client_type") or "dona").lower()
    if len(results) == 1:
        p = results[0]
        usd, summ = _price_pair_for(p, ctype)
        await state.update_data(
            sale_pid=p["id"],
            sale_default_price_usd=usd,
            sale_default_price_sum=summ,
            sale_unit=p.get("unit", "dona"),
        )
        await state.set_state(SaleStates.entering_qty)
        cmode = await get_currency_mode(db, message.from_user.id)
        await _ask_qty(message, p, ctype, cmode)
        return
    cart = data.get("cart", {})
    cmode = await get_currency_mode(db, message.from_user.id)
    await state.set_state(SaleStates.variants)
    await message.answer(
        f"🔍 <b>«{query}»</b> — {len(results)} ta natija. Birini tanlang:",
        reply_markup=sale_variants_kb(results, cart, ctype, cmode),
        parse_mode="HTML"
    )


@router.message(SaleStates.scanning, F.photo)
async def sale_scanning_photo(message: Message, state: FSMContext, bot: Bot):
    """Mahsulot tanlash ekranida shtrix-kod rasmi — darrov qidiradi."""
    if not db.is_barcode_enabled():
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
            "⚠️ Shtrix-kodni o'qib bo'lmadi. Yaqinroqdan suratga oling."
        )
        return
    await message.answer(f"📷 Shtrix-kod: <code>{scanned}</code>", parse_mode="HTML")
    prods = await db.get_all_products()
    avail = [p for p in prods if p.get("qty", 0) > 0]
    results = [p for p in avail if (p.get("barcode") or "").strip() == scanned]
    await db.log_search(message.from_user.id, scanned, len(results),
                        source="sale_barcode")
    if not results:
        await message.answer(
            f"❌ <code>{scanned}</code> bo'yicha mahsulot topilmadi yoki tugagan.",
            parse_mode="HTML"
        )
        return
    await _dispatch_results(message, state, results, scanned)


@router.message(SaleStates.scanning, F.text & ~F.text.in_(RESERVED_MENU))
async def sale_scanning_text(message: Message, state: FSMContext):
    """Mahsulot tanlash ekranida yozilgan matn — darrov qidiradi
    (shtrix-kod raqami / mahsulot ID / nom)."""
    query = (message.text or "").strip()
    if not query:
        return
    prods = await db.get_all_products()
    avail = [p for p in prods if p.get("qty", 0) > 0]
    # 1) Aniq shtrix-kod
    results = [p for p in avail
               if (p.get("barcode") or "").strip() == query and (p.get("barcode") or "").strip()]
    # 2) ID
    if not results and query.isdigit():
        results = [p for p in avail if p["id"] == int(query)]
    # 3) Shtrix-kod qismi
    if not results and query.isdigit():
        results = [p for p in avail if query in (p.get("barcode") or "")]
    # 4) Nom
    if not results:
        ql = query.lower()
        results = [p for p in avail if ql in p["name"].lower()]
    await db.log_search(message.from_user.id, query, len(results),
                        source="sale_quick")
    if not results:
        await message.answer(
            f"❌ <b>«{query}»</b> bo'yicha mahsulot topilmadi yoki tugagan.\n"
            "<i>Boshqa nom/kodni yozing yoki 🔍 tugmasini bosing.</i>",
            parse_mode="HTML"
        )
        return
    await _dispatch_results(message, state, results, query)


# ═══════════════════════════════════════════════════════════════════════════
#  Savatni tahrirlash  (cedit_* / citem_*)
# ═══════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "sale_cart_edit")
async def cart_edit_open(cb: CallbackQuery, state: FSMContext):
    if not db.is_cart_edit_enabled():
        await cb.answer("Savatni tahrirlash o'chirilgan.", show_alert=True)
        return
    data = await state.get_data()
    cart = data.get("cart", {})
    if not cart:
        await cb.answer("Savat bo'sh!", show_alert=True)
        return
    await state.set_state(SaleStates.cart_editing)
    await cb.message.answer(
        f"✏️ <b>Savatni tahrirlash</b>\n\n"
        f"{_cart_text(cart)}\n\n"
        "Qaysi qatorni o'zgartirasiz?",
        reply_markup=cart_edit_kb(cart), parse_mode="HTML"
    )
    await cb.answer()


@router.callback_query(F.data == "cedit_done")
async def cart_edit_done(cb: CallbackQuery, state: FSMContext):
    await _back_to_list(cb, state)


@router.callback_query(F.data == "citem_back")
async def cart_item_back(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cart = data.get("cart", {})
    await state.set_state(SaleStates.cart_editing)
    await cb.message.answer(
        f"✏️ <b>Savat:</b>\n\n{_cart_text(cart)}",
        reply_markup=cart_edit_kb(cart), parse_mode="HTML"
    )
    await cb.answer()


async def _show_cart_item(target, cart: dict, key: str):
    v = cart[key]
    unit = v.get("unit", "dona")
    line_total = v["qty"] * v["price"]
    text = (
        f"📦 <b>{v['name']}</b>\n"
        f"🔢 Miqdori: <b>{v['qty']:g} {unit}</b>\n"
        f"💰 Narxi: <b>${v['price']:,.2f}/{unit}</b>\n"
        f"🧮 Qator jami: <b>${line_total:,.2f}</b>"
    )
    kb = cart_item_kb(key)
    if isinstance(target, CallbackQuery):
        await target.message.answer(text, reply_markup=kb, parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("cedit_"))
async def cart_edit_pick(cb: CallbackQuery, state: FSMContext):
    """cedit_done allaqachon yuqorida; bu yerda — qator tanlash."""
    if cb.data == "cedit_done":
        return  # boshqa handler ushlaydi
    key = cb.data[len("cedit_"):]
    data = await state.get_data()
    cart = data.get("cart", {})
    if key not in cart:
        await cb.answer("Topilmadi.", show_alert=True)
        return
    await _show_cart_item(cb, cart, key)
    await cb.answer()


async def _citem_adjust(cb: CallbackQuery, state: FSMContext,
                         prefix: str, delta: float):
    key = cb.data[len(prefix):]
    data = await state.get_data()
    cart = dict(data.get("cart", {}))
    if key not in cart:
        await cb.answer("Topilmadi.", show_alert=True)
        return
    v = dict(cart[key])
    new_qty = v["qty"] + delta
    if new_qty <= 0:
        del cart[key]
        await state.update_data(cart=cart)
        await cb.answer("🗑 O'chirildi")
        if not cart:
            await state.set_state(SaleStates.scanning)
            await _back_to_list(cb, state)
            return
        await cb.message.answer(
            f"✏️ <b>Savat:</b>\n\n{_cart_text(cart)}",
            reply_markup=cart_edit_kb(cart), parse_mode="HTML"
        )
        return
    # Stok tekshiruvi (oshirganda)
    if delta > 0:
        p = await db.get_product(v["product_id"])
        if p and new_qty > (p.get("qty", 0) or 0):
            await cb.answer(f"⚠️ Faqat {p['qty']:g} ta bor!", show_alert=True)
            return
    v["qty"] = new_qty
    cart[key] = v
    await state.update_data(cart=cart)
    await cb.answer("✅")
    await _show_cart_item(cb, cart, key)


@router.callback_query(F.data.startswith("citem_inc_"))
async def citem_inc(cb: CallbackQuery, state: FSMContext):
    await _citem_adjust(cb, state, "citem_inc_", +1)


@router.callback_query(F.data.startswith("citem_dec_"))
async def citem_dec(cb: CallbackQuery, state: FSMContext):
    await _citem_adjust(cb, state, "citem_dec_", -1)


@router.callback_query(F.data.startswith("citem_rm_"))
async def citem_rm(cb: CallbackQuery, state: FSMContext):
    key = cb.data[len("citem_rm_"):]
    data = await state.get_data()
    cart = dict(data.get("cart", {}))
    if key in cart:
        del cart[key]
    await state.update_data(cart=cart)
    await cb.answer("🗑 O'chirildi")
    if not cart:
        await state.set_state(SaleStates.scanning)
        await _back_to_list(cb, state)
        return
    await cb.message.answer(
        f"✏️ <b>Savat:</b>\n\n{_cart_text(cart)}",
        reply_markup=cart_edit_kb(cart), parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("citem_set_"))
async def citem_set_start(cb: CallbackQuery, state: FSMContext):
    key = cb.data[len("citem_set_"):]
    data = await state.get_data()
    cart = data.get("cart", {})
    if key not in cart:
        await cb.answer("Topilmadi.", show_alert=True)
        return
    v = cart[key]
    await state.update_data(cedit_key=key)
    await state.set_state(SaleStates.cart_edit_qty)
    await cb.message.answer(
        f"✏️ <b>{v['name']}</b> — yangi miqdorni kiriting "
        f"({v.get('unit', 'dona')}):",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )
    await cb.answer()


@router.message(SaleStates.cart_edit_qty)
async def citem_set_done(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.set_state(SaleStates.cart_editing)
        data = await state.get_data()
        cart = data.get("cart", {})
        await message.answer(
            f"✏️ <b>Savat:</b>\n\n{_cart_text(cart)}",
            reply_markup=cart_edit_kb(cart), parse_mode="HTML"
        )
        return
    try:
        qty = float((message.text or "").strip().replace(",", "."))
        if qty < 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Musbat son kiriting (masalan: 2 yoki 0.5):")
        return
    data = await state.get_data()
    key = data.get("cedit_key")
    cart = dict(data.get("cart", {}))
    if key not in cart:
        await state.clear()
        await message.answer("Topilmadi.", reply_markup=await _menu_for_user(message.from_user.id))
        return
    if qty == 0:
        del cart[key]
    else:
        v = dict(cart[key])
        p = await db.get_product(v["product_id"])
        if p and qty > (p.get("qty", 0) or 0):
            await message.answer(f"⚠️ Faqat {p['qty']:g} ta bor! Qayta kiriting:")
            return
        v["qty"] = qty
        cart[key] = v
    await state.update_data(cart=cart)
    if not cart:
        await state.set_state(SaleStates.scanning)
        await _back_to_list(message, state)
        return
    await state.set_state(SaleStates.cart_editing)
    await message.answer(
        f"✅ Yangilandi!\n\n{_cart_text(cart)}",
        reply_markup=cart_edit_kb(cart), parse_mode="HTML"
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Kategoriya bo'yicha filtr (sotuvda)
# ═══════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data == "scatf_open")
async def scatf_open(cb: CallbackQuery, state: FSMContext):
    if not (db.is_categories_enabled() and db.is_cat_filter_enabled()):
        await cb.answer("Filtr o'chirilgan.", show_alert=True)
        return
    cats = await db.get_all_categories()
    if not cats:
        await cb.answer("Hali kategoriya yo'q.", show_alert=True)
        return
    await cb.message.answer(
        "🔻 <b>Kategoriya bo'yicha</b>:",
        reply_markup=category_filter_kb(cats, "scatf"), parse_mode="HTML"
    )
    await cb.answer()


@router.callback_query(F.data == "scatf_back")
async def scatf_back(cb: CallbackQuery, state: FSMContext):
    await _back_to_list(cb, state)


@router.callback_query(F.data == "scatf_all")
async def scatf_all(cb: CallbackQuery, state: FSMContext):
    await _back_to_list(cb, state)


@router.callback_query(F.data.startswith("scatf_"))
async def scatf_pick(cb: CallbackQuery, state: FSMContext):
    arg = cb.data[len("scatf_"):]
    if arg in ("open", "back", "all"):
        return   # boshqa handler ushlagan
    try:
        cid = int(arg)
    except ValueError:
        await cb.answer()
        return
    if cid == 0:
        all_prods = await db.get_all_products()
        prods = [p for p in all_prods
                 if not (p.get("category_id") or 0) and (p.get("qty", 0) or 0) > 0]
        title = "🚫 Kategoriyasiz"
    else:
        cprods = await db.get_products_by_category(cid)
        prods = [p for p in cprods if (p.get("qty", 0) or 0) > 0]
        c = await db.get_category(cid)
        title = f"🗂 {c['name'] if c else cid}"
    if not prods:
        await cb.answer("Bu filtrda mavjud mahsulot yo'q.", show_alert=True)
        return
    data = await state.get_data()
    cart = data.get("cart", {})
    ctype = (data.get("sale_client_type") or "dona").lower()
    cmode = await get_currency_mode(db, cb.from_user.id)
    await state.set_state(SaleStates.variants)
    await cb.message.answer(
        f"{title} — {len(prods)} ta mahsulot:",
        reply_markup=sale_variants_kb(prods, cart, ctype, cmode), parse_mode="HTML"
    )
    await cb.answer()
