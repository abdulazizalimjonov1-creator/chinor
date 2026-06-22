from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.filters import CommandStart, CommandObject
import logging

from bot.config import GLAVNIY_ADMIN_ID, MINI_APP_URL
from bot.keyboards import (
    glavniy_menu, admin_menu, client_menu, admins_list_kb, cancel_kb, stats_kb,
    client_products_kb, _price_for, _price_pair_for, settings_kb,
    settings_main_kb, currency_mode_kb, admin_edit_kb, admin_role_pick_kb,
    admin_perms_kb, features_kb, FEATURE_TOGGLES, request_contact_kb,
    mini_app_kb,
)
from bot.states import AddAdminStates, OrderStates, SetUsdRateStates, AuthByPhoneStates
from bot.auth import (
    verify_login as auth_verify_login,
    attempt_tracker, verify_telegram_init_data, LoginAttemptTracker,
)
from bot.config import BOT_TOKEN
from bot.permissions import (
    PERMISSIONS, PERMISSION_ORDER, ROLE_LABELS, ROLE_PRESETS, CURRENCY_MODES,
    effective_permissions, serialize_permissions, parse_permissions,
    has_permission, get_currency_mode, is_glavniy, get_user_menu,
)
from database.channel_db import db, fmt_usd, fmt_sum, usd_to_sum, now_local

logger = logging.getLogger(__name__)
router = Router()


def _is_glavniy(uid: int) -> bool:
    # Markaziy helperga ko'prik
    return is_glavniy(uid)


async def _menu_for(uid: int):
    # Markaziy helperga ko'prik
    return await get_user_menu(db, uid)


async def _client_buy_flow(message: Message, state: FSMContext, pid: int):
    """Kanaldan kelgan mijozni mahsulot uchun zakaz oqimiga ulash."""
    c = await db.get_client_by_tg(message.from_user.id)
    if not c:
        await message.answer(
            "👋 Assalomu alaykum!\n"
            "Bu mahsulotni xarid qilish uchun avval admin sizni "
            "ro'yxatdan o'tkazishi kerak.\n\n"
            "📞 Admin bilan bog'laning."
        )
        return
    p = await db.get_product(pid)
    if not p:
        await message.answer(
            "⚠️ Mahsulot topilmadi yoki o'chirilgan.",
            reply_markup=client_menu()
        )
        return
    if p.get("qty", 0) <= 0:
        await message.answer(
            f"⚠️ <b>{p['name']}</b> hozircha tugagan.",
            reply_markup=client_menu(), parse_mode="HTML"
        )
        return
    ctype = (c.get("client_type") or "dona").lower()
    usd, summ = _price_pair_for(p, ctype)
    unit = p.get("unit", "dona")
    type_label = "🛍️ Donachi" if ctype == "dona" else "📦 Optomchi"
    if usd > 0:
        price_line = f"💰 <b>${usd:,.2f}/{unit}</b>  (≈ {summ:,.0f} so'm/{unit})"
    else:
        price_line = f"💰 {summ:,.0f} so'm/{unit}"
    await state.update_data(
        cart={}, client_id=c["id"], client_type=ctype, order_pid=pid
    )
    await state.set_state(OrderStates.qty)
    text = (
        f"🛒 <b>Sotib olish</b>\n\n"
        f"📦 <b>{p['name']}</b>\n"
        f"{price_line}  ({type_label} narx)\n"
        f"📦 Mavjud: {p.get('qty', 0):g} {unit}\n\n"
        f"Nechta {unit} kerak?"
    )
    if p.get("image_file_id"):
        try:
            await message.answer_photo(
                p["image_file_id"], caption=text,
                reply_markup=cancel_kb(), parse_mode="HTML"
            )
            return
        except Exception:
            pass
    await message.answer(text, reply_markup=cancel_kb(), parse_mode="HTML")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, command: CommandObject):
    uid = message.from_user.id
    payload = (command.args or "").strip()

    # Kanaldan 'Sotib olish' tugmasi orqali kelgan bo'lsa
    # ('Mijoz buyurtmalari' funksiyasi o'chirilgan bo'lsa — e'tiborsiz qoldiramiz)
    if payload.startswith("buy_") and db.is_client_orders_enabled():
        try:
            pid = int(payload.split("_", 1)[1])
        except (ValueError, IndexError):
            pid = 0
        if pid > 0:
            # Adminlar uchun ham ish ko'radi — kerak bo'lsa mahsulot info'sini ko'rsatamiz
            if _is_glavniy(uid) or await db.is_admin(uid):
                p = await db.get_product(pid)
                if p:
                    unit = p.get("unit", "dona")
                    sell_usd = float(p.get("sell_price_usd", 0) or 0)
                    sell_sum = float(p.get("sell_price", 0) or 0)
                    whs_usd = float(p.get("wholesale_price_usd", 0) or 0)
                    whs_sum = float(p.get("wholesale_price", 0) or 0)
                    if sell_usd > 0:
                        sell_line = f"💰 Donada: <b>${sell_usd:,.2f}/{unit}</b>  (≈ {sell_sum:,.0f} so'm/{unit})"
                    else:
                        sell_line = f"💰 Donada: <b>{sell_sum:,.0f} so'm/{unit}</b>"
                    if whs_usd > 0 or whs_sum > 0:
                        if whs_usd > 0:
                            whs_line = f"\n📦 Optom: <b>${whs_usd:,.2f}/{unit}</b>  (≈ {whs_sum:,.0f} so'm)"
                        else:
                            whs_line = f"\n📦 Optom: <b>{whs_sum:,.0f} so'm/{unit}</b>"
                    else:
                        whs_line = ""
                    await message.answer(
                        f"📦 <b>{p['name']}</b>\n"
                        f"{sell_line}"
                        f"{whs_line}\n"
                        f"📦 Qoldi: <b>{p.get('qty', 0):g} {unit}</b>\n"
                        f"🆔 ID: {pid}\n\n"
                        f"<i>Bu admin paneli — mijozlar bu tugma orqali zakaz beradi.</i>",
                        reply_markup=(await _menu_for(uid)),
                        parse_mode="HTML"
                    )
                    return
            # Mijoz uchun zakaz oqimi
            await _client_buy_flow(message, state, pid)
            return

    # Oddiy /start
    if _is_glavniy(uid):
        await message.answer(
            "👑 <b>Bosh admin paneli</b>",
            reply_markup=glavniy_menu(), parse_mode="HTML"
        )
        return
    if await db.is_admin(uid):
        admin = await db.get_admin(uid)
        perms = effective_permissions(admin) if admin else set()
        role_label = ROLE_LABELS.get((admin.get("role") or "full"), "") if admin else ""
        await message.answer(
            f"🔐 <b>Admin paneli</b>\n"
            f"🎭 Rol: {role_label}",
            reply_markup=admin_menu(perms), parse_mode="HTML"
        )
        return
    client = await db.get_client_by_tg(uid)
    if client:
        ctype = (client.get("client_type") or "dona").lower()
        type_label = "🛍️ Donachi" if ctype == "dona" else "📦 Optomchi"
        debt_usd = float(client.get("debt_usd", 0) or 0)
        debt_sum = float(client.get("debt", 0) or 0)
        if debt_usd > 0:
            debt_line = f"💰 Qarz: <b>{fmt_usd(debt_usd)}</b>  (≈ {fmt_sum(debt_sum)})"
        elif debt_sum > 0:
            debt_line = f"💰 Qarz: <b>{fmt_sum(debt_sum)}</b>"
        else:
            debt_line = "✅ Qarz yo'q"
        await message.answer(
            f"👋 <b>{client['shop_name']}</b> xush kelibsiz!\n"
            f"🏷️ Turingiz: <b>{type_label}</b>\n"
            f"{debt_line}",
            reply_markup=client_menu(), parse_mode="HTML"
        )
        return
    # Ro'yxatdan o'tmagan — kontakt orqali avtorizatsiya bosqichiga o'tamiz.
    await state.set_state(AuthByPhoneStates.waiting_contact)
    await message.answer(
        "👋 <b>Assalomu alaykum!</b>\n\n"
        "Tizimga kirish uchun pastdagi <b>«📱 Raqamni yuborish»</b> "
        "tugmasini bosing.\n\n"
        "🔒 <i>Xavfsizlik uchun raqamni qo'lda yozish qabul qilinmaydi — "
        "faqat tugma orqali yuborilgan kontakt tasdiqlanadi.</i>",
        reply_markup=request_contact_kb(), parse_mode="HTML"
    )


