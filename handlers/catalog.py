"""Kategoriyalar va yetkazib beruvchilar bo'limi.

  • 🗂 Kategoriyalar — qo'shish, tahrirlash, o'chirish, mahsulotlarini ko'rish
  • 🚚 Yetkazib beruvchilar — qo'shish, tahrirlash, o'chirish, mahsulotlari
  • 📋 Zakaz ro'yxati tuzish — yetkazib beruvchidan keladigan tovarlarni
       miqdori bilan ro'yxatga yig'ib, tayyor matn ko'rinishida olish
  • ⚡ Tezkor prixod — yetkazib beruvchidan bir necha mahsulotga darrov prixod
  • ⚡ Past qoldiq tezkor to'ldirish (qrs_*) — past qoldiq ro'yxatidan prixod
"""

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from bot.keyboards import (
    categories_list_kb, category_actions_kb, suppliers_list_kb,
    supplier_actions_kb, supplier_edit_fields_kb, simple_products_kb,
    qty_builder_kb, qty_builder_item_kb, cancel_kb,
)
from bot.states import (
    AddCategoryStates, EditCategoryStates, AddSupplierStates, EditSupplierStates,
    SupplierOrderStates, QuickPrixodStates, QuickRestockStates,
)
from bot.permissions import (
    has_permission, get_user_menu, is_admin_or_glavniy, deny,
)
from database.channel_db import db

router = Router()

CANCEL = "❌ Bekor qilish"
STEP = 10   # zakaz ro'yxati / prixod uchun ➕/➖ qadami


# ─── Umumiy yordamchilar ─────────────────────────────────────────────────────

async def _menu(uid: int):
    return await get_user_menu(db, uid)


async def _send(target, text, kb=None):
    """Matnli ekranni ko'rsatadi (CallbackQuery — edit, Message — answer)."""
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await target.message.answer(text, reply_markup=kb, parse_mode="HTML")
        await target.answer()
    else:
        await target.answer(text, reply_markup=kb, parse_mode="HTML")


async def _guard(target, perm: str, feat: str) -> bool:
    """Admin + funksiya yoqilgan + ruxsat — uchchalasini tekshiradi."""
    uid = target.from_user.id
    if not await is_admin_or_glavniy(db, uid):
        return False
    feat_on = {
        "categories": db.is_categories_enabled,
        "suppliers":  db.is_suppliers_enabled,
    }[feat]()
    if not feat_on:
        msg = "ℹ️ Bu funksiya o'chirilgan."
        if isinstance(target, CallbackQuery):
            await target.answer(msg, show_alert=True)
        else:
            await target.answer(msg, reply_markup=await _menu(uid))
        return False
    if not await has_permission(db, uid, perm):
        await deny(target, db)
        return False
    return True


# ═══════════════════════════════════════════════════════════════════════════
#  KATEGORIYALAR
# ═══════════════════════════════════════════════════════════════════════════

async def _show_categories(target):
    cats = await db.get_all_categories()
    total_prod = sum(c.get("product_count", 0) for c in cats)
    text = (
        f"🗂 <b>Kategoriyalar — {len(cats)} ta</b>\n"
        f"📦 Biriktirilgan mahsulotlar: {total_prod} ta\n\n"
        "Kategoriya ustiga bosib ko'ring yoki yangisini qo'shing."
        if cats else
        "🗂 <b>Kategoriyalar</b>\n\nHali kategoriya yo'q. Yangisini qo'shing 👇"
    )
    await _send(target, text, categories_list_kb(cats))


@router.message(F.text == "🗂 Kategoriyalar")
async def categories_menu(message: Message, state: FSMContext):
    if not await _guard(message, "categories", "categories"):
        return
    await state.clear()
    await _show_categories(message)


@router.callback_query(F.data == "cat_back")
async def cat_back(cb: CallbackQuery, state: FSMContext):
    if not await _guard(cb, "categories", "categories"):
        return
    await state.clear()
    await _show_categories(cb)


@router.callback_query(F.data == "cat_add")
async def cat_add_start(cb: CallbackQuery, state: FSMContext):
    if not await _guard(cb, "categories", "categories"):
        return
    await state.set_state(AddCategoryStates.name)
    await cb.message.answer(
        "🗂 <b>Yangi kategoriya</b>\n\nKategoriya nomini kiriting:\n"
        "(masalan: <code>Ichimliklar</code>, <code>Kanstovar</code>)",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )
    await cb.answer()


