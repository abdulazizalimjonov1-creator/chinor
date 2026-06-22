"""🔑 Mini App uchun login/parol yaratish oqimi.

Bir xil oqim ham mijoz, ham admin uchun ishlaydi:
  1) Foydalanuvchi menyudan '🔑 Login/parol' ni bosadi.
  2) Bot login so'raydi (8+ belgi, harf+son, faqat lotin+raqam+._-).
  3) Login bandligi tekshiriladi (admins + clients fazosida noyob).
  4) Parol so'raladi (8+ belgi, harf+son qoidalari).
  5) Parol PBKDF2 bilan hashlanib, DB ga saqlanadi.
  6) Xavfsizlik uchun parol yozilgan xabar bot tomonidan o'chiriladi.
"""

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.keyboards import cancel_kb
from bot.states import SetCredentialsStates
from bot.permissions import is_admin_or_glavniy, get_user_menu
from bot.auth import (
    validate_username, validate_password,
    hash_password, suggest_username,
    attempt_tracker,
    MIN_LENGTH,
)
from database.channel_db import db

router = Router()
CANCEL = "❌ Bekor qilish"


def _rules_block() -> str:
    return (
        f"📋 <b>Qoidalar:</b>\n"
        f"• Kamida {MIN_LENGTH} ta belgi\n"
        f"• Harf <b>VA</b> son qatnashishi shart\n"
        f"• Login: faqat lotin harflari, raqam va  . _ -"
    )


def _existing_creds_kb() -> 'InlineKeyboardBuilder':
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Yangi login/parol kiritish", callback_data="cred_new")
    kb.button(text="🗑 Loginni butunlay o'chirish", callback_data="cred_clear")
    kb.button(text="❌ Bekor",                       callback_data="cred_cancel")
    kb.adjust(1)
    return kb.as_markup()


async def _ask_new_username(message_or_cb, state: FSMContext,
                              uid: int, is_admin: bool, cid: int,
                              first_name: str):
    """Yangi login so'rash bosqichini ko'rsatadi (Message yoki CallbackQuery uchun)."""
    await state.set_state(SetCredentialsStates.username)
    await state.update_data(target_uid=uid, target_cid=cid, is_admin=is_admin)
    base = first_name or "user"
    suggested = suggest_username(base)
    text = (
        f"🔑 <b>Yangi login yarating</b>\n\n"
        f"{_rules_block()}\n\n"
        f"💡 Taklif: <code>{suggested}</code>\n\n"
        f"Loginni kiriting:"
    )
    if hasattr(message_or_cb, "message"):
        await message_or_cb.message.answer(text, reply_markup=cancel_kb(),
                                             parse_mode="HTML")
    else:
        await message_or_cb.answer(text, reply_markup=cancel_kb(),
                                     parse_mode="HTML")


@router.message(F.text == "🔑 Login/parol")
async def setup_credentials_start(message: Message, state: FSMContext):
    if not db.is_mini_app_enabled():
        await message.answer(
            "ℹ️ Mini App rejimi hozircha o'chirilgan.",
            reply_markup=await get_user_menu(db, message.from_user.id)
        )
        return
    uid = message.from_user.id
    is_admin = await is_admin_or_glavniy(db, uid)
    client = None
    if not is_admin:
        client = await db.get_client_by_tg(uid)
        if not client:
            await message.answer(
                "⚠️ Avval /start orqali ro'yxatdan o'ting.",
                reply_markup=await get_user_menu(db, uid)
            )
            return

    # Mavjud credentials bormi?
    if is_admin:
        admin = await db.get_admin(uid)
        existing_login = (admin or {}).get("username") or ""
        has_creds = await db.admin_has_credentials(uid)
    else:
        existing_login = client.get("username") or ""
        has_creds = await db.client_has_credentials(client["id"])

    cid = client["id"] if client else 0

    # Agar mavjud bo'lsa — tanlov beramiz: yangi yarataylikmi yoki o'chiraylikmi
    if has_creds:
        await state.update_data(
            target_uid=uid, target_cid=cid, is_admin=is_admin
        )
        # Avvalgi blokirovkani ham olib tashlaymiz — foydalanuvchi qaytadan
        # urinib ko'rishi mumkin bo'lsin
        attempt_tracker.clear(uid)
        await message.answer(
            f"🔄 <b>Sizda mavjud login bor:</b> <code>{existing_login}</code>\n\n"
            f"Nima qilamiz?",
            reply_markup=_existing_creds_kb(), parse_mode="HTML"
        )
        return

    # Yangi yaratish — to'g'ridan-to'g'ri login so'raymiz
    await _ask_new_username(message, state, uid, is_admin, cid,
                              message.from_user.first_name or "")