# ─── Telefon orqali avtorizatsiya ────────────────────────────────────────────

@router.message(AuthByPhoneStates.waiting_contact, F.contact)
async def auth_by_contact(message: Message, state: FSMContext):
    """Kontakt yuborilganda — egasini tekshirib, telefon bo'yicha mijozni
    topib, Telegram ID ni biriktiradi (avtorizatsiya)."""
    contact = message.contact
    sender_id = message.from_user.id

    # 1) Faqat O'Z kontaktini qabul qilamiz — boshqasiniki rad.
    if contact.user_id and contact.user_id != sender_id:
        await message.answer(
            "🚫 <b>Faqat o'zingizning raqamingizni yuboring.</b>\n"
            "Boshqa odamning kontakti qabul qilinmaydi.\n\n"
            "Iltimos, yana «📱 Raqamni yuborish» tugmasini bosing.",
            reply_markup=request_contact_kb(), parse_mode="HTML"
        )
        return

    phone = (contact.phone_number or "").strip()
    if not phone:
        await message.answer(
            "⚠️ Telegram raqamingizni yuborolmadi. Yana urinib ko'ring.",
            reply_markup=request_contact_kb()
        )
        return

    # 2) Telefon bo'yicha mijozni qidirish (DB normalizatsiya bilan)
    client = await db.get_client_by_phone(phone)
    if not client:
        await message.answer(
            f"❌ <b>Sizning raqamingiz tizimda topilmadi.</b>\n"
            f"📱 Yuborilgan: <code>{phone}</code>\n\n"
            f"Iltimos, admin bilan bog'laning — sizni shu raqam bilan "
            f"tizimga qo'shsin.",
            parse_mode="HTML"
        )
        # State'da qoldiramiz — admin qo'shgach yana tugma bossa avtomatik o'tadi
        return

    # 3) Mijoz allaqachon boshqa Telegram ID ga bog'lab qo'yilganmi?
    cur_tg = client.get("telegram_id")
    if cur_tg and int(cur_tg) != sender_id:
        await message.answer(
            "🚫 <b>Bu telefon raqami allaqachon boshqa Telegram akkauntiga "
            "bog'langan.</b>\n\n"
            "Agar bu xato deb hisoblasangiz — admin bilan bog'laning.",
            parse_mode="HTML"
        )
        return

    # 4) Hammasi OK — Telegram ID ni biriktiramiz (yoki tasdiqlaymiz)
    if not cur_tg:
        ok = await db.set_client_tg_id(client["id"], sender_id)
        if not ok:
            await message.answer(
                "🚫 Avtorizatsiyada xatolik. Admin bilan bog'laning."
            )
            return

    await state.clear()
    ctype = (client.get("client_type") or "dona").lower()
    type_label = "🛍️ Donachi" if ctype == "dona" else "📦 Optomchi"
    debt_usd = float(client.get("debt_usd", 0) or 0)
    debt_sum = float(client.get("debt", 0) or 0)
    if debt_usd > 0:
        debt_line = f"💰 Qarz: <b>{fmt_usd(debt_usd)}</b>  (≈ {fmt_sum(debt_sum)})"
    elif debt_sum > 0:
        debt_line = f"💰 Qarz: <b>{fmt_sum(debt_sum)}</b>"
    else:
        debt_line = "✅ Qarz yo'q"
    await message.answer(
        f"✅ <b>Tizimga kirdingiz!</b>\n\n"
        f"👋 <b>{client['shop_name']}</b> xush kelibsiz!\n"
        f"🏷️ Turingiz: <b>{type_label}</b>\n"
        f"{debt_line}",
        reply_markup=client_menu(), parse_mode="HTML"
    )


@router.message(AuthByPhoneStates.waiting_contact)
async def auth_reject_typed(message: Message):
    """Avtorizatsiya bosqichida har qanday boshqa narsa (matn, rasm, fayl) —
    qabul qilinmaydi. Faqat haqiqiy kontakt orqali kirish mumkin."""
    await message.answer(
        "🔒 <b>Raqamni qo'lda yozish qabul qilinmaydi.</b>\n\n"
        "Iltimos, pastdagi <b>«📱 Raqamni yuborish»</b> tugmasini bosing — "
        "Telegram sizdan tasdiq so'raydi va haqiqiy raqamingizni yuboradi.",
        reply_markup=request_contact_kb(), parse_mode="HTML"
    )


# ── Adminlar ─────────────────────────────────────────────────────────────────

async def _admins_list_text() -> str:
    admins = await db.get_all_admins()
    return (
        f"👥 <b>Adminlar ({len(admins)} ta):</b>\n\n"
        f"Har bir adminga rol/ruxsat/valyuta sozlash uchun ustiga bosing."
    )