@router.message(AddCategoryStates.name)
async def cat_add_name(message: Message, state: FSMContext):
    if message.text == CANCEL:
        await state.clear()
        await message.answer("Bekor.", reply_markup=await _menu(message.from_user.id))
        await _show_categories(message)
        return
    name = (message.text or "").strip()
    if not name:
        await message.answer("⚠️ Bo'sh bo'lmasin. Kategoriya nomini kiriting:")
        return
    if await db.category_exists(name):
        await message.answer("⚠️ Bunday kategoriya allaqachon mavjud. Boshqa nom kiriting:")
        return
    await db.add_category(name)
    await state.clear()
    await message.answer(
        f"✅ <b>{name}</b> kategoriyasi qo'shildi!",
        reply_markup=await _menu(message.from_user.id), parse_mode="HTML"
    )
    await _show_categories(message)


async def _show_category_card(target, cid: int):
    c = await db.get_category(cid)
    if not c:
        if isinstance(target, CallbackQuery):
            await target.answer("Topilmadi.", show_alert=True)
        return
    prods = await db.get_products_by_category(cid)
    low = sum(1 for p in prods if (p.get("qty", 0) or 0) <= 0)
    text = (
        f"🗂 <b>{c['name']}</b>\n"
        f"🆔 ID: {cid}\n"
        f"📦 Mahsulotlar: <b>{len(prods)} ta</b>\n"
        f"🔴 Tugaganlari: {low} ta\n"
        f"📅 {c['created_at'][:10]}"
    )
    await _send(target, text, category_actions_kb(cid))


@router.callback_query(F.data.startswith("cat_open_"))
async def cat_open(cb: CallbackQuery):
    if not await _guard(cb, "categories", "categories"):
        return
    cid = int(cb.data.rsplit("_", 1)[1])
    await _show_category_card(cb, cid)


@router.callback_query(F.data.startswith("cat_products_"))
async def cat_products(cb: CallbackQuery):
    if not await _guard(cb, "categories", "categories"):
        return
    cid = int(cb.data.rsplit("_", 1)[1])
    c = await db.get_category(cid)
    prods = await db.get_products_by_category(cid)
    if not prods:
        await cb.answer("Bu kategoriyada mahsulot yo'q.", show_alert=True)
        return
    name = c['name'] if c else cid
    await _send(
        cb,
        f"🗂 <b>{name}</b> — {len(prods)} ta mahsulot\n"
        "Tafsilot uchun mahsulot ustiga bosing:",
        simple_products_kb(prods, "view", back_cb=f"cat_open_{cid}")
    )


@router.callback_query(F.data.startswith("cat_edit_"))
async def cat_edit_start(cb: CallbackQuery, state: FSMContext):
    if not await _guard(cb, "categories", "categories"):
        return
    cid = int(cb.data.rsplit("_", 1)[1])
    c = await db.get_category(cid)
    if not c:
        await cb.answer("Topilmadi.", show_alert=True)
        return
    await state.set_state(EditCategoryStates.waiting)
    await state.update_data(edit_cid=cid)
    await cb.message.answer(
        f"✏️ <b>{c['name']}</b> — yangi nomini kiriting:",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )
    await cb.answer()


@router.message(EditCategoryStates.waiting)
async def cat_edit_done(message: Message, state: FSMContext):
    if message.text == CANCEL:
        await state.clear()
        await message.answer("Bekor.", reply_markup=await _menu(message.from_user.id))
        return
    name = (message.text or "").strip()
    if not name:
        await message.answer("⚠️ Bo'sh bo'lmasin. Yangi nom kiriting:")
        return
    data = await state.get_data()
    cid = data.get("edit_cid")
    await db.update_category(cid, name)
    await state.clear()
    await message.answer(
        f"✅ Kategoriya nomi yangilandi: <b>{name}</b>",
        reply_markup=await _menu(message.from_user.id), parse_mode="HTML"
    )
    await _show_category_card(message, cid)


@router.callback_query(F.data.startswith("cat_delok_"))
async def cat_delete_ok(cb: CallbackQuery):
    if not await _guard(cb, "categories", "categories"):
        return
    cid = int(cb.data.rsplit("_", 1)[1])
    await db.delete_category(cid)
    await cb.answer("🗑 O'chirildi!")
    await _show_categories(cb)


