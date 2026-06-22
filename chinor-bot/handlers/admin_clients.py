from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from datetime import datetime

from bot.keyboards import (
    admin_menu, clients_list_kb, client_actions_kb,
    cancel_kb, orders_list_kb, client_products_kb, client_type_kb
)
from bot.states import AddClientStates, PayClientStates, OrderStates
from bot.permissions import (
    has_permission, get_user_menu, is_admin_or_glavniy, deny, require,
)
from database.channel_db import db, fmt_usd, fmt_sum, usd_to_sum, sum_to_usd


async def _deny(message: Message, text: str = "⛔ Sizda bu amal uchun ruxsat yo'q."):
    # Markaziy helperga ko'prik
    await deny(message, db, text)

def _parse_money_admin(txt: str):
    s = (txt or "").strip().lower()
    if not s:
        raise ValueError
    is_usd = "$" in s or "usd" in s or "dollar" in s
    s = s.replace("$","").replace("usd","").replace("dollar","").replace(" ","").replace(",",".")
    val = float(s)
    if val < 0:
        raise ValueError
    return val, ("usd" if is_usd else "sum")

router = Router()


async def _is_admin(uid: int) -> bool:
    # Markaziy helperga ko'prik
    return await is_admin_or_glavniy(db, uid)


# ── Ro'yxat ───────────────────────────────────────────────────────────────────

@router.message(F.text == "👤 Mijozlar")
async def show_clients(message: Message):
    uid = message.from_user.id
    if not await _is_admin(uid):
        return
    if not await has_permission(db, uid, "clients_view"):
        await _deny(message)
        return
    clients = await db.get_all_clients()
    if not clients:
        await message.answer("👤 Mijozlar yo'q.", reply_markup=admin_menu())
        return
    await message.answer(
        f"👤 <b>Mijozlar ({len(clients)} ta):</b>",
        reply_markup=clients_list_kb(clients), parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("client_view_"))
async def view_client(cb: CallbackQuery):
    if not await _is_admin(cb.from_user.id):
        return
    cid = int(cb.data.split("_")[2])
    c = await db.get_client_by_id(cid)
    if not c:
        await cb.answer("Topilmadi!")
        return
    from database.channel_db import now_local
    month = now_local().strftime("%Y-%m")
    rep = await db.client_monthly_report(cid, month)
    ctype = (c.get("client_type") or "dona").lower()
    type_label = "🛍️ Donachi" if ctype == "dona" else "📦 Optomchi"
    tg = c.get("telegram_id")
    tg_line = (f"🆔 <code>{tg}</code>"
               if tg not in (None, 0)
               else "🆔 <i>Telegrami yo'q</i>")
    debt_usd = float(c.get("debt_usd", 0) or 0)
    debt_sum = float(c.get("debt", 0) or 0)
    if debt_usd > 0 or debt_sum > 0:
        if debt_usd > 0:
            debt_line = f"💰 Qarz: <b>{fmt_usd(debt_usd)}</b>  (≈ {fmt_sum(debt_sum)})"
        else:
            debt_line = f"💰 Qarz: <b>{fmt_sum(debt_sum)}</b>"
    else:
        debt_line = "✅ Qarzsiz"
    await cb.message.answer(
        f"👤 <b>{c['shop_name']}</b>\n"
        f"📱 {c['phone']}\n"
        f"{tg_line}\n"
        f"🏷️ Turi: <b>{type_label}</b>\n"
        f"{debt_line}\n"
        f"📅 {c.get('created_at', '')[:10]}\n\n"
        f"📊 Bu oy: zakaz {rep['total_ordered']:,.0f} | to'lov {rep['total_paid']:,.0f} so'm",
        reply_markup=client_actions_kb(cid), parse_mode="HTML"
    )
    await cb.answer()


@router.callback_query(F.data == "clients_back")
async def clients_back(cb: CallbackQuery):
    await cb.answer()
    clients = await db.get_all_clients()
    await cb.message.answer("👤 Mijozlar:", reply_markup=clients_list_kb(clients))


# ── Qo'shish ──────────────────────────────────────────────────────────────────

@router.message(F.text == "➕ Mijoz qo'shish")
async def add_client_start(message: Message, state: FSMContext):
    uid = message.from_user.id
    if not await _is_admin(uid):
        return
    if not await has_permission(db, uid, "clients_add"):
        await _deny(message)
        return
    await message.answer(
        "👤 <b>Yangi mijoz</b>\n\n"
        "Mijoz ismini (yoki do'kon nomini) kiriting:",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )
    await state.set_state(AddClientStates.shop_name)


