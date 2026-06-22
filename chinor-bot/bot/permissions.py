"""Adminlar uchun rollar, ruxsatlar va valyuta rejimi.

Glavniy admin har bir admin uchun:
  • Tayyor rol tanlashi mumkin (full / products / cashier / stats)
  • Yoki 'custom' rol bilan har bir ruxsatni alohida belgilashi mumkin
  • Valyuta rejimini sozlashi mumkin (global / hybrid / uzs_only / usd_only)

Glavniy admin (bot/config.py:GLAVNIY_ADMIN_ID) har doim hammasiga ruxsatga ega.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional, Set

from bot.config import GLAVNIY_ADMIN_ID


# ─── Ruxsat kalitlari (qisqa, DB ga yoziladi) ────────────────────────────────
# Foydalanuvchi-do'st nomlar — admin paneli UI'sida ko'rinadi.
PERMISSIONS: Dict[str, str] = {
    "products_view":  "📦 Mahsulotlarni ko'rish",
    "products_add":   "➕ Yangi mahsulot qo'shish",
    "products_edit":  "✏️ Mahsulotni tahrirlash",
    "products_del":   "🗑️ Mahsulotni o'chirish",
    "products_qty":   "📥 Prixod (tovar qo'shish)",
    "categories":     "🗂 Kategoriyalar",
    "suppliers":      "🚚 Yetkazib beruvchilar",
    "sale":           "🧾 Kassada sotuv",
    "clients_view":   "👤 Mijozlarni ko'rish",
    "clients_add":    "➕ Mijoz qo'shish",
    "clients_pay":    "💳 To'lov qabul qilish",
    "orders":         "📋 Buyurtmalarni boshqarish",
    "stats":          "📊 Statistika ko'rish",
    "export":         "📥 Eksport (Excel)",
    "low_stock":      "⚠️ Past qoldiq ro'yxati",
    "usd_rate":       "💱 USD kursini o'zgartirish",
    "ai_analytics":   "🤖 AI Analitika (Gemini)",
}

# Tartibli ro'yxat — UI da shu tartibda ko'rsatamiz
PERMISSION_ORDER: List[str] = [
    "products_view", "products_add", "products_edit", "products_del", "products_qty",
    "categories", "suppliers",
    "sale",
    "clients_view", "clients_add", "clients_pay",
    "orders",
    "stats", "export", "low_stock",
    "usd_rate",
    "ai_analytics",
]


# ─── Tayyor rollar ───────────────────────────────────────────────────────────
ROLE_LABELS: Dict[str, str] = {
    "full":     "👑 To'liq admin",
    "products": "📦 Mahsulotchi",
    "cashier":  "🧾 Kassir",
    "stats":    "📊 Statistikachi",
    "custom":   "⚙️ Maxsus (qo'lda)",
}

ROLE_PRESETS: Dict[str, Set[str]] = {
    "full":     set(PERMISSIONS.keys()),
    "products": {
        "products_view", "products_add", "products_edit", "products_del",
        "products_qty", "categories", "suppliers", "low_stock",
    },
    "cashier":  {
        "sale", "products_view", "clients_view", "clients_pay",
    },
    "stats":    {
        "stats", "export", "products_view", "clients_view", "low_stock",
        "ai_analytics",
    },
    # 'custom' uchun preset yo'q — admin.permissions JSON dan o'qiladi.
}


# ─── Valyuta rejimlari ───────────────────────────────────────────────────────
CURRENCY_MODES: Dict[str, str] = {
    "hybrid":    "💱 Aralash (USD + so'm)",
    "uzs_only":  "🇺🇿 Faqat so'm",
    "usd_only":  "🇺🇸 Faqat USD",
}


# ─── Yordamchi: parse/serialize permissions ──────────────────────────────────

def parse_permissions(raw: Optional[str]) -> Set[str]:
    """JSON yoki vergul-ajratilgan stringdan ruxsatlar to'plamini olish."""
    if not raw:
        return set()
    raw = raw.strip()
    try:
        # JSON list
        data = json.loads(raw)
        if isinstance(data, list):
            return {str(x) for x in data if x}
        if isinstance(data, dict):
            return {k for k, v in data.items() if v}
    except Exception:
        pass
    # fallback: vergul bilan ajratilgan
    return {p.strip() for p in raw.split(",") if p.strip()}


def serialize_permissions(perms: Set[str]) -> str:
    return json.dumps(sorted(perms), ensure_ascii=False)


def effective_permissions(admin: dict) -> Set[str]:
    """Adminning haqiqiy ruxsatlar to'plami:
    • role='custom' bo'lsa — permissions JSON dan
    • boshqa rollarda — ROLE_PRESETS dan
    • role yo'q yoki noma'lum bo'lsa — 'full' (eski xulq-atvor)
    """
    if not admin:
        return set()
    role = (admin.get("role") or "full").strip().lower()
    if role == "custom":
        return parse_permissions(admin.get("permissions"))
    return set(ROLE_PRESETS.get(role, ROLE_PRESETS["full"]))


