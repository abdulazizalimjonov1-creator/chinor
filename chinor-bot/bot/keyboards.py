import math

from aiogram.types import WebAppInfo
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder


def _mini_app_url() -> str:
    """Mini App URL ni runtime'da olamiz (kerakli paytda kerak)."""
    try:
        from bot.config import MINI_APP_URL
        return (MINI_APP_URL or "").strip()
    except Exception:
        return ""


# ─── Mahsulot ro'yxati uchun sahifalash ──────────────────────────────────────
# Bir sahifada nechta mahsulot tugmasi ko'rinadi. Mahsulotlar SOTUV REYTINGI
# bo'yicha tartiblangani uchun 1-sahifa avtomatik "6 ta eng ko'p sotiladigan"
# bo'ladi. Telegram inline klaviaturada ~100 tugma chegarasi bor — shuning
# uchun cheklovsiz ro'yxat (bug!) o'rniga sahifalab beramiz.
PRODUCT_PAGE_SIZE = 6


# ─── Reply klaviatura tugmalari (asosiy menyu) ──────────────────────────────
# Mahsulot ro'yxati va kassada «yozsa darrov qidiradi» funksiyalari uchun:
# foydalanuvchi shu matnlardan birini bossa — qidiruv emas, asosiy menyu
# ishlasin (shuning uchun catch-all qidiruv handler'lari bu setni istisno
# qiladi).
RESERVED_MENU = {
    # Bosh admin / admin menyusi
    "👥 Adminlar", "📊 Statistika", "🏆 Reyting", "💰 Umumiy foyda",
    "📥 Eksport", "⚠️ Past qoldiq", "💱 USD kursi", "⚙️ Sozlamalar",
    "🧾 Sotuv", "📦 Mahsulotlar", "➕ Mahsulot qo'shish",
    "🗂 Kategoriyalar", "🚚 Yetkazib beruvchilar",
    "👤 Mijozlar", "➕ Mijoz qo'shish", "📋 Buyurtmalar",
    "💳 To'lov qabul qilish", "🤖 AI Analitika",
    # Mijoz menyusi
    "🛒 Buyurtma berish", "📊 Hisobotim", "💳 Qarzim", "ℹ️ Ma'lumotlarim",
    "🔎 Mahsulot qidirish", "💬 AI sotuvchi",
    # Login/parol + Mini App
    "🔑 Login/parol", "🌐 Mini App",
    # Universal
    "❌ Bekor qilish",
}


def paginate(items: list, page: int, size: int = PRODUCT_PAGE_SIZE):
    """Ro'yxatdan bitta sahifani kesib oladi.
    Qaytaradi: (sahifa_elementlari, joriy_sahifa, jami_sahifalar).
    `page` chegaradan tashqarida bo'lsa — eng yaqin haqiqiy sahifaga tushadi."""
    total = len(items)
    pages = max(1, math.ceil(total / size))
    page = max(0, min(int(page or 0), pages - 1))
    start = page * size
    return items[start:start + size], page, pages


def _add_page_nav(kb: InlineKeyboardBuilder, page: int, pages: int, prefix: str):
    """◀️ | 1/N | ▶️ navigatsiya qatorini qo'shadi (faqat pages > 1 bo'lsa).
    callback_data: '{prefix}_{yangi_sahifa}', harakatsizlar uchun '{prefix}_noop'.
    Qaytaradi: qo'shilgan tugmalar soni (adjust uchun)."""
    if pages <= 1:
        return 0
    prev_cb = f"{prefix}_{page-1}" if page > 0 else f"{prefix}_noop"
    next_cb = f"{prefix}_{page+1}" if page < pages - 1 else f"{prefix}_noop"
    kb.button(text=("◀️" if page > 0 else "·"), callback_data=prev_cb)
    kb.button(text=f"{page+1}/{pages}", callback_data=f"{prefix}_noop")
    kb.button(text=("▶️" if page < pages - 1 else "·"), callback_data=next_cb)
    return 3


def cancel_kb():
    kb = ReplyKeyboardBuilder()
    kb.button(text="❌ Bekor qilish")
    return kb.as_markup(resize_keyboard=True)


def request_contact_kb():
    """Mijozdan haqiqiy kontaktini so'rash uchun klaviatura.
    Faqat 'request_contact=True' tugmasi orqali yuborilgan kontakt qabul
    qilinadi; qo'lda yozilgan raqam tasdiqlanmaydi."""
    kb = ReplyKeyboardBuilder()
    kb.button(text="📱 Raqamni yuborish", request_contact=True)
    return kb.as_markup(resize_keyboard=True, one_time_keyboard=True)


def client_type_kb():
    """Mijoz qo'shishda — optomchi/donachi tanlash"""
    kb = InlineKeyboardBuilder()
    kb.button(text="🛍️ Donachi (chakana)",  callback_data="ctype_dona")
    kb.button(text="📦 Optomchi (ulgurji)", callback_data="ctype_optom")
    kb.adjust(1)
    return kb.as_markup()


def glavniy_menu():
    kb = ReplyKeyboardBuilder()
    kb.button(text="👥 Adminlar")
    kb.button(text="📊 Statistika")
    if _feature_on("categories"):
        kb.button(text="🗂 Kategoriyalar")
    if _feature_on("suppliers"):
        kb.button(text="🚚 Yetkazib beruvchilar")
    kb.button(text="🏆 Reyting")
    kb.button(text="💰 Umumiy foyda")
    if _feature_on("ai_analytics"):
        kb.button(text="🤖 AI Analitika")
    kb.button(text="📥 Eksport")
    kb.button(text="⚠️ Past qoldiq")
    kb.button(text="💱 USD kursi")
    if _feature_on("mini_app"):
        kb.button(text="🔑 Login/parol")
        url = _mini_app_url()
        if url:
            kb.button(text="🌐 Mini App", web_app=WebAppInfo(url=url))
        else:
            kb.button(text="🌐 Mini App")
    kb.button(text="⚙️ Sozlamalar")
    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True)