@router.callback_query(F.data.startswith("cat_del_"))
async def cat_delete_confirm(cb: CallbackQuery):
    if not await _guard(cb, "categories", "categories"):
        return
    cid = int(cb.data.rsplit("_", 1)[1])
    c = await db.get_category(cid)
    if not c:
        await cb.answer("Topilmadi.", show_alert=True)
        return
    prods = await db.get_products_by_category(cid)
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Ha, o'chir", callback_data=f"cat_delok_{cid}")
    kb.button(text="🔙 Yo'q",       callback_data=f"cat_open_{cid}")
    kb.adjust(2)
    await _send(
        cb,
        f"⚠️ <b>{c['name']}</b> kategoriyasini o'chirasizmi?\n\n"
        f"📦 {len(prods)} ta mahsulot bu kategoriyada. Ular <b>o'chmaydi</b> — "
        f"faqat kategoriyadan ajraladi (kategoriyasiz bo'lib qoladi).",
        kb.as_markup()
    )


# ═══════════════════════════════════════════════════════════════════════════
#  YETKAZIB BERUVCHILAR
# ═══════════════════════════════════════════════════════════════════════════

async def _show_suppliers(target):
    sups = await db.get_all_suppliers()
    total_low = sum(s.get("low_count", 0) for s in sups)
    if sups:
        text = (
            f"🚚 <b>Yetkazib beruvchilar — {len(sups)} ta</b>\n"
            f"⚠️ Tugagan/kam tovarlar jami: {total_low} ta\n\n"
            "Yetkazib beruvchi ustiga bosib mahsulotlari, zakaz ro'yxati va "
            "tezkor prixodni boshqaring."
        )
    else:
        text = ("🚚 <b>Yetkazib beruvchilar</b>\n\n"
                "Hali yetkazib beruvchi yo'q. Yangisini qo'shing 👇")
    await _send(target, text, suppliers_list_kb(sups))


@router.message(F.text == "🚚 Yetkazib beruvchilar")
async def suppliers_menu(message: Message, state: FSMContext):
    if not await _guard(message, "suppliers", "suppliers"):
        return
    await state.clear()
    await _show_suppliers(message)


@router.callback_query(F.data == "sup_back")
async def sup_back(cb: CallbackQuery, state: FSMContext):
    if not await _guard(cb, "suppliers", "suppliers"):
        return
    await state.clear()
    await _show_suppliers(cb)


@router.callback_query(F.data == "sup_add")
async def sup_add_start(cb: CallbackQuery, state: FSMContext):
    if not await _guard(cb, "suppliers", "suppliers"):
        return
    await state.set_state(AddSupplierStates.name)
    await cb.message.answer(
        "🚚 <b>Yangi yetkazib beruvchi</b>\n\n"
        "Yetkazib beruvchi nomini kiriting:\n"
        "(masalan: <code>Olma Optom</code> yoki ism)",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )
    await cb.answer()


@router.message(AddSupplierStates.name)
async def sup_add_name(message: Message, state: FSMContext):
    if message.text == CANCEL:
        await state.clear()
        await message.answer("Bekor.", reply_markup=await _menu(message.from_user.id))
        await _show_suppliers(message)
        return
    name = (message.text or "").strip()
    if not name:
        await message.answer("⚠️ Bo'sh bo'lmasin. Nom kiriting:")
        return
    await state.update_data(sup_name=name)
    await state.set_state(AddSupplierStates.phone)
    await message.answer(
        "📞 Telefon raqamini kiriting (yoki <b>—</b> o'tkazish uchun):",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )


@router.message(AddSupplierStates.phone)
async def sup_add_phone(message: Message, state: FSMContext):
    if message.text == CANCEL:
        await state.clear()
        await message.answer("Bekor.", reply_markup=await _menu(message.from_user.id))
        return
    txt = (message.text or "").strip()
    phone = "" if txt in ("-", "—", "") else txt
    await state.update_data(sup_phone=phone)
    await state.set_state(AddSupplierStates.note)
    await message.answer(
        "📝 Izoh kiriting (manzil, ish vaqti va h.k.) yoki <b>—</b> o'tkazish uchun:",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )


@router.message(AddSupplierStates.note)
async def sup_add_note(message: Message, state: FSMContext):
    if message.text == CANCEL:
        await state.clear()
        await message.answer("Bekor.", reply_markup=await _menu(message.from_user.id))
        return
    txt = (message.text or "").strip()
    note = "" if txt in ("-", "—", "") else txt
    data = await state.get_data()
    sid = await db.add_supplier(data.get("sup_name", ""),
                                data.get("sup_phone", ""), note)
    await state.clear()
    await message.answer(
        f"✅ <b>{data.get('sup_name','')}</b> yetkazib beruvchi qo'shildi!\n\n"
        "Endi mahsulot qo'shish/tahrirlashda uni biriktirishingiz mumkin.",
        reply_markup=await _menu(message.from_user.id), parse_mode="HTML"
    )
    await _show_supplier_card(message, sid)


