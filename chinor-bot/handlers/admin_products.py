from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.keyboards import (
    admin_menu, products_list_kb, product_actions_kb, product_edit_fields_kb,
    cancel_kb, category_pick_kb, supplier_pick_kb, category_filter_kb,
    simple_products_kb, RESERVED_MENU,
)
from bot.states import AddProductStates, EditProductStates, QtyAddStates, ProductSearchStates
from bot.barcode import decode_barcode
from bot.permissions import (
    has_permission, get_user_menu, get_currency_mode, show_usd, show_uzs,
    is_admin_or_glavniy, deny, require,
)
from database.channel_db import db, fmt_usd, fmt_sum, usd_to_sum

router = Router()

UNITS = ["dona", "kg", "g", "l", "ml", "m", "sm"]


async def _is_admin(uid: int) -> bool:
    # Markaziy helperga ko'prik (eski chaqiriqlar buzilmasin)
    return await is_admin_or_glavniy(db, uid)


async def _deny(message: Message, text: str = "⛔ Sizda bu amal uchun ruxsat yo'q."):
    # Markaziy helperga ko'prik
    await deny(message, db, text)


def unit_kb():
    kb = InlineKeyboardBuilder()
    for u in UNITS:
        kb.button(text=u, callback_data=f"unit_{u}")
    kb.adjust(4)
    return kb.as_markup()


# ── Ro'yxat ───────────────────────────────────────────────────────────────────

@router.message(F.text == "📦 Mahsulotlar")
async def show_products(message: Message, state: FSMContext):
    uid = message.from_user.id
    if not await _is_admin(uid):
        return
    if not await has_permission(db, uid, "products_view"):
        await _deny(message)
        return
    # SOTUV REYTINGI bo'yicha tartiblangan — 1-sahifa = 6 ta top-seller.
    # available_only=False — admin qoldig'i tugaganlarni ham ko'rishi kerak.
    prods = await db.top_selling_products(available_only=False)
    if not prods:
        await state.clear()
        await message.answer("📦 Mahsulotlar yo'q.", reply_markup=await get_user_menu(db, uid))
        return
    # 'Yozsa darrov topsin' funksiyasi uchun browsing state'ga o'tamiz —
    # foydalanuvchi shtrix-kod, ID yoki nom yozsa to'g'ridan-to'g'ri qidiriladi.
    await state.set_state(ProductSearchStates.browsing)
    hint = ("<i>Eng ko'p sotiladiganlar yuqorida. ◀️▶️ bilan varaqlang, "
            "🔍 qidiring yoki to'g'ridan-to'g'ri "
            "<b>shtrix-kod / nom / ID</b> yozing.</i>")
    await message.answer(
        f"📦 <b>Mahsulotlar ({len(prods)} ta):</b>\n{hint}",
        reply_markup=products_list_kb(prods, "view", page=0), parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("prodpage_"))
async def products_page_nav(cb: CallbackQuery):
    """Admin mahsulot ro'yxatida ◀️ / ▶️ sahifa navigatsiyasi."""
    if not await _is_admin(cb.from_user.id):
        return
    if not await has_permission(db, cb.from_user.id, "products_view"):
        await cb.answer("⛔ Ruxsat yo'q", show_alert=True)
        return
    arg = cb.data[len("prodpage_"):]
    if arg == "noop":
        await cb.answer()
        return
    try:
        page = int(arg)
    except ValueError:
        await cb.answer()
        return
    prods = await db.top_selling_products(available_only=False)
    try:
        await cb.message.edit_reply_markup(
            reply_markup=products_list_kb(prods, "view", page=page)
        )
    except Exception:
        pass
    await cb.answer()


@router.callback_query(F.data == "prod_search")
async def product_search_start(cb: CallbackQuery, state: FSMContext):
    """Admin mahsulot qidirish — ID / nom / shtrix-kod."""
    if not await _is_admin(cb.from_user.id):
        return
    if not await has_permission(db, cb.from_user.id, "products_view"):
        await cb.answer("⛔ Ruxsat yo'q", show_alert=True)
        return
    await state.set_state(ProductSearchStates.query)
    await cb.message.answer(
        "🔍 <b>Mahsulot qidirish</b>\n\n"
        "Quyidagilardan birini yuboring:\n"
        "• 🆔 Mahsulot <b>ID</b> raqami (masalan: <code>12</code>)\n"
        "• 📝 Mahsulot <b>nomi</b> yoki uning bir qismi\n"
        "• 🔢 <b>Shtrix-kod</b> raqami",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )
    await cb.answer()