@router.message(F.text == "👥 Adminlar")
async def show_admins(message: Message):
    if not _is_glavniy(message.from_user.id):
        return
    admins = await db.get_all_admins()
    await message.answer(
        await _admins_list_text(),
        reply_markup=admins_list_kb(admins, GLAVNIY_ADMIN_ID),
        parse_mode="HTML"
    )


def _admin_card_text(admin: dict) -> str:
    role = (admin.get("role") or "full").strip().lower()
    role_label = ROLE_LABELS.get(role, role)
    perms = effective_permissions(admin)
    perms_list = "\n".join(
        f"  {'✅' if k in perms else '❌'} {PERMISSIONS[k]}"
        for k in PERMISSION_ORDER
    )
    cm = (admin.get("currency_mode") or "").strip().lower()
    cm_label = CURRENCY_MODES.get(cm, "🌐 Global rejim (default)")
    return (
        f"👨‍💼 <b>{admin.get('full_name') or admin['telegram_id']}</b>\n"
        f"🆔 <code>{admin['telegram_id']}</code>\n"
        f"🎭 Rol: <b>{role_label}</b>\n"
        f"💱 Valyuta rejimi: <b>{cm_label}</b>\n\n"
        f"<b>Ruxsatlar:</b>\n{perms_list}"
    )


@router.callback_query(F.data.startswith("adm_open_"))
async def open_admin_card(cb: CallbackQuery):
    if not _is_glavniy(cb.from_user.id):
        await cb.answer("Faqat bosh admin uchun.", show_alert=True)
        return
    try:
        tg = int(cb.data.rsplit("_", 1)[1])
    except (ValueError, IndexError):
        await cb.answer()
        return
    admin = await db.get_admin(tg)
    if not admin:
        await cb.answer("Topilmadi.", show_alert=True)
        return
    try:
        await cb.message.edit_text(
            _admin_card_text(admin),
            reply_markup=admin_edit_kb(admin, GLAVNIY_ADMIN_ID),
            parse_mode="HTML"
        )
    except Exception:
        await cb.message.answer(
            _admin_card_text(admin),
            reply_markup=admin_edit_kb(admin, GLAVNIY_ADMIN_ID),
            parse_mode="HTML"
        )
    await cb.answer()


@router.callback_query(F.data == "adm_back")
async def back_to_admins_list(cb: CallbackQuery):
    if not _is_glavniy(cb.from_user.id):
        await cb.answer()
        return
    admins = await db.get_all_admins()
    try:
        await cb.message.edit_text(
            await _admins_list_text(),
            reply_markup=admins_list_kb(admins, GLAVNIY_ADMIN_ID),
            parse_mode="HTML"
        )
    except Exception:
        await cb.message.answer(
            await _admins_list_text(),
            reply_markup=admins_list_kb(admins, GLAVNIY_ADMIN_ID),
            parse_mode="HTML"
        )
    await cb.answer()


# ── Rol tanlash ──────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm_role_"))
async def open_role_picker(cb: CallbackQuery):
    if not _is_glavniy(cb.from_user.id):
        await cb.answer()
        return
    try:
        tg = int(cb.data.rsplit("_", 1)[1])
    except (ValueError, IndexError):
        await cb.answer()
        return
    admin = await db.get_admin(tg)
    if not admin:
        await cb.answer("Topilmadi.", show_alert=True)
        return
    role = (admin.get("role") or "full").strip().lower()
    try:
        await cb.message.edit_text(
            "🎭 <b>Rol tanlang:</b>\n\n"
            "• <b>To'liq admin</b> — hammasi\n"
            "• <b>Mahsulotchi</b> — faqat mahsulot va prixod\n"
            "• <b>Kassir</b> — faqat sotuv va to'lov\n"
            "• <b>Statistikachi</b> — faqat ko'rish va eksport\n"
            "• <b>Maxsus</b> — har bir ruxsatni qo'lda belgilash",
            reply_markup=admin_role_pick_kb(tg, role),
            parse_mode="HTML"
        )
    except Exception:
        pass
    await cb.answer()


@router.callback_query(F.data.startswith("adm_setrole_"))
async def set_admin_role_cb(cb: CallbackQuery):
    if not _is_glavniy(cb.from_user.id):
        await cb.answer()
        return
    parts = cb.data.split("_")
    # adm_setrole_<tg>_<role>
    try:
        tg = int(parts[2])
        role = parts[3]
    except (ValueError, IndexError):
        await cb.answer()
        return
    if role not in ROLE_LABELS:
        await cb.answer("Noma'lum rol.", show_alert=True)
        return
    await db.set_admin_role(tg, role)
    # 'custom' tanlansa va hozircha permissions bo'sh bo'lsa — joriy effective'ni
    # JSON ga saqlab qo'yamiz (foydalanuvchi keyin tahrirlasin uchun)
    if role == "custom":
        admin = await db.get_admin(tg)
        existing = parse_permissions(admin.get("permissions") if admin else "")
        if not existing:
            # bo'sh — kassir presetidan boshlaymiz (xavfsizroq)
            await db.set_admin_permissions(tg, serialize_permissions(ROLE_PRESETS["cashier"]))
    admin = await db.get_admin(tg)
    try:
        await cb.message.edit_text(
            _admin_card_text(admin),
            reply_markup=admin_edit_kb(admin, GLAVNIY_ADMIN_ID),
            parse_mode="HTML"
        )
    except Exception:
        pass
    await cb.answer(f"✅ Rol: {ROLE_LABELS[role]}")


# ── Maxsus ruxsatlar (checkbox) ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm_perms_"))
async def open_perms_editor(cb: CallbackQuery):
    if not _is_glavniy(cb.from_user.id):
        await cb.answer()
        return
    try:
        tg = int(cb.data.rsplit("_", 1)[1])
    except (ValueError, IndexError):
        await cb.answer()
        return
    admin = await db.get_admin(tg)
    if not admin:
        await cb.answer("Topilmadi.", show_alert=True)
        return
    # Agar role 'custom' bo'lmasa — avval shu rol presetini permissions ga ko'chirib,
    # role ni 'custom' ga o'tkazamiz, shunda checkbox tahrirlash mantiqiy bo'ladi.
    role = (admin.get("role") or "full").strip().lower()
    if role != "custom":
        preset = ROLE_PRESETS.get(role, ROLE_PRESETS["full"])
        await db.set_admin_permissions(tg, serialize_permissions(preset))
        await db.set_admin_role(tg, "custom")
        admin = await db.get_admin(tg)
    granted = effective_permissions(admin)
    try:
        await cb.message.edit_text(
            f"🛠 <b>{admin.get('full_name')}</b> — ruxsatlar:\n\n"
            f"Har bir tugmani bosib yoqing/o'chiring.",
            reply_markup=admin_perms_kb(tg, granted),
            parse_mode="HTML"
        )
    except Exception:
        pass
    await cb.answer()