async def _show_supplier_card(target, sid: int):
    s = await db.get_supplier(sid)
    if not s:
        if isinstance(target, CallbackQuery):
            await target.answer("Topilmadi.", show_alert=True)
        return
    prods = await db.get_products_by_supplier(sid)
    low = await db.get_low_stock_by_supplier(sid)
    text = (
        f"🚚 <b>{s['name']}</b>\n"
        f"🆔 ID: {sid}\n"
        f"📞 {s.get('phone') or '—'}\n"
        f"📝 {s.get('note') or '—'}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📦 Mahsulotlari: <b>{len(prods)} ta</b>\n"
        f"⚠️ Tugagan/kam: <b>{len(low)} ta</b>\n"
        f"📅 {s['created_at'][:10]}"
    )
    await _send(target, text, supplier_actions_kb(sid))


@router.callback_query(F.data.startswith("sup_open_"))
async def sup_open(cb: CallbackQuery):
    if not await _guard(cb, "suppliers", "suppliers"):
        return
    sid = int(cb.data.rsplit("_", 1)[1])
    await _show_supplier_card(cb, sid)


@router.callback_query(F.data.startswith("sup_products_"))
async def sup_products(cb: CallbackQuery):
    if not await _guard(cb, "suppliers", "suppliers"):
        return
    sid = int(cb.data.rsplit("_", 1)[1])
    s = await db.get_supplier(sid)
    prods = await db.get_products_by_supplier(sid)
    if not prods:
        await cb.answer("Bu yetkazib beruvchida mahsulot yo'q.", show_alert=True)
        return
    name = s['name'] if s else sid
    await _send(
        cb,
        f"🚚 <b>{name}</b> — {len(prods)} ta mahsulot\n"
        "<i>Qoldig'i kam birinchi.</i> Tafsilot uchun bosing:",
        simple_products_kb(prods, "view", back_cb=f"sup_open_{sid}")
    )


@router.callback_query(F.data.startswith("sup_edit_"))
async def sup_edit_menu(cb: CallbackQuery):
    if not await _guard(cb, "suppliers", "suppliers"):
        return
    sid = int(cb.data.rsplit("_", 1)[1])
    s = await db.get_supplier(sid)
    if not s:
        await cb.answer("Topilmadi.", show_alert=True)
        return
    await _send(cb, f"✏️ <b>{s['name']}</b> — qaysi maydonni o'zgartirasiz?",
                supplier_edit_fields_kb(sid))


@router.callback_query(F.data.startswith("supedit_"))
async def sup_edit_field_start(cb: CallbackQuery, state: FSMContext):
    if not await _guard(cb, "suppliers", "suppliers"):
        return
    rest = cb.data[len("supedit_"):]          # "<sid>_<field>"
    sid_str, field = rest.split("_", 1)
    sid = int(sid_str)
    await state.set_state(EditSupplierStates.waiting)
    await state.update_data(edit_sid=sid, edit_field=field)
    labels = {"name": "yangi nom", "phone": "yangi telefon raqami",
              "note": "yangi izoh"}
    hint = "" if field == "name" else "\n(yoki <b>—</b> — bo'sh qoldirish)"
    await cb.message.answer(
        f"✏️ {labels.get(field, field)} kiriting:{hint}",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )
    await cb.answer()


@router.message(EditSupplierStates.waiting)
async def sup_edit_field_done(message: Message, state: FSMContext):
    if message.text == CANCEL:
        await state.clear()
        await message.answer("Bekor.", reply_markup=await _menu(message.from_user.id))
        return
    data = await state.get_data()
    sid = data.get("edit_sid")
    field = data.get("edit_field")
    txt = (message.text or "").strip()
    if field == "name" and not txt:
        await message.answer("⚠️ Nom bo'sh bo'lmasin. Qayta kiriting:")
        return
    if field in ("phone", "note") and txt in ("-", "—"):
        txt = ""
    await db.update_supplier(sid, **{field: txt})
    await state.clear()
    await message.answer("✅ Yangilandi!", reply_markup=await _menu(message.from_user.id))
    await _show_supplier_card(message, sid)


@router.callback_query(F.data.startswith("sup_delok_"))
async def sup_delete_ok(cb: CallbackQuery):
    if not await _guard(cb, "suppliers", "suppliers"):
        return
    sid = int(cb.data.rsplit("_", 1)[1])
    await db.delete_supplier(sid)
    await cb.answer("🗑 O'chirildi!")
    await _show_suppliers(cb)