@router.callback_query(F.data == "cred_new")
async def cred_choose_new(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("target_uid"):
        # State yo'qolgan bo'lsa — qayta boshlash kerak
        await cb.answer("Iltimos, '🔑 Login/parol' tugmasini qaytadan bosing.",
                          show_alert=True)
        return
    await _ask_new_username(
        cb, state,
        uid=data["target_uid"], is_admin=data["is_admin"],
        cid=data.get("target_cid", 0),
        first_name=cb.from_user.first_name or ""
    )
    await cb.answer()


@router.callback_query(F.data == "cred_clear")
async def cred_choose_clear(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    uid = data.get("target_uid") or cb.from_user.id
    is_admin = data.get("is_admin")
    cid = data.get("target_cid", 0)
    # State yo'q bo'lsa qayta aniqlaymiz
    if is_admin is None:
        is_admin = await is_admin_or_glavniy(db, uid)
        if not is_admin:
            c = await db.get_client_by_tg(uid)
            cid = c["id"] if c else 0

    if is_admin:
        await db.clear_admin_credentials(uid)
    elif cid:
        await db.clear_client_credentials(cid)
    attempt_tracker.clear(uid)
    await state.clear()
    await cb.message.answer(
        "🗑 <b>Login va parol o'chirildi.</b>\n\n"
        "Endi qaytadan yaratish uchun «🔑 Login/parol» tugmasini bosing.",
        reply_markup=await get_user_menu(db, cb.from_user.id), parse_mode="HTML"
    )
    await cb.answer("O'chirildi")


@router.callback_query(F.data == "cred_cancel")
async def cred_choose_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.answer(
        "Bekor.", reply_markup=await get_user_menu(db, cb.from_user.id)
    )
    await cb.answer()


@router.message(SetCredentialsStates.username)
async def setup_username(message: Message, state: FSMContext):
    if message.text == CANCEL:
        await state.clear()
        await message.answer(
            "Bekor.",
            reply_markup=await get_user_menu(db, message.from_user.id)
        )
        return
    text = (message.text or "").strip()
    ok, err = validate_username(text)
    if not ok:
        await message.answer(f"⚠️ {err}\nQaytadan kiriting:",
                              reply_markup=cancel_kb())
        return

    # Bandligi: admin + mijoz fazosida bir vaqtning o'zida noyob bo'lsin
    data = await state.get_data()
    a = await db.get_admin_by_username(text)
    c = await db.get_client_by_username(text)
    is_mine = False
    if a and data["is_admin"] and a.get("telegram_id") == data["target_uid"]:
        is_mine = True
    if c and (not data["is_admin"]) and c.get("id") == data["target_cid"]:
        is_mine = True
    if (a or c) and not is_mine:
        await message.answer(
            "⚠️ Bu login allaqachon band. Boshqasini kiriting:",
            reply_markup=cancel_kb()
        )
        return

    await state.update_data(new_username=text)
    await state.set_state(SetCredentialsStates.password)
    await message.answer(
        "✅ Login qabul qilindi.\n\n"
        "🔐 Endi <b>parol</b>ni kiriting:\n\n"
        f"{_rules_block().replace('Login:', 'Parol:').replace('login', 'parol')}\n\n"
        "<i>🛡 Parol yuborilgach, xabaringiz xavfsizlik uchun bot tomonidan "
        "o'chiriladi.</i>",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )


@router.message(SetCredentialsStates.password)
async def setup_password(message: Message, state: FSMContext):
    if message.text == CANCEL:
        await state.clear()
        await message.answer(
            "Bekor.",
            reply_markup=await get_user_menu(db, message.from_user.id)
        )
        return
    # ⚠️ Bo'sh joy va \n ni olib tashlaymiz — mobil klaviaturada tasodifan
    # qo'shilishi mumkin va hash'ni buzadi. Verify tomonida ham strip bor.
    pw = (message.text or "").strip()
    ok, err = validate_password(pw)
    if not ok:
        await message.answer(f"⚠️ {err}\nQaytadan kiriting:",
                              reply_markup=cancel_kb())
        return

    # Xavfsizlik: parol yozilgan xabarni darrov o'chiramiz
    try:
        await message.delete()
    except Exception:
        pass

    data = await state.get_data()
    h = hash_password(pw)
    username = data["new_username"]
    if data["is_admin"]:
        saved = await db.set_admin_credentials(
            data["target_uid"], username, h
        )
    else:
        saved = await db.set_client_credentials(
            data["target_cid"], username, h
        )

    await state.clear()
    if not saved:
        await message.answer(
            "⚠️ Saqlanmadi — login band bo'lib qolgan ekan, qaytadan urinib ko'ring.",
            reply_markup=await get_user_menu(db, message.from_user.id)
        )
        return

    role_txt = "👨‍💼 Hodim (admin)" if data["is_admin"] else "🛍 Mijoz"
    await message.answer(
        f"✅ <b>Login va parol saqlandi!</b>\n\n"
        f"🔑 Login: <code>{username}</code>\n"
        f"🏷 Rol: {role_txt}\n\n"
        f"Endi Mini App'ni ochib shu login va parol bilan kira olasiz. "
        f"Botda baribir telegram orqali ham ishlashda davom etasiz.",
        reply_markup=await get_user_menu(db, message.from_user.id),
        parse_mode="HTML"
    )