def settings_kb(dona_enabled: bool, wholesale_enabled: bool):
    """Bosh admin uchun sozlamalar — dona va optom narxni alohida yoqish/o'chirish."""
    kb = InlineKeyboardBuilder()
    dona_status = "✅" if dona_enabled else "❌"
    whs_status = "✅" if wholesale_enabled else "❌"
    dona_action = "🔴 Dona narxni o'chirish" if dona_enabled else "🟢 Dona narxni yoqish"
    whs_action = "🔴 Optom narxni o'chirish" if wholesale_enabled else "🟢 Optom narxni yoqish"
    kb.button(text=f"🛍️ Dona narx: {dona_status}", callback_data="settings_noop")
    kb.button(text=dona_action, callback_data="settings_toggle_dona")
    kb.button(text=f"📦 Optom narx: {whs_status}", callback_data="settings_noop")
    kb.button(text=whs_action, callback_data="settings_toggle_wholesale")
    kb.adjust(1)
    return kb.as_markup()


def admin_menu(perms: set = None):
    """Admin menyusi.
    perms=None → eski xulq-atvor (hammasi ko'rinadi).
    perms berilgan bo'lsa — faqat ruxsat etilgan tugmalar chiqadi."""
    # Tugma -> (kerakli ruxsat kaliti, funksiya kaliti yoki None)
    # funksiya kaliti berilgan bo'lsa — o'sha funksiya o'chirilgan bo'lsa tugma chiqmaydi.
    BUTTONS = [
        ("🧾 Sotuv",                "sale",          None),
        ("📦 Mahsulotlar",          "products_view", None),
        ("➕ Mahsulot qo'shish",    "products_add",  None),
        ("🗂 Kategoriyalar",        "categories",    "categories"),
        ("🚚 Yetkazib beruvchilar", "suppliers",     "suppliers"),
        ("👤 Mijozlar",             "clients_view",  None),
        ("➕ Mijoz qo'shish",       "clients_add",   None),
        ("📋 Buyurtmalar",          "orders",        None),
        ("📊 Statistika",           "stats",         None),
        ("💳 To'lov qabul qilish",  "clients_pay",   None),
        ("📥 Eksport",              "export",        None),
        ("⚠️ Past qoldiq",          "low_stock",     None),
        ("💱 USD kursi",            "usd_rate",      None),
        ("🤖 AI Analitika",         "ai_analytics",  "ai_analytics"),
        # perm "" — har qanday rolli admin ko'radi (faqat o'z credentialsi)
        ("🔑 Login/parol",          "",              "mini_app"),
        ("🌐 Mini App",             "",              "mini_app"),
    ]
    kb = ReplyKeyboardBuilder()
    any_button = False
    for text, perm, feat in BUTTONS:
        if feat is not None and not _feature_on(feat):
            continue
        # perm "" — har bir adminga ochiq; perm "X" — faqat ruxsati borlarga
        if perms is None or perm == "" or perm in perms:
            kb.button(text=text)
            any_button = True
    if not any_button:
        kb.button(text="ℹ️ Hech qanday ruxsat berilmagan")
    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True)


def settings_main_kb(currency_mode: str):
    """Bosh admin uchun sozlamalar bosh menyusi (inline)."""
    from bot.permissions import CURRENCY_MODES
    kb = InlineKeyboardBuilder()
    kb.button(text="🛍️ Dona / 📦 Optom narx", callback_data="settings_prices")
    cur_label = CURRENCY_MODES.get(currency_mode, currency_mode)
    kb.button(text=f"💱 Valyuta rejimi: {cur_label}", callback_data="settings_currency")
    kb.button(text="🧩 Funksiyalar (yoqish/o'chirish)", callback_data="settings_features")
    kb.adjust(1)
    return kb.as_markup()


# ── Universal funksiya kalitlari ─────────────────────────────────────────────

# Sozlama menyusida ko'rsatiladigan funksiyalar: (kalit, nom, callback qo'shimchasi)
FEATURE_TOGGLES = [
    ("barcode",       "🔢 Shtrix-kod skaneri"),
    ("channel",       "📢 Kanalga e'lon qilish"),
    ("client_orders", "🛒 Mijoz buyurtmalari"),
    ("nasiya",        "🤝 Nasiya (qarzga sotuv)"),
    ("categories",    "🗂 Kategoriyalar bo'limi"),
    ("suppliers",     "🚚 Yetkazib beruvchilar bo'limi"),
    ("cat_filter",    "🔻 Kategoriya bo'yicha filtr"),
    ("cart_edit",     "🛒 Savatni tahrirlash"),
    ("quick_restock", "⚡ Past qoldiq tezkor to'ldirish"),
    ("quick_prixod",  "📥 Tezkor prixod (yetkazib beruvchidan)"),
    ("ai_analytics",  "🤖 AI Analitika (Gemini)"),
    ("client_search", "🔎 Mijoz uchun mahsulot qidirish"),
    ("ai_consult",    "💬 AI sotuvchi-konsultant (mijoz savollari)"),
    ("mini_app",      "🌐 Mini App (login/parol orqali kirish)"),
]


def features_kb(states: dict):
    """Funksiyalarni yoqish/o'chirish klaviaturasi.
    `states` — {kalit: bool} ko'rinishida har bir funksiya holati."""
    kb = InlineKeyboardBuilder()
    for key, name in FEATURE_TOGGLES:
        on = bool(states.get(key, True))
        mark = "✅" if on else "❌"
        action = "o'chirish" if on else "yoqish"
        kb.button(
            text=f"{mark} {name} — {action}",
            callback_data=f"feat_toggle_{key}"
        )
    kb.button(text="🔙 Orqaga", callback_data="settings_back")
    kb.adjust(1)
    return kb.as_markup()


def currency_mode_kb(current: str, target: str = "global", tg_id: int = 0):
    """Valyuta rejimini tanlash klaviaturasi.
    target = 'global' (umumiy) yoki 'admin_<id>' (alohida admin uchun)."""
    from bot.permissions import CURRENCY_MODES
    kb = InlineKeyboardBuilder()
    # admin uchun override'da "Global" tugmasi ham bo'ladi (override'ni o'chirish)
    if target.startswith("admin"):
        mark = "✅ " if not current else ""
        kb.button(
            text=f"{mark}🌐 Global rejimni ishlatish (default)",
            callback_data=f"curset_{target}_default"
        )
    for code, label in CURRENCY_MODES.items():
        mark = "✅ " if current == code else ""
        kb.button(text=f"{mark}{label}", callback_data=f"curset_{target}_{code}")
    kb.button(text="🔙 Orqaga", callback_data="curset_back")
    kb.adjust(1)
    return kb.as_markup()


# ── Admin rollar / ruxsatlar ─────────────────────────────────────────────────