@router.callback_query(F.data.startswith("sup_del_"))
async def sup_delete_confirm(cb: CallbackQuery):
    if not await _guard(cb, "suppliers", "suppliers"):
        return
    sid = int(cb.data.rsplit("_", 1)[1])
    s = await db.get_supplier(sid)
    if not s:
        await cb.answer("Topilmadi.", show_alert=True)
        return
    prods = await db.get_products_by_supplier(sid)
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Ha, o'chir", callback_data=f"sup_delok_{sid}")
    kb.button(text="🔙 Yo'q",       callback_data=f"sup_open_{sid}")
    kb.adjust(2)
    await _send(
        cb,
        f"⚠️ <b>{s['name']}</b> yetkazib beruvchini o'chirasizmi?\n\n"
        f"📦 {len(prods)} ta mahsulot unga biriktirilgan. Mahsulotlar "
        f"<b>o'chmaydi</b> — faqat bog'lanish uziladi.",
        kb.as_markup()
    )


# ═══════════════════════════════════════════════════════════════════════════
#  ZAKAZ RO'YXATI TUZISH  (sord_*)
# ═══════════════════════════════════════════════════════════════════════════

def _accum_get(accum: dict, pid) -> float:
    return float(accum.get(str(pid), accum.get(pid, 0)) or 0)


async def _show_sord_builder(target, state: FSMContext):
    data = await state.get_data()
    sid = data.get("sord_sup_id")
    accum = data.get("sord_accum", {})
    s = await db.get_supplier(sid)
    prods = await db.get_products_by_supplier(sid)
    await state.set_state(SupplierOrderStates.building)
    picked = sum(1 for v in accum.values() if v)
    text = (
        f"📋 <b>Zakaz ro'yxati — {s['name'] if s else sid}</b>\n\n"
        f"Tovar ustiga bosib, kerakli miqdorni qo'shing "
        f"(➕{STEP}/➖{STEP} yoki aniq son).\n"
        f"Tugaganlari (🔴) yuqorida.\n\n"
        f"📝 Ro'yxatda: <b>{picked} xil mahsulot</b>"
    )
    if not prods:
        await _send(target, "Bu yetkazib beruvchida mahsulot yo'q.\n"
                            "Avval mahsulotni unga biriktiring.", None)
        return
    await _send(target, text,
                qty_builder_kb(prods, accum, "sord",
                               action_label="✅ Ro'yxatni ko'rsatish"))


def _builder_item_text(p: dict, qty: float, mode: str) -> str:
    unit = p.get("unit", "dona")
    label = "📋 Ro'yxatga" if mode == "sord" else "📥 Qo'shiladi"
    return (
        f"📦 <b>{p['name']}</b>\n"
        f"🏬 Omborda: <b>{p.get('qty', 0):g} {unit}</b>\n"
        f"{label}: <b>{qty:g} {unit}</b>\n\n"
        f"➕{STEP}/➖{STEP} bilan sozlang yoki aniq miqdor kiriting:"
    )


@router.callback_query(F.data.startswith("sup_order_"))
async def sord_start(cb: CallbackQuery, state: FSMContext):
    if not await _guard(cb, "suppliers", "suppliers"):
        return
    sid = int(cb.data.rsplit("_", 1)[1])
    await state.set_state(SupplierOrderStates.building)
    await state.update_data(sord_sup_id=sid, sord_accum={})
    await _show_sord_builder(cb, state)