@router.callback_query(F.data.startswith("adm_togp_"))
async def toggle_perm(cb: CallbackQuery):
    if not _is_glavniy(cb.from_user.id):
        await cb.answer()
        return
    # adm_togp_<tg>_<perm_key> — lekin perm_key da '_' bo'lishi mumkin
    rest = cb.data[len("adm_togp_"):]
    try:
        tg_str, perm_key = rest.split("_", 1)
        tg = int(tg_str)
    except (ValueError, IndexError):
        await cb.answer()
        return
    if perm_key not in PERMISSIONS:
        await cb.answer("Noma'lum ruxsat.", show_alert=True)
        return
    admin = await db.get_admin(tg)
    if not admin:
        await cb.answer("Topilmadi.", show_alert=True)
        return
    granted = effective_permissions(admin)
    if perm_key in granted:
        granted.discard(perm_key)
    else:
        granted.add(perm_key)
    await db.set_admin_permissions(tg, serialize_permissions(granted))
    await db.set_admin_role(tg, "custom")
    try:
        await cb.message.edit_reply_markup(reply_markup=admin_perms_kb(tg, granted))
    except Exception:
        pass
    await cb.answer("✅")


@router.callback_query(F.data.startswith("adm_pall_"))
async def perms_all_toggle(cb: CallbackQuery):
    if not _is_glavniy(cb.from_user.id):
        await cb.answer()
        return
    # adm_pall_<tg>_on|off
    parts = cb.data.split("_")
    try:
        tg = int(parts[2])
        action = parts[3]
    except (ValueError, IndexError):
        await cb.answer()
        return
    granted = set(PERMISSIONS.keys()) if action == "on" else set()
    await db.set_admin_permissions(tg, serialize_permissions(granted))
    await db.set_admin_role(tg, "custom")
    try:
        await cb.message.edit_reply_markup(reply_markup=admin_perms_kb(tg, granted))
    except Exception:
        pass
    await cb.answer("✅ Hammasi yoqildi" if action == "on" else "✅ Hammasi o'chirildi")


# ── Adminga valyuta rejimi override ──────────────────────────────────────────

@router.callback_query(F.data.startswith("adm_curmode_"))
async def open_admin_curmode(cb: CallbackQuery):
    if not _is_glavniy(cb.from_user.id):
        await cb.answer()
        return
    try:
        tg = int(cb.data.rsplit("_", 1)[1])
    except (ValueError, IndexError):
        await cb.answer()
        return
    admin = await db.get_admin(tg)
    if not admin:
        await cb.answer("Topilmadi.", show_alert=True)
        return
    current = (admin.get("currency_mode") or "").strip().lower()
    try:
        await cb.message.edit_text(
            f"💱 <b>{admin.get('full_name')}</b> uchun valyuta rejimi:\n\n"
            f"🌐 <b>Global</b> — bosh admin sozlamasidan olinadi\n"
            f"💱 <b>Aralash</b> — USD va so'm birga ko'rinadi\n"
            f"🇺🇿 <b>Faqat so'm</b> — USD displeyi yashirin\n"
            f"🇺🇸 <b>Faqat USD</b> — so'm displeyi yashirin",
            reply_markup=currency_mode_kb(current, target=f"admin_{tg}"),
            parse_mode="HTML"
        )
    except Exception:
        pass
    await cb.answer()


@router.callback_query(F.data.startswith("curset_admin_"))
async def set_admin_curmode(cb: CallbackQuery):
    if not _is_glavniy(cb.from_user.id):
        await cb.answer()
        return
    # curset_admin_<tg>_<mode> — mode'da "_" bo'lishi mumkin (uzs_only/usd_only),
    # shuning uchun faqat 3 ta "_" bo'yicha bo'lamiz.
    parts = cb.data.split("_", 3)
    try:
        tg = int(parts[2])
        mode = parts[3]
    except (ValueError, IndexError):
        await cb.answer()
        return
    if mode == "default":
        await db.set_admin_currency_mode(tg, "")
    elif mode in ("hybrid", "uzs_only", "usd_only"):
        await db.set_admin_currency_mode(tg, mode)
    else:
        await cb.answer("Noma'lum rejim.", show_alert=True)
        return
    admin = await db.get_admin(tg)
    try:
        await cb.message.edit_text(
            _admin_card_text(admin),
            reply_markup=admin_edit_kb(admin, GLAVNIY_ADMIN_ID),
            parse_mode="HTML"
        )
    except Exception:
        pass
    await cb.answer("✅ Saqlandi")


@router.callback_query(F.data == "curset_back")
async def curset_back(cb: CallbackQuery):
    # Bu callback foydalanuvchi qaerdan kelganini eslamaydi —
    # shunchaki adminlar ro'yxatiga qaytaramiz.
    if not _is_glavniy(cb.from_user.id):
        await cb.answer()
        return
    admins = await db.get_all_admins()
    try:
        await cb.message.edit_text(
            await _admins_list_text(),
            reply_markup=admins_list_kb(admins, GLAVNIY_ADMIN_ID),
            parse_mode="HTML"
        )
    except Exception:
        pass
    await cb.answer()


@router.callback_query(F.data == "add_admin")
async def add_admin_start(cb: CallbackQuery, state: FSMContext):
    if not _is_glavniy(cb.from_user.id):
        return
    await cb.message.answer(
        "Yangi admin Telegram ID sini kiriting:\n💡 @userinfobot dan olsa bo'ladi",
        reply_markup=cancel_kb()
    )
    await state.set_state(AddAdminStates.tg_id)
    await cb.answer()


@router.message(AddAdminStates.tg_id)
async def add_admin_id(message: Message, state: FSMContext, bot: Bot):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor.", reply_markup=glavniy_menu())
        return
    try:
        new_id = int(message.text.strip())
    except ValueError:
        await message.answer("⚠️ Faqat raqam kiriting:")
        return
    try:
        chat = await bot.get_chat(new_id)
        full_name = chat.full_name or str(new_id)
    except Exception:
        full_name = str(new_id)
    ok = await db.add_admin(new_id, full_name, message.from_user.id)
    await state.clear()
    if ok:
        await message.answer(
            f"✅ <b>{full_name}</b> admin qo'shildi!\n<code>{new_id}</code>",
            reply_markup=glavniy_menu(), parse_mode="HTML"
        )
    else:
        await message.answer("⚠️ Allaqachon mavjud.", reply_markup=glavniy_menu())