@router.message(ProductSearchStates.query)
async def product_search_input(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor.", reply_markup=await get_user_menu(db, message.from_user.id))
        return
    query = (message.text or "").strip()
    if not query:
        await message.answer("⚠️ Qidiruv uchun matn yoki raqam kiriting:")
        return
    results = await db.search_products(query, limit=30)
    # AI analitika uchun qidiruvni jurnalga yozamiz
    await db.log_search(message.from_user.id, query, len(results),
                        source="admin_products")
    if not results:
        await message.answer(
            f"❌ <b>«{query}»</b> bo'yicha mahsulot topilmadi.\n"
            "Qayta urinib ko'ring yoki ❌ Bekor qilish tugmasini bosing.",
            reply_markup=cancel_kb(), parse_mode="HTML"
        )
        return
    # Natija ko'rsatilgach — browsing state'da qolamiz: foydalanuvchi yana yozsa
    # darrov qidirilaveradi (qayta '🔍' bosmasligi uchun).
    await state.set_state(ProductSearchStates.browsing)
    await message.answer(
        f"🔍 <b>«{query}»</b> — {len(results)} ta natija:",
        reply_markup=products_list_kb(results, "view", page=0), parse_mode="HTML"
    )


# ── Yozsa darrov topadi (mahsulotlar bo'limida) ─────────────────────────────

@router.message(ProductSearchStates.browsing, F.photo)
async def products_browse_photo(message: Message, state: FSMContext, bot: Bot):
    """Browsing state'da yuborilgan rasm — shtrix-kod sifatida o'qiladi."""
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
        await message.answer("⚠️ Shtrix-kodni o'qib bo'lmadi. Yaqinroqdan suratga oling.")
        return
    await message.answer(f"📷 Shtrix-kod: <code>{scanned}</code>", parse_mode="HTML")
    results = await db.search_products(scanned, limit=30)
    await db.log_search(message.from_user.id, scanned, len(results),
                        source="admin_products_barcode")
    if not results:
        await message.answer(
            f"❌ <code>{scanned}</code> bo'yicha mahsulot topilmadi.",
            parse_mode="HTML"
        )
        return
    await message.answer(
        f"🔍 <b>{len(results)} ta natija:</b>",
        reply_markup=products_list_kb(results, "view", page=0), parse_mode="HTML"
    )


@router.message(ProductSearchStates.browsing, F.text & ~F.text.in_(RESERVED_MENU))
async def products_browse_text(message: Message, state: FSMContext):
    """Browsing state'da yozilgan matn — shtrix-kod / ID / nom bilan qidiruv."""
    query = (message.text or "").strip()
    if not query:
        return
    results = await db.search_products(query, limit=30)
    await db.log_search(message.from_user.id, query, len(results),
                        source="admin_products_browse")
    if not results:
        await message.answer(
            f"❌ <b>«{query}»</b> bo'yicha mahsulot topilmadi.",
            parse_mode="HTML"
        )
        return
    await message.answer(
        f"🔍 <b>«{query}»</b> — {len(results)} ta natija:",
        reply_markup=products_list_kb(results, "view", page=0), parse_mode="HTML"
    )


# ── Qo'shish ──────────────────────────────────────────────────────────────────

@router.message(F.text == "➕ Mahsulot qo'shish")
async def add_product_start(message: Message, state: FSMContext):
    uid = message.from_user.id
    if not await _is_admin(uid):
        return
    if not await has_permission(db, uid, "products_add"):
        await _deny(message)
        return
    await message.answer("📝 Mahsulot nomini kiriting:", reply_markup=cancel_kb())
    await state.set_state(AddProductStates.name)


@router.message(AddProductStates.name)
async def ap_name(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor.", reply_markup=admin_menu())
        return
    await state.update_data(name=message.text.strip())
    await message.answer("📝 Tavsifini kiriting (yoki — yozing o'tkazish uchun):")
    await state.set_state(AddProductStates.description)


@router.message(AddProductStates.description)
async def ap_desc(message: Message, state: FSMContext):
    desc = "" if message.text.strip() in ["-", "—"] else message.text.strip()
    await state.update_data(description=desc)
    await message.answer(
        "📦 O'lchov birligini tanlang:",
        reply_markup=unit_kb()
    )
    await state.set_state(AddProductStates.unit)


def _parse_usd(txt: str) -> float:
    """1.25 / 1,25 / $1.25 → 1.25. Bo'sh / — bo'lsa ValueError."""
    s = (txt or "").strip().lstrip("$").replace(" ", "").replace(",", ".")
    if not s:
        raise ValueError
    return float(s)


@router.callback_query(AddProductStates.unit, F.data.startswith("unit_"))
async def ap_unit(cb: CallbackQuery, state: FSMContext):
    unit = cb.data.split("_", 1)[1]
    await state.update_data(unit=unit)
    rate = db.get_usd_rate()
    await cb.message.answer(
        f"💰 Sotish narxini <b>USD</b> da kiriting (sentlar bilan, {unit} uchun).\n"
        f"Masalan: <code>1.25</code> yoki <code>0.85</code>\n"
        f"💱 Joriy kurs: <b>1$ = {rate:,.0f} so'm</b> (kassa avtomatik so'mga aylantiradi).",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )
    await state.set_state(AddProductStates.sell_price)
    await cb.answer()


@router.message(AddProductStates.sell_price)
async def ap_sell(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor.", reply_markup=admin_menu())
        return
    try:
        usd = _parse_usd(message.text)
        if usd < 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ USD da raqam kiriting (masalan: 1.25):")
        return
    await state.update_data(sell_price_usd=usd)
    data = await state.get_data()
    unit = data.get("unit", "dona")
    rate = db.get_usd_rate()
    await message.answer(
        f"✅ {fmt_usd(usd)}/{unit}  (≈ {fmt_sum(usd_to_sum(usd, rate))})\n\n"
        f"📦 <b>Optom narxini USD da kiriting</b> (sentlar bilan, {unit} uchun):\n"
        f"💡 Optom mijozlar uchun. Optomda sotmasangiz — <b>0</b> yoki <b>—</b> yozing.",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )
    await state.set_state(AddProductStates.wholesale_price)


@router.message(AddProductStates.wholesale_price)
async def ap_wholesale(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor.", reply_markup=admin_menu())
        return
    txt = (message.text or "").strip()
    if txt in ("-", "—", "0", "0.0", "0.00"):
        whs = 0.0
    else:
        try:
            whs = _parse_usd(txt)
        except ValueError:
            await message.answer("⚠️ USD da raqam kiriting (yoki — / 0):")
            return
    await state.update_data(wholesale_price_usd=whs)
    await message.answer(
        "🏷️ Tannarxini <b>USD</b> da kiriting (sentlar bilan, faqat adminga ko'rinadi):\n"
        "Masalan: <code>0.95</code>",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )
    await state.set_state(AddProductStates.cost_price)


@router.message(AddProductStates.cost_price)
async def ap_cost(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor.", reply_markup=admin_menu())
        return
    try:
        usd = _parse_usd(message.text)
        if usd < 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ USD da raqam kiriting (masalan: 0.95):")
        return
    await state.update_data(cost_price_usd=usd)
    data = await state.get_data()
    unit = data.get("unit", "dona")
    await message.answer(f"📦 Boshlang'ich miqdorini kiriting ({unit}):\n(Masalan: 10 yoki 2.5)")
    await state.set_state(AddProductStates.qty)


@router.message(AddProductStates.qty)
async def ap_qty(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor.", reply_markup=admin_menu())
        return
    try:
        val = float(message.text.strip().replace(",", "."))
        if val < 0:
            raise ValueError
        await state.update_data(qty=val)
        await message.answer("🖼️ Rasmini yuboring (yoki — o'tkazish uchun):")
        await state.set_state(AddProductStates.image)
    except ValueError:
        await message.answer("⚠️ Musbat son kiriting (masalan: 10 yoki 2.5):")


@router.message(AddProductStates.image)
async def ap_image(message: Message, state: FSMContext):
    file_id = ""
    if message.photo:
        file_id = message.photo[-1].file_id
    await state.update_data(image_file_id=file_id)
    # Shtrix-kod funksiyasi o'chirilgan bo'lsa — bu bosqichni butunlay o'tkazib,
    # kategoriya/yetkazib beruvchi tanlashga o'tamiz (kodsiz).
    if not db.is_barcode_enabled():
        await state.update_data(barcode="")
        await _maybe_ask_category(message, state)
        return
    await message.answer(
        "🔖 <b>Shtrix-kod (barcode)</b>\n\n"
        "Shtrix-kod qiymatini quyidagicha kiriting:\n"
        "• 📷 Shtrix-kod <b>rasmini</b> yuboring — kod avtomatik o'qib olinadi\n"
        "• ⌨️ Yoki kod raqamini <b>matn</b> qilib yozing (masalan: <code>4780000000017</code>)\n"
        "• ⏭️ O'tkazib yuborish uchun <b>—</b> yoki <b>-</b> yozing",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )
    await state.set_state(AddProductStates.barcode)


# ─── Mahsulot oqimida kategoriya / yetkazib beruvchi tanlash ───────────────

async def _maybe_ask_category(target, state: FSMContext):
    """Kategoriyalar yoqilgan va mavjud bo'lsa — tanlash inline klaviaturasi."""
    if not db.is_categories_enabled():
        await state.update_data(category_id=0)
        await _maybe_ask_supplier(target, state)
        return
    cats = await db.get_all_categories()
    if not cats:
        await state.update_data(category_id=0)
        await _maybe_ask_supplier(target, state)
        return
    await state.set_state(AddProductStates.category)
    await target.answer(
        "🗂 <b>Kategoriyani tanlang</b>:\n"
        "<i>Agar kerak bo'lmasa — «O'tkazib yuborish».</i>",
        reply_markup=category_pick_kb(cats, "apcat",
                                       allow_skip=True, allow_none=True),
        parse_mode="HTML"
    )


async def _maybe_ask_supplier(target, state: FSMContext):
    """Yetkazib beruvchilar yoqilgan va mavjud bo'lsa — tanlash inline klaviaturasi."""
    if not db.is_suppliers_enabled():
        await state.update_data(supplier_id=0)
        await _finalize_after_pickers(target, state)
        return
    sups = await db.get_all_suppliers()
    if not sups:
        await state.update_data(supplier_id=0)
        await _finalize_after_pickers(target, state)
        return
    await state.set_state(AddProductStates.supplier)
    await target.answer(
        "🚚 <b>Yetkazib beruvchini tanlang</b>:\n"
        "<i>Bog'lansa — keyinroq «🚚 Yetkazib beruvchilar» bo'limidan zakaz/prixod qulay.</i>",
        reply_markup=supplier_pick_kb(sups, "apsup",
                                       allow_skip=True, allow_none=True),
        parse_mode="HTML"
    )


async def _finalize_after_pickers(target, state: FSMContext):
    """Pickerlar tugagach — mahsulotni saqlaydi (state.data dan barcha qiymatlar)."""
    data = await state.get_data()
    barcode = data.get("barcode", "") or ""
    msg = target.message if hasattr(target, "message") and not hasattr(target, "text") else target
    await _finalize_add_product(msg, state, barcode=barcode)


@router.callback_query(AddProductStates.category, F.data.startswith("apcat_"))
async def ap_pick_category(cb: CallbackQuery, state: FSMContext):
    arg = cb.data[len("apcat_"):]
    if arg == "skip" or arg == "0":
        await state.update_data(category_id=0)
    else:
        try:
            await state.update_data(category_id=int(arg))
        except ValueError:
            await state.update_data(category_id=0)
    await cb.answer("✅")
    # Endi yetkazib beruvchi bosqichi (yoki yakunlash)
    await _maybe_ask_supplier(cb.message, state)


@router.callback_query(AddProductStates.supplier, F.data.startswith("apsup_"))
async def ap_pick_supplier(cb: CallbackQuery, state: FSMContext):
    arg = cb.data[len("apsup_"):]
    if arg == "skip" or arg == "0":
        await state.update_data(supplier_id=0)
    else:
        try:
            await state.update_data(supplier_id=int(arg))
        except ValueError:
            await state.update_data(supplier_id=0)
    await cb.answer("✅")
    await _finalize_after_pickers(cb.message, state)


async def _finalize_add_product(message: Message, state: FSMContext, barcode: str = ""):
    data = await state.get_data()
    file_id = data.get("image_file_id", "") or ""
    unit = data.get("unit", "dona")
    sell_usd = float(data.get("sell_price_usd", 0))
    whs_usd = float(data.get("wholesale_price_usd", 0))
    cost_usd = float(data.get("cost_price_usd", 0))
    category_id = int(data.get("category_id", 0) or 0)
    supplier_id = int(data.get("supplier_id", 0) or 0)
    pid = await db.add_product(
        name=data["name"], description=data.get("description", ""),
        sell_price_usd=sell_usd, cost_price_usd=cost_usd,
        qty=data["qty"], image_file_id=file_id, unit=unit,
        wholesale_price_usd=whs_usd,
        barcode=barcode,
        category_id=category_id, supplier_id=supplier_id,
    )
    await state.clear()
    rate = db.get_usd_rate()
    whs_line = (
        f"\n📦 Optom: <b>{fmt_usd(whs_usd)}/{unit}</b> (≈ {fmt_sum(usd_to_sum(whs_usd, rate))})"
        if whs_usd and whs_usd > 0 else ""
    )
    bc_line = f"\n🔖 Shtrix-kod: <code>{barcode}</code>" if barcode else ""
    cat_name = ""
    sup_name = ""
    if category_id:
        c = await db.get_category(category_id)
        if c:
            cat_name = c["name"]
    if supplier_id:
        s = await db.get_supplier(supplier_id)
        if s:
            sup_name = s["name"]
    cat_line = f"\n🗂 Kategoriya: <b>{cat_name}</b>" if cat_name else ""
    sup_line = f"\n🚚 Yetkazib beruvchi: <b>{sup_name}</b>" if sup_name else ""
    channel_line = (
        "\n📢 Kanalga rasm + 'Sotib olish' tugmasi bilan yuborildi."
        if file_id else
        "\n⚠️ Rasm yuborilmadi — kanalga post qo'yilmadi."
    )
    await message.answer(
        f"✅ Mahsulot qo'shildi!\n\n"
        f"📦 <b>{data['name']}</b>\n"
        f"💰 Donada: <b>{fmt_usd(sell_usd)}/{unit}</b> (≈ {fmt_sum(usd_to_sum(sell_usd, rate))})"
        f"{whs_line}\n"
        f"📦 Qoldi: {data['qty']:g} {unit}"
        f"{bc_line}"
        f"{cat_line}{sup_line}\n"
        f"💱 Joriy kurs: 1$ = {rate:,.0f} so'm\n"
        f"🆔 ID: {pid}"
        f"{channel_line}",
        reply_markup=await get_user_menu(db, message.from_user.id), parse_mode="HTML"
    )


@router.message(AddProductStates.barcode)
async def ap_barcode(message: Message, state: FSMContext, bot: Bot):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor.", reply_markup=admin_menu())
        return
    barcode = ""
    if message.photo:
        # Shtrix-kod rasmini yuklab olib, kodni o'qib olish
        try:
            f = await bot.get_file(message.photo[-1].file_id)
            buf = await bot.download_file(f.file_path)
            image_bytes = buf.read() if hasattr(buf, "read") else bytes(buf)
            barcode = decode_barcode(image_bytes) or ""
        except Exception:
            barcode = ""
        if not barcode:
            await message.answer(
                "⚠️ Bu rasmdan shtrix-kodni o'qib bo'lmadi.\n"
                "Iltimos, kodni yaqindan, ravshan suratga oling yoki raqamini qo'lda yozing:",
                reply_markup=cancel_kb()
            )
            return
        await message.answer(
            f"✅ Shtrix-kod o'qildi: <code>{barcode}</code>",
            parse_mode="HTML"
        )
    else:
        txt = (message.text or "").strip()
        if txt in ("-", "—", ""):
            barcode = ""
        else:
            barcode = txt
    # Bir xil shtrix-kod boshqa mahsulotda bormi tekshiramiz
    if barcode:
        existing = await db.get_product_by_barcode(barcode)
        if existing:
            await message.answer(
                f"⚠️ Bu shtrix-kod (<code>{barcode}</code>) allaqachon "
                f"<b>{existing['name']}</b> (ID {existing['id']}) ga biriktirilgan.\n"
                f"Boshqa kod kiriting yoki <b>—</b> yozing (kodsiz saqlash):",
                reply_markup=cancel_kb(), parse_mode="HTML"
            )
            return
    # Shtrix-kodni state.data ga saqlab, kategoriya/yetkazib beruvchi tanlashga o'tamiz.
    await state.update_data(barcode=barcode)
    await _maybe_ask_category(message, state)


# ── Ko'rish ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("prod_view_"))
async def view_product(cb: CallbackQuery):
    if not await _is_admin(cb.from_user.id):
        return
    pid = int(cb.data.split("_")[2])
    p = await db.get_product(pid)
    if not p:
        await cb.answer("Topilmadi!")
        return
    unit = p.get("unit", "dona")
    rate = db.get_usd_rate()
    sell_usd = float(p.get("sell_price_usd", 0) or 0)
    sell_sum = float(p.get("sell_price", 0) or 0)
    cost_usd = float(p.get("cost_price_usd", 0) or 0)
    cost_sum = float(p.get("cost_price", 0) or 0)
    whs_usd = float(p.get("wholesale_price_usd", 0) or 0)
    whs_sum = float(p.get("wholesale_price", 0) or 0)
    if whs_usd > 0 or whs_sum > 0:
        if whs_usd > 0:
            whs_line = f"\n📦 Optom: <b>{fmt_usd(whs_usd)}/{unit}</b> (≈ {fmt_sum(whs_sum)})"
        else:
            whs_line = f"\n📦 Optom: <b>{fmt_sum(whs_sum)}/{unit}</b>"
    else:
        whs_line = "\n📦 Optom: <i>belgilanmagan</i>"
    if sell_usd > 0:
        sell_line = f"💰 Donada: <b>{fmt_usd(sell_usd)}/{unit}</b> (≈ {fmt_sum(sell_sum)})"
    else:
        sell_line = f"💰 Donada: <b>{fmt_sum(sell_sum)}/{unit}</b>"
    if cost_usd > 0:
        cost_line = f"🏷️ Tannarx: <b>{fmt_usd(cost_usd)}</b> (≈ {fmt_sum(cost_sum)})"
    else:
        cost_line = f"🏷️ Tannarx: <b>{fmt_sum(cost_sum)}</b>"
    bc = (p.get("barcode") or "").strip()
    bc_line = f"\n🔖 Shtrix-kod: <code>{bc}</code>" if bc else "\n🔖 Shtrix-kod: <i>belgilanmagan</i>"
    # Kategoriya / Yetkazib beruvchi — funksiya yoqilgan bo'lsa ko'rsatamiz
    cat_line = ""
    sup_line = ""
    if db.is_categories_enabled():
        cid = int(p.get("category_id", 0) or 0)
        if cid:
            c = await db.get_category(cid)
            cat_line = f"\n🗂 Kategoriya: <b>{c['name'] if c else '—'}</b>"
        else:
            cat_line = "\n🗂 Kategoriya: <i>belgilanmagan</i>"
    if db.is_suppliers_enabled():
        sid = int(p.get("supplier_id", 0) or 0)
        if sid:
            s = await db.get_supplier(sid)
            sup_line = f"\n🚚 Yetkazib beruvchi: <b>{s['name'] if s else '—'}</b>"
        else:
            sup_line = "\n🚚 Yetkazib beruvchi: <i>belgilanmagan</i>"
    text = (
        f"📦 <b>{p['name']}</b>\n"
        f"📝 {p.get('description') or '—'}\n\n"
        f"{sell_line}"
        f"{whs_line}\n"
        f"{cost_line}\n"
        f"📦 Qoldi: <b>{p.get('qty', 0):g} {unit}</b>"
        f"{bc_line}"
        f"{cat_line}{sup_line}\n"
        f"💱 Joriy kurs: 1$ = {rate:,.0f} so'm\n"
        f"🆔 ID: {pid}"
    )
    kb = product_actions_kb(pid)
    if p.get("image_file_id"):
        try:
            await cb.message.answer_photo(p["image_file_id"], caption=text, reply_markup=kb, parse_mode="HTML")
            await cb.answer()
            return
        except Exception:
            pass
    await cb.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await cb.answer()


# ── Tahrirlash ────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("prod_edit_"))
async def edit_menu(cb: CallbackQuery):
    if not await _is_admin(cb.from_user.id):
        return
    if not await has_permission(db, cb.from_user.id, "products_edit"):
        await cb.answer("⛔ Ruxsat yo'q", show_alert=True)
        return
    pid = int(cb.data.split("_")[2])
    await cb.message.answer("Qaysi maydonni o'zgartirish?", reply_markup=product_edit_fields_kb(pid))
    await cb.answer()


@router.callback_query(F.data.startswith("pedit_"))
async def edit_field_start(cb: CallbackQuery, state: FSMContext):
    if not await _is_admin(cb.from_user.id):
        return
    if not await has_permission(db, cb.from_user.id, "products_edit"):
        await cb.answer("⛔ Ruxsat yo'q", show_alert=True)
        return
    # DIQQAT: maydon nomida "_" bo'lishi mumkin (sell_price, wholesale_price,
    # cost_price). Shuning uchun faqat birinchi 2 ta "_" bo'yicha bo'lamiz —
    # aks holda "pedit_3_sell_price" → field "sell" bo'lib qolardi (bug).
    parts = cb.data.split("_", 2)
    pid = int(parts[1]); field = parts[2]
    # Eski "sell_price" / "wholesale_price" / "cost_price" tugmalari
    # endi USD ustunlarni tahrirlaydi.
    field_map = {
        "sell_price":      "sell_price_usd",
        "wholesale_price": "wholesale_price_usd",
        "cost_price":      "cost_price_usd",
    }
    field = field_map.get(field, field)
    await state.update_data(edit_pid=pid, edit_field=field)

    if field == "unit":
        await cb.message.answer("📦 Yangi o'lchov birligini tanlang:", reply_markup=unit_kb())
        await state.set_state(EditProductStates.waiting)
        await cb.answer()
        return

    if field == "category":
        if not db.is_categories_enabled():
            await cb.answer("Kategoriyalar o'chirilgan.", show_alert=True)
            return
        cats = await db.get_all_categories()
        if not cats:
            await cb.answer("Hali kategoriya yo'q. Avval 🗂 Kategoriyalar bo'limidan qo'shing.",
                            show_alert=True)
            return
        p = await db.get_product_any(pid)
        cur = int((p or {}).get("category_id", 0) or 0)
        await cb.message.answer(
            "🗂 Yangi kategoriyani tanlang:",
            reply_markup=category_pick_kb(cats, f"pcedit_{pid}",
                                          allow_none=True, current=cur)
        )
        await cb.answer()
        return

    if field == "supplier":
        if not db.is_suppliers_enabled():
            await cb.answer("Yetkazib beruvchilar o'chirilgan.", show_alert=True)
            return
        sups = await db.get_all_suppliers()
        if not sups:
            await cb.answer("Hali yetkazib beruvchi yo'q. Avval 🚚 bo'limidan qo'shing.",
                            show_alert=True)
            return
        p = await db.get_product_any(pid)
        cur = int((p or {}).get("supplier_id", 0) or 0)
        await cb.message.answer(
            "🚚 Yangi yetkazib beruvchini tanlang:",
            reply_markup=supplier_pick_kb(sups, f"psedit_{pid}",
                                          allow_none=True, current=cur)
        )
        await cb.answer()
        return

    rate = db.get_usd_rate()
    if field == "barcode":
        await cb.message.answer(
            "🔖 <b>Yangi shtrix-kod</b>\n\n"
            "• 📷 Shtrix-kod <b>rasmini</b> yuboring — kod avtomatik o'qib olinadi\n"
            "• ⌨️ Yoki kod raqamini <b>matn</b> qilib yozing\n"
            "• 🗑️ Olib tashlash uchun <b>—</b> yoki <b>-</b> yozing",
            reply_markup=cancel_kb(), parse_mode="HTML"
        )
        await state.set_state(EditProductStates.waiting)
        await cb.answer()
        return
    labels = {
        "name": "yangi nom", "description": "yangi tavsif",
        "sell_price_usd":      f"yangi donada narxi (USD, sentlar bilan).\n💱 1$ = {rate:,.0f} so'm",
        "wholesale_price_usd": f"yangi optom narxi (USD, 0 — optomda sotmaslik).\n💱 1$ = {rate:,.0f} so'm",
        "cost_price_usd":      f"yangi tannarx (USD, sentlar bilan).\n💱 1$ = {rate:,.0f} so'm",
        "qty": "yangi miqdor (kasr bo'lishi mumkin, masalan: 2.5)", "image": "yangi rasm"
    }
    await cb.message.answer(f"✏️ {labels.get(field, field)} kiriting:",
                             reply_markup=cancel_kb(), parse_mode="HTML")
    await state.set_state(EditProductStates.waiting)
    await cb.answer()


@router.callback_query(EditProductStates.waiting, F.data.startswith("unit_"))
async def edit_unit_done(cb: CallbackQuery, state: FSMContext):
    unit = cb.data.split("_", 1)[1]
    data = await state.get_data()
    pid = data["edit_pid"]
    await db.update_product(pid, unit=unit)
    await state.clear()
    p = await db.get_product(pid)
    await cb.message.answer(
        f"✅ O'lchov birligi yangilandi! <b>{p['name']}</b> → {unit}",
        reply_markup=admin_menu(), parse_mode="HTML"
    )
    await cb.answer()


@router.message(EditProductStates.waiting)
async def edit_field_done(message: Message, state: FSMContext, bot: Bot):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor.", reply_markup=admin_menu())
        return
    data = await state.get_data()
    pid = data["edit_pid"]; field = data["edit_field"]
    if field == "image":
        if not message.photo:
            await message.answer("⚠️ Rasm yuboring!")
            return
        await db.update_product(pid, image_file_id=message.photo[-1].file_id)
    elif field == "barcode":
        barcode = ""
        if message.photo:
            try:
                f = await bot.get_file(message.photo[-1].file_id)
                buf = await bot.download_file(f.file_path)
                image_bytes = buf.read() if hasattr(buf, "read") else bytes(buf)
                barcode = decode_barcode(image_bytes) or ""
            except Exception:
                barcode = ""
            if not barcode:
                await message.answer(
                    "⚠️ Bu rasmdan shtrix-kodni o'qib bo'lmadi.\n"
                    "Iltimos, kodni yaqindan, ravshan suratga oling yoki raqamini qo'lda yozing:",
                    reply_markup=cancel_kb()
                )
                return
            await message.answer(
                f"✅ Shtrix-kod o'qildi: <code>{barcode}</code>",
                parse_mode="HTML"
            )
        else:
            txt = (message.text or "").strip()
            barcode = "" if txt in ("-", "—") else txt
        # Bir xil kod boshqa mahsulotda bo'lmasligi kerak
        if barcode:
            existing = await db.get_product_by_barcode(barcode)
            if existing and existing.get("id") != pid:
                await message.answer(
                    f"⚠️ Bu shtrix-kod (<code>{barcode}</code>) allaqachon "
                    f"<b>{existing['name']}</b> (ID {existing['id']}) ga biriktirilgan.",
                    parse_mode="HTML"
                )
                return
        await db.update_product(pid, barcode=barcode)
    elif field in ("sell_price_usd", "cost_price_usd", "wholesale_price_usd"):
        txt = message.text.strip()
        if field == "wholesale_price_usd" and txt in ("-", "—"):
            txt = "0"
        try:
            val = _parse_usd(txt)
            if val < 0:
                raise ValueError
            await db.update_product(pid, **{field: val})
        except ValueError:
            await message.answer("⚠️ USD da raqam kiriting (masalan: 1.25)!")
            return
    elif field == "qty":
        try:
            val = float(message.text.strip().replace(",", "."))
            if val < 0:
                raise ValueError
            await db.update_product(pid, qty=val)
        except ValueError:
            await message.answer("⚠️ Musbat son kiriting (masalan: 10 yoki 2.5)!")
            return
    else:
        await db.update_product(pid, **{field: message.text.strip()})
    await state.clear()
    p = await db.get_product(pid)
    await message.answer(
        f"✅ Yangilandi! Kanal ham o'zgartirildi.\n<b>{p['name']}</b>",
        reply_markup=admin_menu(), parse_mode="HTML"
    )


# ── Tovar qo'shish ────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("prod_qty_"))
async def qty_add_start(cb: CallbackQuery, state: FSMContext):
    if not await _is_admin(cb.from_user.id):
        return
    if not await has_permission(db, cb.from_user.id, "products_qty"):
        await cb.answer("⛔ Prixod uchun ruxsat yo'q", show_alert=True)
        return
    pid = int(cb.data.split("_")[2])
    p = await db.get_product(pid)
    unit = p.get("unit", "dona")
    await state.update_data(qty_pid=pid)
    await cb.message.answer(
        f"📦 <b>{p['name']}</b> — hozir {p.get('qty', 0):g} {unit}\n\nNechta qo'shish? ({unit})\n(Masalan: 5 yoki 2.5)",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )
    await state.set_state(QtyAddStates.enter)
    await cb.answer()


@router.message(QtyAddStates.enter)
async def qty_add_done(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor.", reply_markup=admin_menu())
        return
    try:
        delta = float(message.text.strip().replace(",", "."))
    except ValueError:
        await message.answer("⚠️ Son kiriting (masalan: 5 yoki 2.5):")
        return
    data = await state.get_data()
    await db.change_qty(data["qty_pid"], delta)
    await state.clear()
    p = await db.get_product(data["qty_pid"])
    unit = p.get("unit", "dona")
    await message.answer(
        f"✅ Yangilandi! <b>{p['name']}</b>: {p.get('qty', 0):g} {unit}",
        reply_markup=admin_menu(), parse_mode="HTML"
    )


# ── O'chirish ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("prod_del_"))
async def del_confirm(cb: CallbackQuery):
    if not await _is_admin(cb.from_user.id):
        return
    if not await has_permission(db, cb.from_user.id, "products_del"):
        await cb.answer("⛔ O'chirish uchun ruxsat yo'q", show_alert=True)
        return
    pid = int(cb.data.split("_")[2])
    p = await db.get_product(pid)
    if not p:
        await cb.answer("Topilmadi!")
        return
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Ha, o'chir", callback_data=f"prod_delok_{pid}")
    kb.button(text="❌ Yo'q",       callback_data=f"prod_view_{pid}")
    kb.adjust(2)
    await cb.message.answer(
        f"⚠️ <b>{p['name']}</b> ni o'chirasizmi?",
        reply_markup=kb.as_markup(), parse_mode="HTML"
    )
    await cb.answer()


@router.callback_query(F.data.startswith("prod_delok_"))
async def del_ok(cb: CallbackQuery):
    if not await _is_admin(cb.from_user.id):
        return
    if not await has_permission(db, cb.from_user.id, "products_del"):
        await cb.answer("⛔ Ruxsat yo'q", show_alert=True)
        return
    pid = int(cb.data.split("_")[2])
    await db.deactivate_product(pid)
    await cb.answer("🗑️ O'chirildi!")
    await cb.message.edit_text("🗑️ Mahsulot o'chirildi.")


@router.callback_query(F.data == "prod_back")
async def prod_back(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(ProductSearchStates.browsing)
    prods = await db.top_selling_products(available_only=False)
    await cb.message.answer(
        "📦 Mahsulotlar:",
        reply_markup=products_list_kb(prods, "view", page=0)
    )


# ─── Mahsulot edit: kategoriya / yetkazib beruvchi callback ────────────────

@router.callback_query(F.data.startswith("pcedit_"))
async def pcedit_apply(cb: CallbackQuery):
    if not await _is_admin(cb.from_user.id):
        return
    if not await has_permission(db, cb.from_user.id, "products_edit"):
        await cb.answer("⛔ Ruxsat yo'q", show_alert=True)
        return
    # pcedit_<pid>_<cid_or_0>  yoki  pcedit_<pid>_skip
    rest = cb.data[len("pcedit_"):]
    pid_str, _, arg = rest.partition("_")
    try:
        pid = int(pid_str)
    except ValueError:
        await cb.answer()
        return
    if arg == "skip":
        await cb.answer()
        return
    try:
        cid = int(arg)
    except ValueError:
        cid = 0
    await db.set_product_category(pid, cid)
    c = await db.get_category(cid) if cid else None
    await cb.answer(f"✅ {c['name'] if c else 'Kategoriyasiz'}")
    await cb.message.answer(
        f"✅ Kategoriya yangilandi: <b>{c['name'] if c else 'Kategoriyasiz'}</b>",
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("psedit_"))
async def psedit_apply(cb: CallbackQuery):
    if not await _is_admin(cb.from_user.id):
        return
    if not await has_permission(db, cb.from_user.id, "products_edit"):
        await cb.answer("⛔ Ruxsat yo'q", show_alert=True)
        return
    rest = cb.data[len("psedit_"):]
    pid_str, _, arg = rest.partition("_")
    try:
        pid = int(pid_str)
    except ValueError:
        await cb.answer()
        return
    if arg == "skip":
        await cb.answer()
        return
    try:
        sid = int(arg)
    except ValueError:
        sid = 0
    await db.set_product_supplier(pid, sid)
    s = await db.get_supplier(sid) if sid else None
    await cb.answer(f"✅ {s['name'] if s else 'Belgilanmagan'}")
    await cb.message.answer(
        f"✅ Yetkazib beruvchi yangilandi: <b>{s['name'] if s else 'Belgilanmagan'}</b>",
        parse_mode="HTML"
    )


# ─── Kategoriya bo'yicha filtr (mahsulotlar bo'limi) ────────────────────────

@router.callback_query(F.data == "pcatf_open")
async def pcatf_open(cb: CallbackQuery):
    if not await _is_admin(cb.from_user.id):
        return
    if not (db.is_categories_enabled() and db.is_cat_filter_enabled()):
        await cb.answer("Filtr o'chirilgan.", show_alert=True)
        return
    cats = await db.get_all_categories()
    await cb.message.answer(
        "🔻 <b>Kategoriya bo'yicha filtr</b>:",
        reply_markup=category_filter_kb(cats, "pcatf"), parse_mode="HTML"
    )
    await cb.answer()


@router.callback_query(F.data == "pcatf_back")
async def pcatf_back(cb: CallbackQuery, state: FSMContext):
    if not await _is_admin(cb.from_user.id):
        return
    await state.set_state(ProductSearchStates.browsing)
    prods = await db.top_selling_products(available_only=False)
    await cb.message.answer(
        f"📦 <b>Mahsulotlar ({len(prods)} ta):</b>",
        reply_markup=products_list_kb(prods, "view", page=0), parse_mode="HTML"
    )
    await cb.answer()


@router.callback_query(F.data == "pcatf_all")
async def pcatf_all(cb: CallbackQuery, state: FSMContext):
    await pcatf_back(cb, state)


@router.callback_query(F.data.startswith("pcatf_"))
async def pcatf_pick(cb: CallbackQuery, state: FSMContext):
    if not await _is_admin(cb.from_user.id):
        return
    arg = cb.data[len("pcatf_"):]
    # 'all', 'back' allaqachon yuqorida ushlangan — bu yerda raqam (0 yoki cid)
    try:
        cid = int(arg)
    except ValueError:
        await cb.answer()
        return
    if cid == 0:
        # Kategoriyasiz mahsulotlar
        all_prods = await db.get_all_products()
        prods = [p for p in all_prods if not (p.get("category_id") or 0)]
        title = "🚫 Kategoriyasiz"
    else:
        prods = await db.get_products_by_category(cid)
        c = await db.get_category(cid)
        title = f"🗂 {c['name'] if c else cid}"
    if not prods:
        await cb.answer("Bu filtrda mahsulot yo'q.", show_alert=True)
        return
    await state.set_state(ProductSearchStates.browsing)
    await cb.message.answer(
        f"{title} — {len(prods)} ta mahsulot:",
        reply_markup=simple_products_kb(prods, "view", back_cb="pcatf_back"),
        parse_mode="HTML"
    )
    await cb.answer()