# ─── Ruxsat tekshirish ───────────────────────────────────────────────────────

def is_glavniy(uid: int) -> bool:
    return uid == GLAVNIY_ADMIN_ID


async def has_permission(db, uid: int, perm: str) -> bool:
    """Berilgan foydalanuvchida `perm` ruxsati bormi?
    Glavniy admin har doim True. Admin bo'lmasa False."""
    if is_glavniy(uid):
        return True
    admin = await db.get_admin(uid)
    if not admin:
        return False
    return perm in effective_permissions(admin)


async def is_admin_or_glavniy(db, uid: int) -> bool:
    if is_glavniy(uid):
        return True
    return await db.is_admin(uid)


# ─── Valyuta rejimi ──────────────────────────────────────────────────────────

async def get_currency_mode(db, uid: int) -> str:
    """Foydalanuvchi uchun haqiqiy valyuta rejimini qaytaradi.
    Admin uchun override bor bo'lsa — shu, aks holda global rejim."""
    if not is_glavniy(uid):
        admin = await db.get_admin(uid)
        if admin:
            mode = (admin.get("currency_mode") or "").strip().lower()
            if mode in ("hybrid", "uzs_only", "usd_only"):
                return mode
    return db.get_currency_mode_global()


def show_usd(mode: str) -> bool:
    """Shu rejimda USD displeyi/inputi ko'rsatilsinmi?"""
    return mode in ("hybrid", "usd_only")


def show_uzs(mode: str) -> bool:
    """Shu rejimda so'm displeyi/inputi ko'rsatilsinmi?"""
    return mode in ("hybrid", "uzs_only")


def fmt_price_pair(usd: float, summ: float, mode: str = "hybrid") -> str:
    """Narxni valyuta rejimiga qarab formatlaydi.
    'hybrid' → '$1.25 (≈ 15,625 so'm)' yoki yolg'iz so'm
    'usd_only' → '$1.25'
    'uzs_only' → '15,625 so'm'
    """
    usd = float(usd or 0)
    summ = float(summ or 0)
    if mode == "uzs_only":
        return f"{summ:,.0f} so'm"
    if mode == "usd_only":
        if usd > 0:
            return f"${usd:,.2f}"
        return f"${summ:,.2f}"  # USD bo'lmasa — yo'qligi noaniq, lekin so'mni $ deb ko'rsatmaymiz
    # hybrid
    if usd > 0:
        return f"${usd:,.2f}  (≈ {summ:,.0f} so'm)"
    return f"{summ:,.0f} so'm"


# ─── Menyu yordamchisi ───────────────────────────────────────────────────────

async def get_user_menu(db, uid: int):
    """Foydalanuvchi uchun mos reply klaviaturani qaytaradi:
    • Glavniy admin → glavniy_menu
    • Boshqa admin → ruxsatlarga moslangan admin_menu
    • Admin emas (mijoz yoki noma'lum) → client_menu"""
    # Lazy import — siklik importdan saqlanish uchun
    from bot.keyboards import glavniy_menu, admin_menu, client_menu
    if is_glavniy(uid):
        return glavniy_menu()
    admin = await db.get_admin(uid)
    if admin:
        return admin_menu(effective_permissions(admin))
    return client_menu()


# ─── Handlerlar uchun yagona yordamchilar ────────────────────────────────────
# Bular ilgari har bir handler faylida alohida-alohida (_is_admin, _deny,
# _check_perm, _menu_for ...) yozilgan edi — endi bitta joydan import qilinadi.

async def deny(target, db, text: str = "⛔ Sizda bu amal uchun ruxsat yo'q."):
    """Foydalanuvchiga 'ruxsat yo'q' javobini yuboradi.
    `target` — Message yoki CallbackQuery bo'lishi mumkin."""
    # CallbackQuery'da `.data` atributi bor, Message'da yo'q
    if hasattr(target, "data") and not hasattr(target, "text"):
        await target.answer("⛔ Ruxsat yo'q", show_alert=True)
        return
    # Message
    try:
        menu = await get_user_menu(db, target.from_user.id)
        await target.answer(text, reply_markup=menu)
    except Exception:
        await target.answer(text)


async def require(target, db, perm: str) -> bool:
    """Foydalanuvchida `perm` ruxsati borligini tekshiradi.
    Bo'lmasa — deny() yuborib, False qaytaradi. Bo'lsa — True.

    Ishlatish:
        if not await require(message, db, "products_add"):
            return
    """
    if await has_permission(db, target.from_user.id, perm):
        return True
    await deny(target, db)
    return False