@router.message(AddClientStates.shop_name)
async def ac_shop(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor.", reply_markup=admin_menu())
        return
    name = (message.text or "").strip()
    if not name:
        await message.answer("⚠️ Bo'sh bo'lmasin. Ism kiriting:")
        return
    await state.update_data(shop_name=name)
    await message.answer(
        "📱 <b>Telefon raqamini kiriting</b> (mamlakat kodi bilan):\n"
        "Masalan: <code>+998901234567</code>  yoki  <code>998901234567</code>\n\n"
        "<i>Mijoz keyin botga kirib o'z raqamini yuborganda — avtomatik "
        "shu profilga ulanadi.</i>",
        parse_mode="HTML"
    )
    await state.set_state(AddClientStates.phone)


def _is_valid_phone(txt: str) -> bool:
    digits = "".join(c for c in (txt or "") if c.isdigit())
    return 9 <= len(digits) <= 15


@router.message(AddClientStates.phone)
async def ac_phone(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor.", reply_markup=admin_menu())
        return
    phone_txt = (message.text or "").strip()
    if not _is_valid_phone(phone_txt):
        await message.answer(
            "⚠️ Telefon raqami noto'g'ri. Kamida 9 ta raqam bo'lishi kerak.\n"
            "Masalan: <code>+998901234567</code>",
            parse_mode="HTML"
        )
        return
    # Telefon dublikat tekshiruvi — admin'ga oldindan ogohlantirish
    exists = await db.get_client_by_phone(phone_txt)
    if exists:
        await message.answer(
            f"⚠️ Bu telefon raqami allaqachon mavjud:\n"
            f"👤 <b>{exists['shop_name']}</b>\n"
            f"📱 {exists.get('phone','')}\n\n"
            f"Boshqa raqam kiriting yoki ❌ Bekor qilish:",
            parse_mode="HTML"
        )
        return
    await state.update_data(phone=phone_txt)
    # Faqat bitta narx turi yoqilgan bo'lsa — mijoz turini so'ramasdan
    dona_on = db.is_dona_enabled()
    whs_on = db.is_wholesale_enabled()
    if not (dona_on and whs_on):
        auto_ctype = "optom" if (whs_on and not dona_on) else "dona"
        await _save_new_client(message, state, auto_ctype)
        return
    await message.answer(
        "🏷️ Mijoz turini tanlang:\n"
        "• 🛍️ <b>Donachi</b> — odatdagi (chakana) narxda sotiladi\n"
        "• 📦 <b>Optomchi</b> — optom (ulgurji) narxda sotiladi",
        reply_markup=client_type_kb(), parse_mode="HTML"
    )
    await state.set_state(AddClientStates.client_type)


async def _save_new_client(target, state: FSMContext, ctype: str):
    """Yangi mijozni saqlaydi va admin'ga natijani ko'rsatadi.
    Telegram ID ESLAB QO'YILMAYDI — mijoz keyin botga 'Raqamni yuborish'
    tugmasi bilan o'zini avtomatik avtorizatsiya qiladi."""
    data = await state.get_data()
    uid = target.from_user.id
    ok = await db.add_client(
        tg_id=None,
        shop_name=data["shop_name"],
        phone=data.get("phone", ""),
        registered_by=uid,
        client_type=ctype,
    )
    await state.clear()
    type_label = "🛍️ Donachi" if ctype == "dona" else "📦 Optomchi"
    msg = (target.message if hasattr(target, "data") and not hasattr(target, "text")
           else target)
    if ok:
        await msg.answer(
            f"✅ <b>Mijoz qo'shildi!</b>\n\n"
            f"👤 <b>{data['shop_name']}</b>\n"
            f"📱 {data.get('phone','')}\n"
            f"🏷️ Turi: <b>{type_label}</b>\n\n"
            f"📲 <i>Mijoz botga kirib «📱 Raqamni yuborish» tugmasini bossa, "
            f"shu telefon raqami bo'yicha avtomatik tanib olinadi.</i>",
            reply_markup=admin_menu(), parse_mode="HTML"
        )
    else:
        await msg.answer(
            "⚠️ Bu mijoz allaqachon mavjud (telefon raqami takrorlangan).",
            reply_markup=admin_menu()
        )


@router.callback_query(AddClientStates.client_type, F.data.startswith("ctype_"))
async def ac_type(cb: CallbackQuery, state: FSMContext):
    ctype = cb.data.split("_", 1)[1]  # 'dona' yoki 'optom'
    if ctype not in ("dona", "optom"):
        await cb.answer("❌ Noto'g'ri tur!")
        return
    await _save_new_client(cb, state, ctype)
    await cb.answer()


# ── O'chirish ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("client_del_"))
async def client_del_ask(cb: CallbackQuery):
    if not await _is_admin(cb.from_user.id):
        return
    cid = int(cb.data.split("_")[2])
    c = await db.get_client_by_id(cid)
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Ha", callback_data=f"client_delok_{cid}")
    kb.button(text="❌ Yo'q", callback_data=f"client_view_{cid}")
    kb.adjust(2)
    await cb.message.answer(
        f"⚠️ <b>{c['shop_name']}</b> ni o'chirasizmi?",
        reply_markup=kb.as_markup(), parse_mode="HTML"
    )
    await cb.answer()


@router.callback_query(F.data.startswith("client_delok_"))
async def client_del_ok(cb: CallbackQuery):
    if not await _is_admin(cb.from_user.id):
        return
    cid = int(cb.data.split("_")[2])
    await db.delete_client(cid)
    await cb.answer("🗑️ O'chirildi!")
    await cb.message.edit_text("🗑️ Mijoz o'chirildi.")


# ── To'lov ────────────────────────────────────────────────────────────────────

@router.message(F.text == "💳 To'lov qabul qilish")
async def payment_start(message: Message):
    uid = message.from_user.id
    if not await _is_admin(uid):
        return
    if not await has_permission(db, uid, "clients_pay"):
        await _deny(message)
        return
    clients = await db.get_all_clients()
    debtors = [c for c in clients if c.get("debt", 0) > 0]
    if not debtors:
        await message.answer("✅ Barcha mijozlar qarzsiz!", reply_markup=admin_menu())
        return
    await message.answer("Qaysi mijozdan to'lov?", reply_markup=clients_list_kb(debtors))


@router.callback_query(F.data.startswith("pay_"))
async def pay_start(cb: CallbackQuery, state: FSMContext):
    if not await _is_admin(cb.from_user.id):
        return
    # pay_cash, pay_card, pay_mixed, pay_nasiya kabi callback larni o'tkazib yuborish
    parts = cb.data.split("_")
    if len(parts) < 2 or not parts[1].isdigit():
        await cb.answer()
        return
    cid = int(parts[1])
    c = await db.get_client_by_id(cid)
    if not c:
        await cb.answer("Topilmadi!")
        return
    debt_usd = float(c.get("debt_usd", 0) or 0)
    debt_sum = float(c.get("debt", 0) or 0)
    await state.update_data(pay_cid=cid)
    await cb.message.answer(
        f"💳 <b>{c['shop_name']}</b>\n"
        f"💰 Qarz: <b>{fmt_usd(debt_usd)}</b>  (≈ {fmt_sum(debt_sum)})\n\n"
        f"To'lov summasini kiriting:\n"
        f"USDda: <code>$20</code> yoki <code>15.50</code>\n"
        f"So'mda: <code>200000</code>",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )
    await state.set_state(PayClientStates.amount)
    await cb.answer()


@router.message(PayClientStates.amount)
async def pay_amount(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor.", reply_markup=admin_menu())
        return
    try:
        amount, currency = _parse_money_admin(message.text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer(
            "⚠️ Summani to'g'ri kiriting:\n"
            "USDda: <code>$20</code>; So'mda: <code>200000</code>",
            parse_mode="HTML"
        )
        return
    await state.update_data(pay_amount=amount, pay_currency=currency)
    await message.answer("📝 Izoh yozing (yoki — o'tkazish uchun):")
    await state.set_state(PayClientStates.note)


@router.message(PayClientStates.note)
async def pay_note(message: Message, state: FSMContext):
    note = "" if message.text.strip() in ["-", "—"] else message.text.strip()
    data = await state.get_data()
    cur = data.get("pay_currency", "sum")
    await db.add_payment(data["pay_cid"], data["pay_amount"],
                         currency=cur, note=note)
    await state.clear()
    c = await db.get_client_by_id(data["pay_cid"])
    debt_usd = float(c.get("debt_usd", 0) or 0)
    debt_sum = float(c.get("debt", 0) or 0)
    if cur == "usd":
        paid_line = f"💳 To'langan: <b>{fmt_usd(data['pay_amount'])}</b>"
    else:
        paid_line = f"💳 To'langan: <b>{fmt_sum(data['pay_amount'])}</b>"
    if debt_usd > 0 or debt_sum > 0:
        debt_line = f"💰 Qolgan qarz: <b>{fmt_usd(debt_usd)}</b>  (≈ {fmt_sum(debt_sum)})"
    else:
        debt_line = "✅ Qarzsiz"
    await message.answer(
        f"✅ To'lov qabul qilindi!\n\n"
        f"👤 <b>{c['shop_name']}</b>\n"
        f"{paid_line}\n"
        f"{debt_line}",
        reply_markup=admin_menu(), parse_mode="HTML"
    )


# ── Buyurtmalar tarixi ────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("client_orders_"))
async def client_orders(cb: CallbackQuery):
    if not await _is_admin(cb.from_user.id):
        return
    cid = int(cb.data.split("_")[2])
    c = await db.get_client_by_id(cid)
    orders = await db.get_client_orders(cid)
    if not orders:
        await cb.message.answer(f"📋 {c['shop_name']} — buyurtmalar yo'q.")
        await cb.answer()
        return
    await cb.message.answer(
        f"📋 <b>{c['shop_name']}</b> ({len(orders)} ta):",
        reply_markup=orders_list_kb(orders[:20]), parse_mode="HTML"
    )
    await cb.answer()


@router.callback_query(F.data.startswith("odetail_"))
async def order_detail(cb: CallbackQuery):
    oid = int(cb.data.split("_")[1])
    o = await db.get_order(oid)
    if not o:
        await cb.answer("Topilmadi!")
        return
    from bot.config import ORDER_STATUSES
    from bot.keyboards import order_status_kb
    st = ORDER_STATUSES.get(o.get("status", ""), "")
    lines = "".join(
        f"  • {i['name']}: {i['qty']} × {i['price']:,.0f} = {i['total']:,.0f}\n"
        for i in o.get("items", [])
    )
    await cb.message.answer(
        f"📋 <b>Buyurtma #{o['id']}</b>\n"
        f"👤 {o['shop_name']}  📱 {o['phone']}\n"
        f"📊 {st}  📅 {o.get('created_at', '')[:16]}\n\n"
        f"{lines}\n💰 <b>Jami: {o.get('total', 0):,.0f} so'm</b>",
        reply_markup=order_status_kb(oid, o.get("status", "accepted")),
        parse_mode="HTML"
    )
    await cb.answer()


@router.callback_query(F.data.startswith("ostatus_"))
async def update_status(cb: CallbackQuery, bot: Bot):
    # ostatus_<oid>_<status> — status'da "_" bo'lishi mumkin (on_way),
    # shuning uchun faqat 2 ta "_" bo'yicha bo'lamiz.
    parts = cb.data.split("_", 2)
    oid = int(parts[1]); new_st = parts[2]
    await db.update_order_status(oid, new_st)
    o = await db.get_order(oid)
    from bot.config import ORDER_STATUSES
    from bot.keyboards import order_status_kb, order_client_confirm_kb
    st_txt = ORDER_STATUSES.get(new_st, new_st)
    await cb.answer(f"✅ {st_txt}")
    if o and new_st == "delivered":
        tg_id = o.get("client_tg_id")
        if tg_id:
            try:
                await bot.send_message(
                    tg_id,
                    f"📦 <b>Buyurtma #{oid}</b> yetkazib berildi!\nTovarni oldingizmi?",
                    reply_markup=order_client_confirm_kb(oid), parse_mode="HTML"
                )
            except Exception:
                pass
    elif o:
        tg_id = o.get("client_tg_id")
        if tg_id:
            try:
                await bot.send_message(tg_id, f"🔔 Buyurtma #{oid}: <b>{st_txt}</b>", parse_mode="HTML")
            except Exception:
                pass
    await cb.message.edit_reply_markup(reply_markup=order_status_kb(oid, new_st))


# ── Admin tomonidan zakaz ─────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_order_"))
async def admin_order_start(cb: CallbackQuery, state: FSMContext):
    if not await _is_admin(cb.from_user.id):
        return
    if not db.is_client_orders_enabled():
        await cb.answer(
            "ℹ️ 'Mijoz buyurtmalari' funksiyasi o'chirilgan.",
            show_alert=True
        )
        return
    cid = int(cb.data.split("_")[2])
    c = await db.get_client_by_id(cid)
    # Sotuv reytingi bo'yicha, sahifalangan (6/sahifa) — order_page_nav
    # (client.py) bu oqimni ham boshqaradi, chunki OrderStates.browsing umumiy.
    avail = await db.top_selling_products()
    if not avail:
        await cb.message.answer("⚠️ Mahsulot qolmagan.")
        await cb.answer()
        return
    await state.update_data(cart={}, client_id=cid, order_page=0)
    await state.set_state(OrderStates.browsing)
    await cb.message.answer(
        f"🛒 <b>{c['shop_name']}</b> uchun zakaz:",
        reply_markup=client_products_kb(avail, {}, page=0), parse_mode="HTML"
    )
    await cb.answer()