@router.callback_query(F.data.startswith("del_admin_"))
async def del_admin(cb: CallbackQuery):
    if not _is_glavniy(cb.from_user.id):
        return
    try:
        tg_id = int(cb.data.split("_")[2])
    except (ValueError, IndexError):
        await cb.answer()
        return
    if tg_id == GLAVNIY_ADMIN_ID:
        await cb.answer("⛔ Bosh adminni o'chirib bo'lmaydi.", show_alert=True)
        return
    await db.remove_admin(tg_id)
    await cb.answer("✅ O'chirildi!")
    admins = await db.get_all_admins()
    try:
        await cb.message.edit_text(
            await _admins_list_text(),
            reply_markup=admins_list_kb(admins, GLAVNIY_ADMIN_ID),
            parse_mode="HTML"
        )
    except Exception:
        await cb.message.edit_reply_markup(
            reply_markup=admins_list_kb(admins, GLAVNIY_ADMIN_ID)
        )


# ── Statistika ────────────────────────────────────────────────────────────────

@router.message(F.text == "📊 Statistika")
async def show_stats_menu(message: Message):
    uid = message.from_user.id
    if not (_is_glavniy(uid) or await db.is_admin(uid)):
        return
    if not await has_permission(db, uid, "stats"):
        await message.answer("⛔ Sizda statistika ko'rish ruxsati yo'q.")
        return
    await message.answer("📊 Davrni tanlang:", reply_markup=stats_kb())


# ── USD kursi ─────────────────────────────────────────────────────────────────

@router.message(F.text == "💱 USD kursi")
async def usd_rate_menu(message: Message, state: FSMContext):
    uid = message.from_user.id
    if not _is_glavniy(uid):
        if not await has_permission(db, uid, "usd_rate"):
            await message.answer("⛔ Sizda bu amal uchun ruxsat yo'q.")
            return
    rate = db.get_usd_rate()
    await message.answer(
        f"💱 <b>Joriy USD kursi:</b> 1$ = <b>{rate:,.2f} so'm</b>\n\n"
        "Yangi kursni kiriting (so'mda):\n"
        "Masalan: <code>12500</code> yoki <code>12750.50</code>\n\n"
        "💡 Kurs o'zgarganda barcha mahsulotlarning so'm narxlari avtomatik qayta hisoblanadi.\n"
        "Eskidan saqlangan sotuv cheklari esa o'sha vaqtdagi kurs bo'yicha qoladi.",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )
    await state.set_state(SetUsdRateStates.waiting)