@router.callback_query(F.data == "sord_back")
async def sord_back(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    sid = data.get("sord_sup_id")
    await state.clear()
    if sid:
        await _show_supplier_card(cb, sid)
    else:
        await _show_suppliers(cb)


@router.callback_query(F.data == "sord_list")
async def sord_list(cb: CallbackQuery, state: FSMContext):
    await _show_sord_builder(cb, state)


@router.callback_query(F.data == "sord_clear")
async def sord_clear(cb: CallbackQuery, state: FSMContext):
    await state.update_data(sord_accum={})
    await cb.answer("🗑 Ro'yxat tozalandi")
    await _show_sord_builder(cb, state)


@router.callback_query(F.data.startswith("sord_item_"))
async def sord_item(cb: CallbackQuery, state: FSMContext):
    pid = int(cb.data.rsplit("_", 1)[1])
    p = await db.get_product_any(pid)
    if not p:
        await cb.answer("Topilmadi.", show_alert=True)
        return
    data = await state.get_data()
    accum = data.get("sord_accum", {})
    await state.update_data(sord_cur_pid=pid)
    await _send(cb, _builder_item_text(p, _accum_get(accum, pid), "sord"),
                qty_builder_item_kb(pid, "sord", STEP))


async def _sord_adjust(cb, state, delta):
    pid = int(cb.data.rsplit("_", 1)[1])
    data = await state.get_data()
    accum = dict(data.get("sord_accum", {}))
    cur = _accum_get(accum, pid)
    new = max(0.0, cur + delta)
    if new > 0:
        accum[str(pid)] = new
    else:
        accum.pop(str(pid), None)
        accum.pop(pid, None)
    await state.update_data(sord_accum=accum)
    p = await db.get_product_any(pid)
    await _send(cb, _builder_item_text(p, new, "sord"),
                qty_builder_item_kb(pid, "sord", STEP))


@router.callback_query(F.data.startswith("sord_inc_"))
async def sord_inc(cb: CallbackQuery, state: FSMContext):
    await _sord_adjust(cb, state, STEP)


@router.callback_query(F.data.startswith("sord_dec_"))
async def sord_dec(cb: CallbackQuery, state: FSMContext):
    await _sord_adjust(cb, state, -STEP)


@router.callback_query(F.data.startswith("sord_rm_"))
async def sord_rm(cb: CallbackQuery, state: FSMContext):
    pid = int(cb.data.rsplit("_", 1)[1])
    data = await state.get_data()
    accum = dict(data.get("sord_accum", {}))
    accum.pop(str(pid), None)
    accum.pop(pid, None)
    await state.update_data(sord_accum=accum)
    await cb.answer("🗑 Olib tashlandi")
    await _show_sord_builder(cb, state)


@router.callback_query(F.data.startswith("sord_set_"))
async def sord_set_start(cb: CallbackQuery, state: FSMContext):
    pid = int(cb.data.rsplit("_", 1)[1])
    p = await db.get_product_any(pid)
    if not p:
        await cb.answer("Topilmadi.", show_alert=True)
        return
    await state.update_data(sord_cur_pid=pid)
    await state.set_state(SupplierOrderStates.qty)
    await cb.message.answer(
        f"✏️ <b>{p['name']}</b> — zakaz miqdorini kiriting "
        f"({p.get('unit','dona')}):",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )
    await cb.answer()


@router.message(SupplierOrderStates.qty)
async def sord_set_done(message: Message, state: FSMContext):
    if message.text == CANCEL:
        await state.set_state(SupplierOrderStates.building)
        await _show_sord_builder(message, state)
        return
    try:
        qty = float((message.text or "").strip().replace(",", "."))
        if qty < 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Musbat son kiriting (masalan: 30):")
        return
    data = await state.get_data()
    pid = data.get("sord_cur_pid")
    accum = dict(data.get("sord_accum", {}))
    if qty > 0:
        accum[str(pid)] = qty
    else:
        accum.pop(str(pid), None)
        accum.pop(pid, None)
    await state.update_data(sord_accum=accum)
    await state.set_state(SupplierOrderStates.building)
    await _show_sord_builder(message, state)


@router.callback_query(F.data == "sord_save")
async def sord_save(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    sid = data.get("sord_sup_id")
    accum = data.get("sord_accum", {})
    s = await db.get_supplier(sid)
    items = [(str(k), v) for k, v in accum.items() if v]
    if not items:
        await cb.answer("⚠️ Ro'yxat bo'sh!", show_alert=True)
        return
    lines = ""
    n = 0
    for k, qty in items:
        p = await db.get_product_any(int(k))
        if not p:
            continue
        n += 1
        unit = p.get("unit", "dona")
        lines += f"{n}. {p['name']} — <b>{qty:g} {unit}</b>\n"
    phone_line = (s.get('phone') or '—') if s else '—'
    text = (
        f"📋 <b>ZAKAZ RO'YXATI</b>\n"
        f"🚚 {s['name'] if s else '—'}\n"
        f"📞 {phone_line}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{lines}"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📦 Jami: <b>{n} xil mahsulot</b>\n\n"
        f"<i>Ushbu ro'yxatni nusxalab yetkazib beruvchiga yuboring.</i>"
    )
    await state.clear()
    await cb.message.answer(text, parse_mode="HTML")
    await cb.answer("✅ Ro'yxat tayyor")
    if sid:
        await _show_supplier_card(cb.message, sid)


# ═══════════════════════════════════════════════════════════════════════════
#  TEZKOR PRIXOD  (qpx_*)
# ═══════════════════════════════════════════════════════════════════════════

async def _show_qpx_builder(target, state: FSMContext):
    data = await state.get_data()
    sid = data.get("qpx_sup_id")
    accum = data.get("qpx_accum", {})
    s = await db.get_supplier(sid)
    prods = await db.get_products_by_supplier(sid)
    await state.set_state(QuickPrixodStates.picking)
    picked = sum(1 for v in accum.values() if v)
    if not prods:
        await _send(target, "Bu yetkazib beruvchida mahsulot yo'q.", None)
        return
    text = (
        f"⚡ <b>Tezkor prixod — {s['name'] if s else sid}</b>\n\n"
        f"Tovar ustiga bosib, kelgan miqdorni qo'shing.\n"
        f"«✅ Prixodni saqlash» — barchasi omborga qo'shiladi.\n\n"
        f"📝 Tanlangan: <b>{picked} xil mahsulot</b>"
    )
    await _send(target, text,
                qty_builder_kb(prods, accum, "qpx",
                               action_label="✅ Prixodni saqlash"))


@router.callback_query(F.data.startswith("sup_prixod_"))
async def qpx_start(cb: CallbackQuery, state: FSMContext):
    if not await _guard(cb, "suppliers", "suppliers"):
        return
    if not db.is_quick_prixod_enabled():
        await cb.answer("ℹ️ Tezkor prixod funksiyasi o'chirilgan.", show_alert=True)
        return
    if not await has_permission(db, cb.from_user.id, "products_qty"):
        await cb.answer("⛔ Prixod uchun ruxsat yo'q.", show_alert=True)
        return
    sid = int(cb.data.rsplit("_", 1)[1])
    await state.set_state(QuickPrixodStates.picking)
    await state.update_data(qpx_sup_id=sid, qpx_accum={})
    await _show_qpx_builder(cb, state)


@router.callback_query(F.data == "qpx_back")
async def qpx_back(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    sid = data.get("qpx_sup_id")
    await state.clear()
    if sid:
        await _show_supplier_card(cb, sid)
    else:
        await _show_suppliers(cb)


@router.callback_query(F.data == "qpx_list")
async def qpx_list(cb: CallbackQuery, state: FSMContext):
    await _show_qpx_builder(cb, state)


@router.callback_query(F.data == "qpx_clear")
async def qpx_clear(cb: CallbackQuery, state: FSMContext):
    await state.update_data(qpx_accum={})
    await cb.answer("🗑 Tozalandi")
    await _show_qpx_builder(cb, state)


@router.callback_query(F.data.startswith("qpx_item_"))
async def qpx_item(cb: CallbackQuery, state: FSMContext):
    pid = int(cb.data.rsplit("_", 1)[1])
    p = await db.get_product_any(pid)
    if not p:
        await cb.answer("Topilmadi.", show_alert=True)
        return
    data = await state.get_data()
    accum = data.get("qpx_accum", {})
    await state.update_data(qpx_cur_pid=pid)
    await _send(cb, _builder_item_text(p, _accum_get(accum, pid), "qpx"),
                qty_builder_item_kb(pid, "qpx", STEP))


async def _qpx_adjust(cb, state, delta):
    pid = int(cb.data.rsplit("_", 1)[1])
    data = await state.get_data()
    accum = dict(data.get("qpx_accum", {}))
    cur = _accum_get(accum, pid)
    new = max(0.0, cur + delta)
    if new > 0:
        accum[str(pid)] = new
    else:
        accum.pop(str(pid), None)
        accum.pop(pid, None)
    await state.update_data(qpx_accum=accum)
    p = await db.get_product_any(pid)
    await _send(cb, _builder_item_text(p, new, "qpx"),
                qty_builder_item_kb(pid, "qpx", STEP))


@router.callback_query(F.data.startswith("qpx_inc_"))
async def qpx_inc(cb: CallbackQuery, state: FSMContext):
    await _qpx_adjust(cb, state, STEP)


@router.callback_query(F.data.startswith("qpx_dec_"))
async def qpx_dec(cb: CallbackQuery, state: FSMContext):
    await _qpx_adjust(cb, state, -STEP)


@router.callback_query(F.data.startswith("qpx_rm_"))
async def qpx_rm(cb: CallbackQuery, state: FSMContext):
    pid = int(cb.data.rsplit("_", 1)[1])
    data = await state.get_data()
    accum = dict(data.get("qpx_accum", {}))
    accum.pop(str(pid), None)
    accum.pop(pid, None)
    await state.update_data(qpx_accum=accum)
    await cb.answer("🗑 Olib tashlandi")
    await _show_qpx_builder(cb, state)


@router.callback_query(F.data.startswith("qpx_set_"))
async def qpx_set_start(cb: CallbackQuery, state: FSMContext):
    pid = int(cb.data.rsplit("_", 1)[1])
    p = await db.get_product_any(pid)
    if not p:
        await cb.answer("Topilmadi.", show_alert=True)
        return
    await state.update_data(qpx_cur_pid=pid)
    await state.set_state(QuickPrixodStates.qty)
    await cb.message.answer(
        f"✏️ <b>{p['name']}</b> — kelgan miqdorni kiriting "
        f"({p.get('unit','dona')}):",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )
    await cb.answer()


@router.message(QuickPrixodStates.qty)
async def qpx_set_done(message: Message, state: FSMContext):
    if message.text == CANCEL:
        await state.set_state(QuickPrixodStates.picking)
        await _show_qpx_builder(message, state)
        return
    try:
        qty = float((message.text or "").strip().replace(",", "."))
        if qty < 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Musbat son kiriting (masalan: 50):")
        return
    data = await state.get_data()
    pid = data.get("qpx_cur_pid")
    accum = dict(data.get("qpx_accum", {}))
    if qty > 0:
        accum[str(pid)] = qty
    else:
        accum.pop(str(pid), None)
        accum.pop(pid, None)
    await state.update_data(qpx_accum=accum)
    await state.set_state(QuickPrixodStates.picking)
    await _show_qpx_builder(message, state)


@router.callback_query(F.data == "qpx_save")
async def qpx_save(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    sid = data.get("qpx_sup_id")
    accum = data.get("qpx_accum", {})
    items = [(str(k), v) for k, v in accum.items() if v]
    if not items:
        await cb.answer("⚠️ Hech narsa tanlanmagan!", show_alert=True)
        return
    await cb.answer("⏳ Saqlanmoqda...")
    s = await db.get_supplier(sid)
    lines = ""
    n = 0
    for k, qty in items:
        p = await db.get_product_any(int(k))
        if not p:
            continue
        old = float(p.get("qty", 0) or 0)
        await db.change_qty(int(k), qty)
        n += 1
        unit = p.get("unit", "dona")
        lines += f"• {p['name']}: {old:g} → <b>{old + qty:g} {unit}</b>  (+{qty:g})\n"
    await state.clear()
    await cb.message.answer(
        f"✅ <b>PRIXOD SAQLANDI</b>\n"
        f"🚚 {s['name'] if s else '—'}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{lines}"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📦 {n} ta mahsulot omboriga qo'shildi.",
        parse_mode="HTML"
    )
    if sid:
        await _show_supplier_card(cb.message, sid)


# ═══════════════════════════════════════════════════════════════════════════
#  PAST QOLDIQ — TEZKOR TO'LDIRISH  (qrs_*)
# ═══════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("qrs_"))
async def quick_restock_start(cb: CallbackQuery, state: FSMContext):
    if not await is_admin_or_glavniy(db, cb.from_user.id):
        return
    if not db.is_quick_restock_enabled():
        await cb.answer("ℹ️ Tezkor to'ldirish o'chirilgan.", show_alert=True)
        return
    if not await has_permission(db, cb.from_user.id, "products_qty"):
        await cb.answer("⛔ Prixod uchun ruxsat yo'q.", show_alert=True)
        return
    pid = int(cb.data.rsplit("_", 1)[1])
    p = await db.get_product_any(pid)
    if not p:
        await cb.answer("Topilmadi.", show_alert=True)
        return
    await state.set_state(QuickRestockStates.qty)
    await state.update_data(qrs_pid=pid)
    unit = p.get("unit", "dona")
    await cb.message.answer(
        f"⚡ <b>{p['name']}</b>\n"
        f"🏬 Hozir: {p.get('qty', 0):g} {unit}\n\n"
        f"Nechta {unit} qo'shamiz?",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )
    await cb.answer()


@router.message(QuickRestockStates.qty)
async def quick_restock_done(message: Message, state: FSMContext):
    if message.text == CANCEL:
        await state.clear()
        await message.answer("Bekor.", reply_markup=await _menu(message.from_user.id))
        return
    try:
        qty = float((message.text or "").strip().replace(",", "."))
        if qty <= 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Musbat son kiriting (masalan: 20):")
        return
    data = await state.get_data()
    pid = data.get("qrs_pid")
    await db.change_qty(pid, qty)
    await state.clear()
    p = await db.get_product_any(pid)
    unit = p.get("unit", "dona") if p else "dona"
    await message.answer(
        f"✅ <b>{p['name'] if p else pid}</b> to'ldirildi!\n"
        f"🏬 Yangi qoldiq: <b>{p.get('qty', 0):g} {unit}</b>"
        if p else "✅ To'ldirildi!",
        reply_markup=await _menu(message.from_user.id), parse_mode="HTML"
    )