def admin_edit_kb(admin: dict, glavniy_id: int):
    """Bitta adminni tahrirlash menyusi: rol tanlash, ruxsatlar, valyuta, o'chirish."""
    from bot.permissions import ROLE_LABELS, CURRENCY_MODES
    kb = InlineKeyboardBuilder()
    tg = admin["telegram_id"]
    role = (admin.get("role") or "full").strip().lower()
    role_label = ROLE_LABELS.get(role, role)
    cm = (admin.get("currency_mode") or "").strip().lower()
    cm_label = CURRENCY_MODES.get(cm, "🌐 Global (default)")
    kb.button(text=f"🎭 Rol: {role_label}", callback_data=f"adm_role_{tg}")
    kb.button(text="🛠 Ruxsatlarni qo'lda sozlash", callback_data=f"adm_perms_{tg}")
    kb.button(text=f"💱 Valyuta: {cm_label}", callback_data=f"adm_curmode_{tg}")
    if tg != glavniy_id:
        kb.button(text="🗑️ Adminlikdan olib tashlash", callback_data=f"del_admin_{tg}")
    kb.button(text="🔙 Adminlar ro'yxati", callback_data="adm_back")
    kb.adjust(1)
    return kb.as_markup()


def admin_role_pick_kb(tg_id: int, current_role: str):
    from bot.permissions import ROLE_LABELS
    kb = InlineKeyboardBuilder()
    for code, label in ROLE_LABELS.items():
        mark = "✅ " if current_role == code else ""
        kb.button(text=f"{mark}{label}", callback_data=f"adm_setrole_{tg_id}_{code}")
    kb.button(text="🔙 Orqaga", callback_data=f"adm_open_{tg_id}")
    kb.adjust(1)
    return kb.as_markup()


def admin_perms_kb(tg_id: int, granted: set):
    """Checkbox-stil ruxsat klaviaturasi."""
    from bot.permissions import PERMISSIONS, PERMISSION_ORDER
    kb = InlineKeyboardBuilder()
    for key in PERMISSION_ORDER:
        label = PERMISSIONS[key]
        mark = "✅" if key in granted else "❌"
        kb.button(text=f"{mark} {label}", callback_data=f"adm_togp_{tg_id}_{key}")
    kb.button(text="✅ Hammasini yoqish", callback_data=f"adm_pall_{tg_id}_on")
    kb.button(text="❌ Hammasini o'chirish", callback_data=f"adm_pall_{tg_id}_off")
    kb.button(text="🔙 Adminga qaytish", callback_data=f"adm_open_{tg_id}")
    kb.adjust(1)
    return kb.as_markup()


def client_menu():
    kb = ReplyKeyboardBuilder()
    if _feature_on("client_search"):
        kb.button(text="🔎 Mahsulot qidirish")
    if _feature_on("ai_consult"):
        kb.button(text="💬 AI sotuvchi")
    # "🛒 Buyurtma berish" — faqat Mijoz buyurtmalari funksiyasi yoqilgan bo'lsa
    if _feature_on("client_orders"):
        kb.button(text="🛒 Buyurtma berish")
    kb.button(text="📊 Hisobotim")
    kb.button(text="💳 Qarzim")
    kb.button(text="ℹ️ Ma'lumotlarim")
    if _feature_on("mini_app"):
        kb.button(text="🔑 Login/parol")
        # web_app to'g'ridan-to'g'ri reply keyboard tugmasiga ulanadi —
        # shunda tg.sendData() ishonchli ravishda ishlaydi.
        url = _mini_app_url()
        if url:
            kb.button(text="🌐 Mini App", web_app=WebAppInfo(url=url))
        else:
            kb.button(text="🌐 Mini App")
    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True)


# ── Mahsulot ─────────────────────────────────────────────────────────────────

def products_list_kb(products, mode="view", page=0):
    """Admin mahsulot ro'yxati — SAHIFALANGAN (6/sahifa).
    `products` — to'liq tartiblangan ro'yxat (sotuv reytingi bo'yicha);
    funksiya kerakli sahifani o'zi kesadi. 1-sahifa = 6 ta top-seller.
    Eski cheklovsiz versiya 95+ mahsulotda Telegram limitidan oshib ketardi."""
    kb = InlineKeyboardBuilder()
    page_items, page, pages = paginate(products, page)
    n = 0
    for p in page_items:
        icon = "🟢" if p.get("qty", 0) > 0 else "🔴"
        unit = p.get("unit", "dona")
        kb.button(
            text=f"{icon} {p['name']} ({p.get('qty', 0):g} {unit})",
            callback_data=f"prod_{mode}_{p['id']}"
        )
        n += 1
    sizes = [1] * n
    nav = _add_page_nav(kb, page, pages, "prodpage")
    if nav:
        sizes.append(nav)
    kb.button(text="🔍 ID / nom / shtrix-kod bilan qidirish",
              callback_data="prod_search")
    sizes.append(1)
    if _feature_on("categories") and _feature_on("cat_filter"):
        kb.button(text="🔻 Kategoriya bo'yicha filtr", callback_data="pcatf_open")
        sizes.append(1)
    kb.adjust(*sizes)
    return kb.as_markup()