@router.message(SetUsdRateStates.waiting)
async def usd_rate_set(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        uid = message.from_user.id
        menu = await _menu_for(uid)
        await message.answer("Bekor.", reply_markup=menu)
        return
    txt = (message.text or "").strip().replace(" ", "").replace(",", ".")
    try:
        new_rate = float(txt)
        if new_rate <= 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Musbat raqam kiriting (masalan: 12500):")
        return
    db.set_usd_rate(new_rate)
    await db.reprice_all_with_rate(new_rate)
    await state.clear()
    uid = message.from_user.id
    menu = await _menu_for(uid)
    await message.answer(
        f"✅ Yangi kurs saqlandi: <b>1$ = {new_rate:,.2f} so'm</b>\n"
        f"📦 Barcha mahsulotlarning so'mdagi narxlari qayta hisoblandi.",
        reply_markup=menu, parse_mode="HTML"
    )


def _pair_money(sum_val: float, usd_val: float) -> str:
    sum_val = float(sum_val or 0)
    usd_val = float(usd_val or 0)
    if usd_val > 0:
        return f"<b>{fmt_usd(usd_val)}</b>  (≈ {fmt_sum(sum_val)})"
    return f"<b>{fmt_sum(sum_val)}</b>"


@router.message(F.text == "💰 Umumiy foyda")
async def total_profit(message: Message):
    if not _is_glavniy(message.from_user.id):
        return
    month = now_local().strftime("%Y-%m")
    ms = await db.stats_month(month)
    all_st = await db.stats_all_time()
    rate = db.get_usd_rate()
    await message.answer(
        f"💰 <b>Foyda hisoboti</b>\n"
        f"💱 Kurs: 1$ = {rate:,.0f} so'm\n\n"
        f"📆 <b>Bu oy ({month}):</b>\n"
        f"  🧾 Kassa sotuvi: {ms['sale_count']} ta\n"
        f"  🚚 Buyurtmalar: {ms['order_count']} ta\n"
        f"  💵 Tushum: {_pair_money(ms.get('revenue',0), ms.get('revenue_usd',0))}\n"
        f"  📦 Xarajat: {_pair_money(ms.get('cost',0), ms.get('cost_usd',0))}\n"
        f"  ✅ Foyda: {_pair_money(ms.get('profit',0), ms.get('profit_usd',0))}\n\n"
        f"📈 <b>Jami barcha vaqt:</b>\n"
        f"  💵 Tushum: {_pair_money(all_st.get('revenue',0), all_st.get('revenue_usd',0))}\n"
        f"  📦 Xarajat: {_pair_money(all_st.get('cost',0), all_st.get('cost_usd',0))}\n"
        f"  ✅ Foyda: {_pair_money(all_st.get('profit',0), all_st.get('profit_usd',0))}",
        parse_mode="HTML"
    )


# ── Sozlamalar ────────────────────────────────────────────────────────────────

def _settings_text(dona_enabled: bool, whs_enabled: bool) -> str:
    dona_status = "✅ Yoqilgan" if dona_enabled else "❌ O'chirilgan"
    whs_status = "✅ Yoqilgan" if whs_enabled else "❌ O'chirilgan"
    if dona_enabled and whs_enabled:
        mode_note = (
            "Hozir: <b>ikkalasi ham yoqilgan</b> — sotuv mijoz turiga qarab "
            "(donachi/optomchi) avtomatik tanlanadi."
        )
    elif dona_enabled and not whs_enabled:
        mode_note = "Hozir: faqat <b>dona narx</b>da sotiladi (barcha mijozlarga)."
    elif whs_enabled and not dona_enabled:
        mode_note = "Hozir: faqat <b>optom narx</b>da sotiladi (barcha mijozlarga)."
    else:
        mode_note = (
            "⚠️ Ikkalasi ham o'chirilgan! Bu holatda tizim <b>dona narx</b>ga "
            "qaytadi. Kamida bittasini yoqing."
        )
    return (
        f"⚙️ <b>Sozlamalar</b>\n\n"
        f"🛍️ <b>Dona narx</b>: {dona_status}\n"
        f"📦 <b>Optom narx</b>: {whs_status}\n\n"
        f"<i>{mode_note}\n\n"
        f"💡 Narxlar bazada saqlanib qoladi — istalgan vaqtda qaytarib yoqsangiz, "
        f"avvalgi qiymatlari bilan ishlatiladi.</i>"
    )


@router.message(F.text == "⚙️ Sozlamalar")
async def show_settings(message: Message):
    if not _is_glavniy(message.from_user.id):
        return
    cur_mode = db.get_currency_mode_global()
    await message.answer(
        "⚙️ <b>Sozlamalar</b>\n\n"
        "Quyidagidan tanlang:",
        reply_markup=settings_main_kb(cur_mode),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "settings_prices")
async def open_price_settings(cb: CallbackQuery):
    if not _is_glavniy(cb.from_user.id):
        await cb.answer()
        return
    dona_on = db.is_dona_enabled()
    whs_on = db.is_wholesale_enabled()
    try:
        await cb.message.edit_text(
            _settings_text(dona_on, whs_on),
            reply_markup=settings_kb(dona_on, whs_on),
            parse_mode="HTML"
        )
    except Exception:
        await cb.message.answer(
            _settings_text(dona_on, whs_on),
            reply_markup=settings_kb(dona_on, whs_on),
            parse_mode="HTML"
        )
    await cb.answer()


@router.callback_query(F.data == "settings_currency")
async def open_currency_settings(cb: CallbackQuery):
    if not _is_glavniy(cb.from_user.id):
        await cb.answer()
        return
    cur = db.get_currency_mode_global()
    try:
        await cb.message.edit_text(
            "💱 <b>Umumiy valyuta rejimi</b>\n\n"
            "Bu rejim barcha adminlar (override qilinmaganlar) uchun amal qiladi:\n\n"
            "• <b>Aralash</b> — USD va so'm birga ko'rinadi (hozirgi standart)\n"
            "• <b>Faqat so'm</b> — USD displeyi va inputi yashirin bo'ladi\n"
            "• <b>Faqat USD</b> — so'm displeyi va inputi yashirin bo'ladi\n\n"
            "💡 Alohida adminlar uchun 'Adminlar' menyusidan override qilish mumkin.",
            reply_markup=currency_mode_kb(cur, target="global"),
            parse_mode="HTML"
        )
    except Exception:
        pass
    await cb.answer()


@router.callback_query(F.data.startswith("curset_global_"))
async def set_global_currency(cb: CallbackQuery):
    if not _is_glavniy(cb.from_user.id):
        await cb.answer()
        return
    mode = cb.data[len("curset_global_"):]
    if mode not in ("hybrid", "uzs_only", "usd_only"):
        await cb.answer()
        return
    db.set_currency_mode_global(mode)
    cur = db.get_currency_mode_global()
    label = CURRENCY_MODES.get(cur, cur)
    try:
        await cb.message.edit_text(
            "💱 <b>Umumiy valyuta rejimi</b>\n\n"
            f"Hozirgi: <b>{label}</b>",
            reply_markup=currency_mode_kb(cur, target="global"),
            parse_mode="HTML"
        )
    except Exception:
        pass
    await cb.answer(f"✅ {label}")


# ── Universal funksiya kalitlari ─────────────────────────────────────────────

def _feature_states() -> dict:
    """Barcha funksiya kalitlarining joriy holati."""
    return {
        "barcode":       db.is_barcode_enabled(),
        "channel":       db.is_channel_enabled(),
        "client_orders": db.is_client_orders_enabled(),
        "nasiya":        db.is_nasiya_enabled(),
        "categories":    db.is_categories_enabled(),
        "suppliers":     db.is_suppliers_enabled(),
        "cat_filter":    db.is_cat_filter_enabled(),
        "cart_edit":     db.is_cart_edit_enabled(),
        "quick_restock": db.is_quick_restock_enabled(),
        "quick_prixod":  db.is_quick_prixod_enabled(),
        "ai_analytics":  db.is_ai_analytics_enabled(),
        "client_search": db.is_client_search_enabled(),
        "ai_consult":    db.is_ai_consult_enabled(),
        "mini_app":      db.is_mini_app_enabled(),
    }


_FEATURE_SETTERS = {
    "barcode":       db.set_barcode_enabled,
    "channel":       db.set_channel_enabled,
    "client_orders": db.set_client_orders_enabled,
    "nasiya":        db.set_nasiya_enabled,
    "categories":    db.set_categories_enabled,
    "suppliers":     db.set_suppliers_enabled,
    "cat_filter":    db.set_cat_filter_enabled,
    "cart_edit":     db.set_cart_edit_enabled,
    "quick_restock": db.set_quick_restock_enabled,
    "quick_prixod":  db.set_quick_prixod_enabled,
    "ai_analytics":  db.set_ai_analytics_enabled,
    "client_search": db.set_client_search_enabled,
    "ai_consult":    db.set_ai_consult_enabled,
    "mini_app":      db.set_mini_app_enabled,
}


def _features_text() -> str:
    return (
        "🧩 <b>Funksiyalar</b>\n\n"
        "Do'koningizga keraksiz funksiyalarni o'chirib qo'yishingiz mumkin — "
        "ular bot interfeysidan butunlay yo'qoladi.\n\n"
        "• <b>Shtrix-kod skaneri</b> — mahsulot qo'shishda shtrix-kod bosqichi\n"
        "• <b>Kanalga e'lon</b> — mahsulot rasmi + 'Sotib olish' tugmasi kanalga\n"
        "• <b>Mijoz buyurtmalari</b> — mijozlar bot orqali zakaz berishi\n"
        "• <b>Nasiya</b> — kassada qarzga sotuv\n"
        "• <b>Kategoriyalar</b> — mahsulotlarni guruhlash bo'limi\n"
        "• <b>Yetkazib beruvchilar</b> — yetkazib beruvchilar bo'limi, zakaz/prixod\n"
        "• <b>Kategoriya filtri</b> — ro'yxatlarni kategoriya bo'yicha saralash\n"
        "• <b>Savatni tahrirlash</b> — kassada savat qatorlarini o'zgartirish\n"
        "• <b>Tezkor to'ldirish</b> — past qoldiq ro'yxatidan darrov prixod\n"
        "• <b>Tezkor prixod</b> — yetkazib beruvchidan bir necha mahsulotga prixod\n"
        "• <b>AI Analitika</b> — Google Gemini orqali biznes-tahlil va maslahat\n"
        "• <b>Mijoz qidiruvi</b> — mijozlar bot orqali tovar topishi (DB+AI)\n"
        "• <b>AI sotuvchi-konsultant</b> — mijoz murakkab savol berib, AI dan "
        "hisob va tovar tavsiyasini olishi\n\n"
        "<i>Tugmani bosib yoqing yoki o'chiring.</i>"
    )


@router.callback_query(F.data == "settings_features")
async def open_features_settings(cb: CallbackQuery):
    if not _is_glavniy(cb.from_user.id):
        await cb.answer()
        return
    try:
        await cb.message.edit_text(
            _features_text(),
            reply_markup=features_kb(_feature_states()),
            parse_mode="HTML"
        )
    except Exception:
        await cb.message.answer(
            _features_text(),
            reply_markup=features_kb(_feature_states()),
            parse_mode="HTML"
        )
    await cb.answer()


@router.callback_query(F.data.startswith("feat_toggle_"))
async def toggle_feature(cb: CallbackQuery):
    if not _is_glavniy(cb.from_user.id):
        await cb.answer("❌ Faqat bosh admin uchun!", show_alert=True)
        return
    key = cb.data[len("feat_toggle_"):]
    setter = _FEATURE_SETTERS.get(key)
    if not setter:
        await cb.answer()
        return
    states = _feature_states()
    new_val = not states.get(key, True)
    setter(new_val)
    try:
        await cb.message.edit_reply_markup(reply_markup=features_kb(_feature_states()))
    except Exception:
        pass
    name = dict(FEATURE_TOGGLES).get(key, key)
    await cb.answer(f"{'✅ yoqildi' if new_val else '❌ o`chirildi'}: {name}")


@router.callback_query(F.data == "settings_back")
async def settings_back(cb: CallbackQuery):
    """Funksiyalar bo'limidan sozlamalar bosh menyusiga qaytish."""
    if not _is_glavniy(cb.from_user.id):
        await cb.answer()
        return
    cur_mode = db.get_currency_mode_global()
    try:
        await cb.message.edit_text(
            "⚙️ <b>Sozlamalar</b>\n\nQuyidagidan tanlang:",
            reply_markup=settings_main_kb(cur_mode),
            parse_mode="HTML"
        )
    except Exception:
        pass
    await cb.answer()


async def _refresh_settings_view(cb: CallbackQuery):
    dona_on = db.is_dona_enabled()
    whs_on = db.is_wholesale_enabled()
    try:
        await cb.message.edit_text(
            _settings_text(dona_on, whs_on),
            reply_markup=settings_kb(dona_on, whs_on),
            parse_mode="HTML"
        )
    except Exception:
        pass


@router.callback_query(F.data == "settings_toggle_wholesale")
async def toggle_wholesale(cb: CallbackQuery):
    if not _is_glavniy(cb.from_user.id):
        await cb.answer("❌ Faqat bosh admin uchun!", show_alert=True)
        return
    current = db.is_wholesale_enabled()
    # Ikkalasini ham o'chirib qo'yishni bloklash
    if current and not db.is_dona_enabled():
        await cb.answer(
            "⚠️ Kamida bittasi yoqilgan bo'lishi kerak! "
            "Avval dona narxni yoqing.",
            show_alert=True
        )
        return
    db.set_wholesale_enabled(not current)
    await _refresh_settings_view(cb)
    await cb.answer("✅ Optom narx yoqildi!" if not current else "✅ Optom narx o'chirildi!")


@router.callback_query(F.data == "settings_toggle_dona")
async def toggle_dona(cb: CallbackQuery):
    if not _is_glavniy(cb.from_user.id):
        await cb.answer("❌ Faqat bosh admin uchun!", show_alert=True)
        return
    current = db.is_dona_enabled()
    # Ikkalasini ham o'chirib qo'yishni bloklash
    if current and not db.is_wholesale_enabled():
        await cb.answer(
            "⚠️ Kamida bittasi yoqilgan bo'lishi kerak! "
            "Avval optom narxni yoqing.",
            show_alert=True
        )
        return
    db.set_dona_enabled(not current)
    await _refresh_settings_view(cb)
    await cb.answer("✅ Dona narx yoqildi!" if not current else "✅ Dona narx o'chirildi!")


@router.callback_query(F.data == "settings_noop")
async def settings_noop(cb: CallbackQuery):
    await cb.answer()


@router.message(F.text == "🏆 Reyting")
async def top_clients(message: Message):
    if not _is_glavniy(message.from_user.id):
        return
    clients = await db.top_clients(10)
    text = "🏆 <b>Top-10 mijozlar:</b>\n\n"
    medals = ["🥇", "🥈", "🥉"]
    for i, c in enumerate(clients, 1):
        m = medals[i-1] if i <= 3 else f"{i}."
        debt_usd = float(c.get("debt_usd", 0) or 0)
        debt_sum = float(c.get("debt", 0) or 0)
        if debt_usd > 0:
            debt_part = f"{fmt_usd(debt_usd)} (≈ {fmt_sum(debt_sum)})"
        elif debt_sum > 0:
            debt_part = fmt_sum(debt_sum)
        else:
            debt_part = "yo'q"
        text += (
            f"{m} <b>{c['shop_name']}</b>\n"
            f"   💵 {c['total_spent']:,.0f} so'm  🛒 {c['order_count']} ta\n"
            f"   💰 Qarz: {debt_part}\n\n"
        )
    await message.answer(text, parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════════════════
#  🌐 Mini App — WebApp tugmasi + tg.sendData qabul qilish
# ═══════════════════════════════════════════════════════════════════════════

def _mini_app_available() -> bool:
    return bool(MINI_APP_URL) and db.is_mini_app_enabled()


@router.message(F.text == "🌐 Mini App")
async def open_mini_app(message: Message):
    """Foydalanuvchi menyudan '🌐 Mini App' bossa — Inline tugma orqali ochish."""
    if not _mini_app_available():
        await message.answer(
            "ℹ️ Mini App hozircha sozlanmagan.",
            reply_markup=await get_user_menu(db, message.from_user.id)
        )
        return
    uid = message.from_user.id
    # Login bormi tekshirib taklif beramiz
    has_creds = False
    if is_glavniy(uid) or await db.is_admin(uid):
        has_creds = await db.admin_has_credentials(uid)
    else:
        c = await db.get_client_by_tg(uid)
        if c:
            has_creds = await db.client_has_credentials(c["id"])
    note = (
        "✅ Sizda login/parol bor — to'g'ridan-to'g'ri kira olasiz."
        if has_creds else
        "ℹ️ Sizda hali login yo'q. Avval menyudan «🔑 Login/parol» bosib yarating."
    )
    await message.answer(
        f"🌐 <b>Mini App'ni ochish</b>\n\n{note}",
        reply_markup=mini_app_kb(MINI_APP_URL), parse_mode="HTML"
    )


async def _notify_glavniy(bot: Bot, text: str):
    """Bosh adminga xavfsizlik xabari yuboradi (yengil — silent fail)."""
    try:
        await bot.send_message(GLAVNIY_ADMIN_ID, text, parse_mode="HTML")
    except Exception:
        pass


@router.message(F.web_app_data)
async def on_web_app_data(message: Message, state: FSMContext, bot: Bot):
    """Mini App'dan tg.sendData() bilan kelgan ma'lumotni qabul qiladi.

    Xavfsizlik:
      • Sender'ning Telegram ID si: `message.from_user.id` — Telegram bilan
        kafolatlangan (sendData orqali kelgan service message).
      • Rate limit: 5 noto'g'ri urinish / 15 daqiqa → 30 daqiqaga blok.
      • Strict binding: agar hisobda allaqachon boshqa tg_id bog'langan
        bo'lsa — rad. Agar tg_id bo'sh bo'lsa — birinchi muvaffaqiyatli
        loginda biriktirib qo'yamiz.
      • Generic xato: 'login yo'q' va 'parol noto'g'ri' alohida ko'rsatilmaydi.
      • Bosh admin'ga shubhali aktivlik (lockout, ID nomos kelganda) xabar.
    """
    import json as _json
    raw = message.web_app_data.data if message.web_app_data else ""
    sender_tg = message.from_user.id
    sender_name = message.from_user.full_name or ""

    payload = None
    try:
        payload = _json.loads(raw)
    except Exception:
        payload = None

    if not (isinstance(payload, dict) and payload.get("type") == "login"):
        # Login emas — boshqa harakat (kelajak uchun ochiq)
        await message.answer(
            f"📨 Mini App'dan ma'lumot keldi:\n<code>{raw[:300]}</code>",
            parse_mode="HTML"
        )
        return

    await state.clear()

    # ── 1) Rate limit tekshiruv ────────────────────────────────────────────
    locked, remaining = attempt_tracker.is_locked(sender_tg)
    if locked:
        mins = max(1, remaining // 60)
        await message.answer(
            "🚫 <b>Juda ko'p noto'g'ri urinish.</b>\n\n"
            f"Hisobingiz xavfsizlik uchun vaqtinchalik bloklandi. "
            f"Qaytadan urinishga <b>{mins} daqiqa</b> kuting.",
            parse_mode="HTML"
        )
        return

    # ── 2) Maydonlarni o'qish va lokal qoidalarga tekshirish ───────────────
    login_in = (payload.get("login") or "").strip()
    password_in = (payload.get("password") or "").strip()   # bo'sh joylar zararsiz
    if not login_in or not password_in:
        await message.answer(
            "⚠️ Login yoki parol bo'sh — qaytadan kiriting."
        )
        return
    if len(login_in) > 64 or len(password_in) > 128:
        await message.answer("⚠️ Maydon juda uzun.")
        return

    # ── 3) Asosiy tekshiruv: DB dan hisobni qidirish va parolni solishtirish
    result = await auth_verify_login(db, login_in, password_in)

    def _record_fail():
        count, just_locked = attempt_tracker.record_failure(sender_tg)
        return count, just_locked

    if not result:
        count, just_locked = _record_fail()
        msg = (
            "❌ <b>Login yoki parol noto'g'ri.</b>\n\n"
            "Iltimos, qaytadan urinib ko'ring."
        )
        if just_locked:
            msg += (
                f"\n\n🚫 Juda ko'p noto'g'ri urinish — hisob "
                f"30 daqiqaga bloklandi."
            )
            await _notify_glavniy(
                bot,
                f"🚨 <b>Shubhali login urinishlari</b>\n"
                f"👤 TG ID: <code>{sender_tg}</code> ({sender_name})\n"
                f"🔑 Login: <code>{login_in}</code>\n"
                f"❗ 5 noto'g'ri urinish — 30 daqiqaga blokirovka qo'yildi."
            )
        elif count >= 3:
            msg += (
                f"\n\nDiqqat: yana <b>{LoginAttemptTracker.LOCK_THRESHOLD - count}</b> ta "
                f"noto'g'ri urinishdan keyin hisob bloklanadi."
            )
        await message.answer(msg, parse_mode="HTML")
        return

    # ── 4) Strict telegram_id binding ──────────────────────────────────────
    user = result["user"]
    role = result["role"]
    bound_tg = user.get("telegram_id") or 0
    if bound_tg and int(bound_tg) != int(sender_tg):
        # Boshqa Telegram akkauntdan kirishga urinish — qattiq rad
        attempt_tracker.record_failure(sender_tg)
        await message.answer(
            "🚫 <b>Bu hisob boshqa Telegram akkauntiga biriktirilgan.</b>\n\n"
            "Agar bu sizning hisobingiz deb hisoblasangiz — admin bilan "
            "bog'laning. Boshqacha hech kim sizning login/parolingizdan "
            "foydalana olmaydi.",
            parse_mode="HTML"
        )
        await _notify_glavniy(
            bot,
            f"🚨 <b>Boshqa TG ID dan login urinishi</b>\n"
            f"🔑 Login: <code>{login_in}</code>\n"
            f"📛 Hisobdagi TG: <code>{bound_tg}</code>\n"
            f"❗ Kirishga urindi: <code>{sender_tg}</code> ({sender_name})"
        )
        return

    # Bog'lanmagan mijoz bo'lsa — birinchi muvaffaqiyatli kirishda biriktiramiz
    if role == "client" and not bound_tg:
        try:
            await db.set_client_tg_id(user["id"], sender_tg)
        except Exception as e:
            logger.error(f"set_client_tg_id failed: user_id={user['id']}, tg_id={sender_tg}, error={e}")

    # ── 5) Muvaffaqiyatli login ────────────────────────────────────────────
    attempt_tracker.clear(sender_tg)
    menu = await get_user_menu(db, sender_tg)
    if role == "client":
        await message.answer(
            f"✅ <b>Tizimga kirdingiz</b> (mijoz sifatida)\n\n"
            f"👋 <b>{user.get('shop_name','Salom')}</b>!\n"
            f"Bot menyusidan yoki Mini App'dan ishlashda davom eting.",
            reply_markup=menu, parse_mode="HTML"
        )
    else:  # admin
        await message.answer(
            f"✅ <b>Tizimga kirdingiz</b> (hodim sifatida)\n\n"
            f"👨‍💼 <b>{user.get('full_name','')}</b>\n"
            f"Bot menyusidan to'liq ishlashingiz mumkin.",
            reply_markup=menu, parse_mode="HTML"
        )