def product_actions_kb(pid: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Tahrirlash",   callback_data=f"prod_edit_{pid}")
    kb.button(text="➕ Tovar qo'shish", callback_data=f"prod_qty_{pid}")
    kb.button(text="🗑️ O'chirish",    callback_data=f"prod_del_{pid}")
    kb.button(text="🔙 Orqaga",       callback_data="prod_back")
    kb.adjust(2)
    return kb.as_markup()


def product_edit_fields_kb(pid: int):
    kb = InlineKeyboardBuilder()
    fields = [
        ("Nomi",                "name"),
        ("Tavsifi",             "description"),
        ("Donada narxi (USD)",  "sell_price"),
        ("Optom narxi (USD)",   "wholesale_price"),
        ("Tannarxi (USD)",      "cost_price"),
        ("Miqdori",             "qty"),
        ("O'lchov birligi",     "unit"),
        ("Rasm",                "image"),
        ("Shtrix-kod",          "barcode"),
        ("Kategoriya",          "category"),
        ("Yetkazib beruvchi",   "supplier"),
    ]
    # Shtrix-kod / Kategoriya / Yetkazib beruvchi — funksiya o'chirilgan
    # bo'lsa mos maydon yashirinadi.
    if not _feature_on("barcode"):
        fields = [(l, f) for l, f in fields if f != "barcode"]
    if not _feature_on("categories"):
        fields = [(l, f) for l, f in fields if f != "category"]
    if not _feature_on("suppliers"):
        fields = [(l, f) for l, f in fields if f != "supplier"]
    for label, field in fields:
        kb.button(text=f"✏️ {label}", callback_data=f"pedit_{pid}_{field}")
    kb.button(text="🔙 Orqaga", callback_data=f"prod_view_{pid}")
    kb.adjust(2)
    return kb.as_markup()


# ── Mijoz ─────────────────────────────────────────────────────────────────────

def clients_list_kb(clients):
    kb = InlineKeyboardBuilder()
    for c in clients:
        debt_usd = float(c.get("debt_usd", 0) or 0)
        debt_sum = float(c.get("debt", 0) or 0)
        if debt_usd > 0:
            label = f"🔴 {c['shop_name']}  ${debt_usd:,.2f}"
        elif debt_sum > 0:
            label = f"🔴 {c['shop_name']}  {debt_sum:,.0f} so'm"
        else:
            label = f"🟢 {c['shop_name']}"
        kb.button(text=label, callback_data=f"client_view_{c['id']}")
    kb.adjust(1)
    return kb.as_markup()


def client_actions_kb(cid: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="💳 To'lov",          callback_data=f"pay_{cid}")
    kb.button(text="📋 Buyurtmalar",     callback_data=f"client_orders_{cid}")
    kb.button(text="🛒 Zakaz berish",    callback_data=f"admin_order_{cid}")
    kb.button(text="🗑️ O'chirish",      callback_data=f"client_del_{cid}")
    kb.button(text="🔙 Orqaga",          callback_data="clients_back")
    kb.adjust(2)
    return kb.as_markup()


# ── Buyurtma ──────────────────────────────────────────────────────────────────

def order_status_kb(oid: int, status: str):
    from bot.config import STATUS_NEXT, ORDER_STATUSES
    kb = InlineKeyboardBuilder()
    if status in STATUS_NEXT:
        nxt = STATUS_NEXT[status]
        kb.button(text=f"➡️ {ORDER_STATUSES[nxt]}", callback_data=f"ostatus_{oid}_{nxt}")
    kb.button(text="📋 Tafsilot", callback_data=f"odetail_{oid}")
    kb.adjust(1)
    return kb.as_markup()


def orders_list_kb(orders):
    from bot.config import ORDER_STATUSES
    kb = InlineKeyboardBuilder()
    for o in orders:
        st = ORDER_STATUSES.get(o.get("status", ""), "")
        kb.button(
            text=f"#{o['id']} | {o.get('total', 0):,.0f} so'm | {st}",
            callback_data=f"odetail_{o['id']}"
        )
    kb.adjust(1)
    return kb.as_markup()


def order_client_confirm_kb(oid: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Ha, oldim!", callback_data=f"cconfirm_{oid}")
    kb.button(text="❌ Kelmadi",    callback_data=f"cnotyet_{oid}")
    kb.adjust(1)
    return kb.as_markup()


# ── Klient buyurtma ───────────────────────────────────────────────────────────

def _feature_on(key: str) -> bool:
    """Universal funksiya kaliti yoqilganmi? (barcode/channel/client_orders/nasiya)
    Xato bo'lsa — yoqilgan deb hisoblaydi (xavfsiz default)."""
    try:
        from database.channel_db import db
        return {
            "barcode":       db.is_barcode_enabled,
            "channel":       db.is_channel_enabled,
            "client_orders": db.is_client_orders_enabled,
            "nasiya":        db.is_nasiya_enabled,
            "categories":    db.is_categories_enabled,
            "suppliers":     db.is_suppliers_enabled,
            "cat_filter":    db.is_cat_filter_enabled,
            "cart_edit":     db.is_cart_edit_enabled,
            "quick_restock": db.is_quick_restock_enabled,
            "quick_prixod":  db.is_quick_prixod_enabled,
            "ai_analytics":  db.is_ai_analytics_enabled,
            "client_search": db.is_client_search_enabled,
            "ai_consult":    db.is_ai_consult_enabled,
            "mini_app":      db.is_mini_app_enabled,
        }[key]()
    except Exception:
        return True


def _wholesale_enabled() -> bool:
    """Optom narx funksiyasi yoqilganmi? Sozlamadan o'qiydi (xato bo'lsa — yoqilgan deb hisoblaydi)."""
    try:
        from database.channel_db import db
        return db.is_wholesale_enabled()
    except Exception:
        return True


def _dona_enabled() -> bool:
    """Dona narx funksiyasi yoqilganmi? Sozlamadan o'qiydi (xato bo'lsa — yoqilgan deb hisoblaydi)."""
    try:
        from database.channel_db import db
        return db.is_dona_enabled()
    except Exception:
        return True


def _effective_ctype(client_type: str = "dona") -> str:
    """Sozlamaga qarab haqiqiy ishlatiladigan mijoz turini qaytaradi.
    - Faqat optom yoqilgan → 'optom'
    - Faqat dona yoqilgan → 'dona'
    - Ikkalasi yoqilgan → mijozning o'z turi
    - Ikkalasi o'chirilgan → 'dona' (fallback)
    """
    dona_on = _dona_enabled()
    whs_on = _wholesale_enabled()
    if whs_on and not dona_on:
        return "optom"
    if dona_on and not whs_on:
        return "dona"
    if not dona_on and not whs_on:
        return "dona"
    return (client_type or "dona").lower()


def _price_for(p: dict, client_type: str = "dona") -> float:
    """Mijoz turiga qarab tegishli SO'M narxini qaytaradi (eski kod uchun)."""
    ctype = _effective_ctype(client_type)
    if ctype.startswith("opt"):
        whs = p.get("wholesale_price", 0) or 0
        if whs and whs > 0:
            return float(whs)
    return float(p.get("sell_price", 0))


def _price_usd_for(p: dict, client_type: str = "dona") -> float:
    """Mijoz turiga qarab tegishli USD narxini qaytaradi.
    Optomchi va wholesale_price_usd > 0 bo'lsa optom narxi, aks holda dona narxi."""
    ctype = _effective_ctype(client_type)
    if ctype.startswith("opt"):
        whs = p.get("wholesale_price_usd", 0) or 0
        if whs and whs > 0:
            return float(whs)
    return float(p.get("sell_price_usd", 0) or 0)


def _price_pair_for(p: dict, client_type: str = "dona") -> tuple:
    """(USD, so'm) juftligini qaytaradi. Sozlamaga (dona/optom yoqilgan/o'chirilgan) qarab tanlanadi."""
    ctype = _effective_ctype(client_type)
    if ctype.startswith("opt"):
        whs_usd = float(p.get("wholesale_price_usd", 0) or 0)
        whs_sum = float(p.get("wholesale_price", 0) or 0)
        if whs_usd > 0 or whs_sum > 0:
            return whs_usd, whs_sum
    return float(p.get("sell_price_usd", 0) or 0), float(p.get("sell_price", 0) or 0)


def _label_price(usd: float, summ: float, currency_mode: str = "hybrid") -> str:
    """Narx yorlig'ini valyuta rejimiga qarab qisqa formatlash (tugma matnida ishlatish)."""
    if currency_mode == "uzs_only":
        return f"{summ:,.0f} so'm"
    if currency_mode == "usd_only":
        return f"${usd:,.2f}" if usd > 0 else f"${summ:,.2f}"
    # hybrid: USD bo'lsa USD, aks holda so'm
    if usd > 0:
        return f"${usd:,.2f}"
    return f"{summ:,.0f} so'm"


def client_products_kb(products, cart: dict, client_type: str = "dona",
                       currency_mode: str = "hybrid", page: int = 0):
    """Mijoz buyurtma oynasi — SAHIFALANGAN (6/sahifa, sotuv reytingi bo'yicha).
    `products` — to'liq tartiblangan ro'yxat; funksiya sahifani o'zi kesadi."""
    kb = InlineKeyboardBuilder()
    page_items, page, pages = paginate(products, page)
    n = 0
    for p in page_items:
        in_cart = cart.get(str(p["id"]))
        qty_txt = f" [{in_cart['qty']:g}]" if in_cart else ""
        usd, summ = _price_pair_for(p, client_type)
        label = f"🛍️ {p['name']} — {_label_price(usd, summ, currency_mode)}{qty_txt}"
        kb.button(text=label, callback_data=f"order_add_{p['id']}")
        n += 1
    sizes = [1] * n
    nav = _add_page_nav(kb, page, pages, "order_page")
    if nav:
        sizes.append(nav)
    kb.button(text="✅ Tasdiqlash",    callback_data="order_confirm")
    kb.button(text="🗑️ Savatni tozala", callback_data="order_clear")
    sizes += [1, 1]
    kb.adjust(*sizes)
    return kb.as_markup()


# ── Sotuv (kassa) ─────────────────────────────────────────────────────────────

def sale_products_kb(products, cart: dict, client_type: str = "dona",
                     currency_mode: str = "hybrid", page: int = 0):
    """Kassa mahsulot ro'yxati — SAHIFALANGAN (6/sahifa, sotuv reytingi bo'yicha).
    1-sahifa = 6 ta eng ko'p sotiladigan mahsulot. Qolganini 🔍 qidiruv yoki
    ◀️▶️ sahifalash orqali topish mumkin.
    `products` — to'liq tartiblangan ro'yxat; funksiya sahifani o'zi kesadi."""
    kb = InlineKeyboardBuilder()
    page_items, page, pages = paginate(products, page)
    n = 0
    for p in page_items:
        in_cart = cart.get(str(p["id"]))
        unit = p.get("unit", "dona")
        qty_txt = f" ✅[{in_cart['qty']:g} {unit}]" if in_cart else ""
        usd, summ = _price_pair_for(p, client_type)
        label = f"📦 {p['name']} — {_label_price(usd, summ, currency_mode)}/{unit}{qty_txt}"
        kb.button(text=label, callback_data=f"sale_add_{p['id']}")
        n += 1
    sizes = [1] * n
    nav = _add_page_nav(kb, page, pages, "sale_page")
    if nav:
        sizes.append(nav)
    kb.button(text="🔍 ID / nom / shtrix-kod bilan qidirish", callback_data="sale_search")
    sizes.append(1)
    if _feature_on("categories") and _feature_on("cat_filter"):
        kb.button(text="🔻 Kategoriya bo'yicha", callback_data="scatf_open")
        sizes.append(1)
    kb.button(text="🧾 Kassaga o'tish",  callback_data="sale_checkout")
    if cart and _feature_on("cart_edit"):
        kb.button(text="✏️ Savatni tahrirlash", callback_data="sale_cart_edit")
        sizes.append(1)
    kb.button(text="🗑️ Tozalash",        callback_data="sale_clear")
    kb.button(text="❌ Bekor qilish",    callback_data="sale_cancel")
    sizes += [1, 1, 1]
    kb.adjust(*sizes)
    return kb.as_markup()


def sale_variants_kb(products: list, cart: dict, client_type: str = "dona", currency_mode: str = "hybrid"):
    """Qidiruv natijalari – variant tanlash"""
    kb = InlineKeyboardBuilder()
    for p in products:
        in_cart = cart.get(str(p["id"]))
        unit = p.get("unit", "dona")
        qty_txt = f" ✅[{in_cart['qty']:g}]" if in_cart else ""
        usd, summ = _price_pair_for(p, client_type)
        price_txt = f"{_label_price(usd, summ, currency_mode)}/{unit}"
        kb.button(
            text=f"🆔{p['id']} {p['name']} — {price_txt}{qty_txt}  ({p.get('qty',0):g} {unit} qoldi)",
            callback_data=f"sale_add_{p['id']}"
        )
    kb.button(text="🔙 Orqaga",  callback_data="sale_back_to_list")
    kb.button(text="❌ Bekor",   callback_data="sale_cancel")
    kb.adjust(1)
    return kb.as_markup()


def sale_payment_kb(has_discount: bool = False):
    kb = InlineKeyboardBuilder()
    kb.button(text="💵 Naqd",            callback_data="sale_pay_cash")
    kb.button(text="💳 Karta",           callback_data="sale_pay_card")
    # "🤝 Nasiya" — faqat Nasiya funksiyasi yoqilgan bo'lsa
    if _feature_on("nasiya"):
        kb.button(text="🤝 Nasiya (qarzga)", callback_data="sale_pay_nasiya")
    label = "♻️ Chegirma o'chirish" if has_discount else "💸 Chegirma berish (yumaloqlash)"
    kb.button(text=label,                callback_data="sale_discount")
    kb.button(text="❌ Bekor qilish",    callback_data="sale_cancel")
    kb.adjust(1)
    return kb.as_markup()


def sale_price_kb():
    """Mahsulotni savatga qo'shganda — narxni o'zgartirish/saqlash."""
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Aynan o'sha narx",       callback_data="sale_price_default")
    kb.button(text="✏️ Boshqa narx kiritish",  callback_data="sale_price_custom")
    kb.button(text="❌ Bekor qilish",          callback_data="sale_back_to_list")
    kb.adjust(1)
    return kb.as_markup()


def sale_paid_kb(method: str):
    """Kassada to'lov qabul qilishda: 'aynan' yoki 'boshqa summa'.
    method — 'cash' yoki 'card' (callback_datada uzatamiz)."""
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Aynan jami summa",       callback_data=f"sale_paid_exact_{method}")
    kb.button(text="✏️ Boshqa summa (so'm yoki $)", callback_data=f"sale_paid_custom_{method}")
    kb.button(text="🔙 Orqaga",                  callback_data="sale_paid_back")
    kb.adjust(1)
    return kb.as_markup()


def _client_btn_label(c: dict) -> str:
    debt_usd = float(c.get("debt_usd", 0) or 0)
    debt_sum = float(c.get("debt", 0) or 0)
    if debt_usd > 0:
        return f"👤 {c['shop_name']} (qarzi: ${debt_usd:,.2f})"
    if debt_sum > 0:
        return f"👤 {c['shop_name']} (qarzi: {debt_sum:,.0f} so'm)"
    return f"👤 {c['shop_name']}"


def sale_nasiya_clients_kb(clients: list):
    """Nasiya uchun mijoz tanlash"""
    kb = InlineKeyboardBuilder()
    for c in clients:
        kb.button(text=_client_btn_label(c),
                  callback_data=f"nasiya_client_{c['id']}")
    kb.button(text="🔙 Orqaga", callback_data="nasiya_back")
    kb.button(text="❌ Bekor",  callback_data="sale_cancel")
    kb.adjust(1)
    return kb.as_markup()


def sale_choose_client_kb(clients: list, limit: int = 12):
    """Sotuv boshida mijoz tanlash. Mijozsiz davom etish ham bor."""
    kb = InlineKeyboardBuilder()
    kb.button(text="🚶 Mijozsiz davom etish", callback_data="sale_no_client")
    kb.button(text="🔍 Mijozni qidirish",     callback_data="sale_client_search")
    for c in clients[:limit]:
        kb.button(text=_client_btn_label(c),
                  callback_data=f"sale_pick_client_{c['id']}")
    kb.button(text="❌ Bekor qilish", callback_data="sale_cancel")
    kb.adjust(1)
    return kb.as_markup()


def sale_client_search_results_kb(clients: list):
    """Qidiruv natijasidagi mijozlar"""
    kb = InlineKeyboardBuilder()
    for c in clients:
        kb.button(text=_client_btn_label(c),
                  callback_data=f"sale_pick_client_{c['id']}")
    kb.button(text="🔙 Orqaga",     callback_data="sale_client_back")
    kb.button(text="❌ Bekor",      callback_data="sale_cancel")
    kb.adjust(1)
    return kb.as_markup()


def sale_confirm_kb(sid: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="🧾 Yangi sotuv",      callback_data="sale_new")
    kb.button(text="📋 Chekni ko'rish",   callback_data=f"sale_receipt_{sid}")
    kb.button(text="🏠 Bosh panel",       callback_data="sale_go_home")
    kb.adjust(1)
    return kb.as_markup()


# ── Admin boshqaruv ───────────────────────────────────────────────────────────

def admins_list_kb(admins, glavniy_id: int, pending=None):
    """Adminlar ro'yxati. Har bir admin ustiga bosib tahrirlash sahifasi ochiladi.
    Bosh admin ham ro'yxatda, lekin uni o'chirish/rol o'zgartirib bo'lmaydi.
    `pending` — telefon orqali qo'shilgan, lekin hali botga kirmagan hodimlar;
    ular ⏳ bilan ko'rsatiladi, bosilsa — bekor qilish (o'chirish)."""
    from bot.permissions import ROLE_LABELS
    kb = InlineKeyboardBuilder()
    for a in admins:
        tg = a["telegram_id"]
        role = (a.get("role") or "full").strip().lower()
        role_label = ROLE_LABELS.get(role, role)
        mark = "👑 " if tg == glavniy_id else ""
        name = a.get("full_name") or str(tg)
        kb.button(
            text=f"{mark}{name} — {role_label}",
            callback_data=f"adm_open_{tg}"
        )
    for p in (pending or []):
        name = p.get("full_name") or p.get("phone") or "?"
        kb.button(
            text=f"⏳ {name} — kutilmoqda",
            callback_data=f"pend_open_{p['id']}"
        )
    kb.button(text="➕ Hodim qo'shish", callback_data="add_admin")
    kb.adjust(1)
    return kb.as_markup()


def stats_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="📅 Bugun",  callback_data="stats_today")
    kb.button(text="📆 Bu oy",  callback_data="stats_month")
    kb.adjust(2)
    return kb.as_markup()


def export_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Sotuvlar (bu oy)",   callback_data="export_sales_month")
    kb.button(text="📈 Sotuvlar (hammasi)", callback_data="export_sales_all")
    kb.button(text="👤 Mijozlar",            callback_data="export_clients")
    kb.button(text="💳 Qarzdorlar",          callback_data="export_debtors")
    kb.button(text="📦 Ombor holati",        callback_data="export_products")
    kb.button(text="🗄️ Backup (.zip)",       callback_data="export_backup")
    kb.adjust(1)
    return kb.as_markup()


# ── Kategoriyalar ─────────────────────────────────────────────────────────────

def categories_list_kb(categories):
    """Kategoriyalar ro'yxati — har biriga mahsulot soni bilan."""
    kb = InlineKeyboardBuilder()
    for c in categories:
        cnt = c.get("product_count", 0)
        kb.button(text=f"🗂 {c['name']}  ({cnt})",
                  callback_data=f"cat_open_{c['id']}")
    kb.button(text="➕ Kategoriya qo'shish", callback_data="cat_add")
    kb.adjust(1)
    return kb.as_markup()


def category_actions_kb(cid: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="📦 Mahsulotlari",         callback_data=f"cat_products_{cid}")
    kb.button(text="✏️ Nomini o'zgartirish",  callback_data=f"cat_edit_{cid}")
    kb.button(text="🗑 O'chirish",            callback_data=f"cat_del_{cid}")
    kb.button(text="🔙 Kategoriyalar",        callback_data="cat_back")
    kb.adjust(2)
    return kb.as_markup()


# ── Yetkazib beruvchilar ──────────────────────────────────────────────────────

def suppliers_list_kb(suppliers):
    """Yetkazib beruvchilar ro'yxati — mahsulot soni va past qoldiq soni bilan."""
    kb = InlineKeyboardBuilder()
    for s in suppliers:
        cnt = s.get("product_count", 0)
        low = s.get("low_count", 0)
        low_tag = f"  ⚠️{low}" if low else ""
        kb.button(text=f"🚚 {s['name']} — {cnt} mahsulot{low_tag}",
                  callback_data=f"sup_open_{s['id']}")
    kb.button(text="➕ Yetkazib beruvchi qo'shish", callback_data="sup_add")
    kb.adjust(1)
    return kb.as_markup()


def supplier_actions_kb(sid: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="📦 Mahsulotlari",          callback_data=f"sup_products_{sid}")
    kb.button(text="📋 Zakaz ro'yxati tuzish", callback_data=f"sup_order_{sid}")
    if _feature_on("quick_prixod"):
        kb.button(text="⚡ Tezkor prixod",      callback_data=f"sup_prixod_{sid}")
    kb.button(text="✏️ Tahrirlash",            callback_data=f"sup_edit_{sid}")
    kb.button(text="🗑 O'chirish",             callback_data=f"sup_del_{sid}")
    kb.button(text="🔙 Yetkazib beruvchilar",  callback_data="sup_back")
    kb.adjust(1)
    return kb.as_markup()


def supplier_edit_fields_kb(sid: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Nomi",     callback_data=f"supedit_{sid}_name")
    kb.button(text="✏️ Telefon",  callback_data=f"supedit_{sid}_phone")
    kb.button(text="✏️ Izoh",     callback_data=f"supedit_{sid}_note")
    kb.button(text="🔙 Orqaga",   callback_data=f"sup_open_{sid}")
    kb.adjust(2)
    return kb.as_markup()


# ── Kategoriya / Yetkazib beruvchi tanlash (mahsulot oqimida) ────────────────

def category_pick_kb(categories, prefix: str, allow_skip: bool = False,
                      allow_none: bool = False, current: int = 0):
    """Kategoriya tanlash klaviaturasi. callback: '{prefix}_{cid}'.
    allow_none → '🚫 Kategoriyasiz' ('{prefix}_0'),
    allow_skip → '⏭️ O'tkazib yuborish' ('{prefix}_skip')."""
    kb = InlineKeyboardBuilder()
    for c in categories:
        mark = "✅ " if current and current == c["id"] else ""
        kb.button(text=f"{mark}🗂 {c['name']}", callback_data=f"{prefix}_{c['id']}")
    if allow_none:
        mark = "✅ " if not current else ""
        kb.button(text=f"{mark}🚫 Kategoriyasiz", callback_data=f"{prefix}_0")
    if allow_skip:
        kb.button(text="⏭️ O'tkazib yuborish", callback_data=f"{prefix}_skip")
    kb.adjust(1)
    return kb.as_markup()


def supplier_pick_kb(suppliers, prefix: str, allow_skip: bool = False,
                     allow_none: bool = False, current: int = 0):
    """Yetkazib beruvchi tanlash klaviaturasi. callback: '{prefix}_{sid}'."""
    kb = InlineKeyboardBuilder()
    for s in suppliers:
        mark = "✅ " if current and current == s["id"] else ""
        kb.button(text=f"{mark}🚚 {s['name']}", callback_data=f"{prefix}_{s['id']}")
    if allow_none:
        mark = "✅ " if not current else ""
        kb.button(text=f"{mark}🚫 Belgilanmagan", callback_data=f"{prefix}_0")
    if allow_skip:
        kb.button(text="⏭️ O'tkazib yuborish", callback_data=f"{prefix}_skip")
    kb.adjust(1)
    return kb.as_markup()


# ── Oddiy (sahifalanmagan) mahsulot ro'yxati — filtr/bo'lim natijalari ───────

def simple_products_kb(products, mode: str = "view", back_cb: str = "prod_back"):
    """Sahifalanmagan mahsulot ro'yxati (kategoriya/yetkazib beruvchi ichi).
    callback: 'prod_{mode}_{pid}'. Telegram limiti uchun 90 ta bilan cheklangan."""
    kb = InlineKeyboardBuilder()
    for p in products[:90]:
        icon = "🟢" if p.get("qty", 0) > 0 else "🔴"
        unit = p.get("unit", "dona")
        kb.button(text=f"{icon} {p['name']} ({p.get('qty', 0):g} {unit})",
                  callback_data=f"prod_{mode}_{p['id']}")
    kb.button(text="🔙 Orqaga", callback_data=back_cb)
    kb.adjust(1)
    return kb.as_markup()


# ── Kategoriya bo'yicha filtr (mahsulotlar / sotuv) ─────────────────────────

def category_filter_kb(categories, prefix: str):
    """Kategoriya filtri. callback: '{prefix}_all' | '{prefix}_0' | '{prefix}_{cid}'.
    prefix: 'pcatf' (mahsulotlar bo'limi) yoki 'scatf' (sotuv)."""
    kb = InlineKeyboardBuilder()
    kb.button(text="📋 Barcha mahsulotlar", callback_data=f"{prefix}_all")
    for c in categories:
        cnt = c.get("product_count", 0)
        kb.button(text=f"🗂 {c['name']}  ({cnt})", callback_data=f"{prefix}_{c['id']}")
    kb.button(text="🚫 Kategoriyasiz", callback_data=f"{prefix}_0")
    kb.button(text="🔙 Orqaga", callback_data=f"{prefix}_back")
    kb.adjust(1)
    return kb.as_markup()


# ── Miqdor yig'uvchi (zakaz ro'yxati / tezkor prixod) ───────────────────────

def qty_builder_kb(products, accum: dict, prefix: str,
                   action_label: str = "✅ Saqlash"):
    """Mahsulotlarga miqdor yig'ish klaviaturasi.
    accum — {pid: qty} (str yoki int kalit). callback: '{prefix}_item_{pid}'.
    prefix: 'sord' (zakaz ro'yxati) | 'qpx' (tezkor prixod)."""
    kb = InlineKeyboardBuilder()
    for p in products[:80]:
        pid = p["id"]
        unit = p.get("unit", "dona")
        icon = "🔴" if (p.get("qty", 0) or 0) <= 0 else "🟢"
        added = accum.get(str(pid), accum.get(pid, 0))
        tag = f"  ➕{added:g}" if added else ""
        kb.button(
            text=f"{icon} {p['name']} (qoldi {p.get('qty', 0):g} {unit}){tag}",
            callback_data=f"{prefix}_item_{pid}"
        )
    if accum:
        kb.button(text=action_label, callback_data=f"{prefix}_save")
        kb.button(text="🗑 Ro'yxatni tozalash", callback_data=f"{prefix}_clear")
    kb.button(text="🔙 Orqaga", callback_data=f"{prefix}_back")
    kb.adjust(1)
    return kb.as_markup()


def qty_builder_item_kb(pid: int, prefix: str, step: int = 10):
    """Bitta mahsulot uchun miqdor tahrirlagich (➖/➕/aniq/o'chir)."""
    kb = InlineKeyboardBuilder()
    kb.button(text=f"➖{step}", callback_data=f"{prefix}_dec_{pid}")
    kb.button(text=f"➕{step}", callback_data=f"{prefix}_inc_{pid}")
    kb.button(text="✏️ Aniq miqdor",      callback_data=f"{prefix}_set_{pid}")
    kb.button(text="🗑 Ro'yxatdan olib tashlash", callback_data=f"{prefix}_rm_{pid}")
    kb.button(text="🔙 Ro'yxatga qaytish", callback_data=f"{prefix}_list")
    kb.adjust(2, 1, 1, 1)
    return kb.as_markup()


# ── Past qoldiq — tezkor to'ldirish ──────────────────────────────────────────

def low_stock_kb(items):
    """Past qoldiq ro'yxati — har bir tovarga tezkor to'ldirish tugmasi."""
    kb = InlineKeyboardBuilder()
    for p in items[:40]:
        unit = p.get("unit", "dona")
        kb.button(text=f"⚡ {p['name']} — {p.get('qty', 0):g} {unit}",
                  callback_data=f"qrs_{p['id']}")
    kb.adjust(1)
    return kb.as_markup()


# ── Savatni tahrirlash (kassa) ───────────────────────────────────────────────

def cart_edit_kb(cart: dict):
    """Savatdagi qatorlar ro'yxati — tahrirlash uchun."""
    kb = InlineKeyboardBuilder()
    for key, v in cart.items():
        unit = v.get("unit", "dona")
        kb.button(
            text=f"✏️ {v['name']} — {v['qty']:g} {unit} × ${v['price']:,.2f}",
            callback_data=f"cedit_{key}"
        )
    kb.button(text="✅ Tayyor (ro'yxatga qaytish)", callback_data="cedit_done")
    kb.adjust(1)
    return kb.as_markup()


def client_product_card_kb(pid: int, can_order: bool, ai_on: bool = True):
    """Mijoz mahsulot kartasi tagidagi tugmalar.
    can_order = True bo'lsa va qoldiq bor bo'lsa — 'Buyurtma qilish' tugmasi.
    ai_on = True bo'lsa — '✨ Tushuntirib bering' tugmasi (AI sotuvchi)."""
    kb = InlineKeyboardBuilder()
    if can_order:
        kb.button(text="🛒 Buyurtma qilish",   callback_data=f"cs_order_{pid}")
    if ai_on:
        kb.button(text="✨ AI: tushuntirib bering", callback_data=f"cs_pitch_{pid}")
    kb.button(text="🔎 Boshqa mahsulot qidirish", callback_data="cs_again")
    kb.adjust(1)
    return kb.as_markup()


def client_consult_followup_kb(mentioned_products: list):
    """AI konsultatsiyasidan keyin: tavsiya etilgan tovarlarni ko'rish tugmalari."""
    kb = InlineKeyboardBuilder()
    for p in mentioned_products[:6]:
        unit = p.get("unit", "dona")
        kb.button(
            text=f"📦 {p['name'][:30]} ({p.get('qty', 0):g} {unit})",
            callback_data=f"cs_view_{p['id']}"
        )
    kb.button(text="💬 Yana savol berish", callback_data="cc_again")
    kb.adjust(1)
    return kb.as_markup()


def client_search_results_kb(products):
    """Mijoz qidiruvida 2+ natija bo'lsa — har birini tanlash."""
    kb = InlineKeyboardBuilder()
    for p in products[:20]:
        unit = p.get("unit", "dona")
        icon = "🟢" if (p.get("qty", 0) or 0) > 0 else "🔴"
        kb.button(
            text=f"{icon} {p['name']} ({p.get('qty', 0):g} {unit})",
            callback_data=f"cs_view_{p['id']}"
        )
    kb.button(text="🔎 Yana qidirish", callback_data="cs_again")
    kb.adjust(1)
    return kb.as_markup()


def ai_menu_kb():
    """🤖 AI Analitika asosiy menyusi — preset savollar + erkin savol."""
    kb = InlineKeyboardBuilder()
    kb.button(text="📈 Top sotuvlar va nimani buyurtma qilish", callback_data="ai_top")
    kb.button(text="❓ Qidirilgan, lekin yo'q tovarlar",         callback_data="ai_misses")
    kb.button(text="📉 Sotilmayotgan tovarlar",                  callback_data="ai_slow")
    kb.button(text="💡 Umumiy biznes maslahat",                  callback_data="ai_general")
    kb.button(text="💬 O'zim savol beraman",                     callback_data="ai_ask")
    kb.adjust(1)
    return kb.as_markup()


def mini_app_kb(url: str):
    """Inline tugma: Mini App'ni ochish."""
    kb = InlineKeyboardBuilder()
    kb.button(text="🌐 Mini App'ni ochish", web_app=WebAppInfo(url=url))
    return kb.as_markup()


def cart_item_kb(key: str):
    """Bitta savat qatori uchun tahrirlagich."""
    kb = InlineKeyboardBuilder()
    kb.button(text="➖1", callback_data=f"citem_dec_{key}")
    kb.button(text="➕1", callback_data=f"citem_inc_{key}")
    kb.button(text="✏️ Miqdor kiritish", callback_data=f"citem_set_{key}")
    kb.button(text="🗑 O'chirish",        callback_data=f"citem_rm_{key}")
    kb.button(text="🔙 Savat",            callback_data=f"citem_back")
    kb.adjust(2, 1, 1, 1)
    return kb.as_markup()
