import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GLAVNIY_ADMIN_ID = int(os.getenv("GLAVNIY_ADMIN_ID", "0"))
CHANNEL_ID = os.getenv("CHANNEL_ID", "")

# ── Chek turlariga qarab maxsus kanallar (avtomatik post) ─────────────────
# Bot SHU kanallarning har biriga admin bo'lishi shart. Chek turi aniqlanib,
# tegishli kanalga avtomatik post tashlanadi. .env orqali o'zgartirsa bo'ladi.
#   • CHANNEL_INTERNAL_ID   — «Chinor» ichki rasxod cheklari
#   • CHANNEL_OUTOFSTOCK_ID — qolmagan (tugagan) tovarlar cheki
#   • CHANNEL_SALES_ID      — hamma oddiy sotuvlar
#   • CHANNEL_DRAFTS_ID     — qoralama (parked) cheklar
CHANNEL_INTERNAL_ID   = os.getenv("CHANNEL_INTERNAL_ID",   "-1004464526447").strip()
CHANNEL_OUTOFSTOCK_ID = os.getenv("CHANNEL_OUTOFSTOCK_ID", "-1004434424899").strip()
CHANNEL_SALES_ID      = os.getenv("CHANNEL_SALES_ID",      "-1004476274146").strip()
CHANNEL_DRAFTS_ID     = os.getenv("CHANNEL_DRAFTS_ID",     "-1004451962859").strip()

# ── To'lov-link boti (kassa QR to'lovi uchun) ─────────────────────────────
# Kassa "QR to'lov" bosilganda egaga (PAYMENT_OWNER_IDS) xabar boradi; ega
# to'lov linkini SHU botga yuboradi, server uni ushlab kassaga uzatadi.
# ASOSIY botdan ALOHIDA token bo'lishi shart (getUpdates to'qnashmasligi uchun).
# Bo'sh qoldirilsa — QR to'lov funksiyasi o'chiq (kassa chekni QR'siz chop etadi).
PAYMENT_BOT_TOKEN = os.getenv("PAYMENT_BOT_TOKEN", "").strip()
# Linkni kim yuborishi mumkin (vergul bilan, Telegram ID lar). Bo'sh bo'lsa —
# GLAVNIY_ADMIN_ID ishlatiladi. Eski kassa qotirgan ID ham standart qo'shiladi.
_pay_owners_env = os.getenv("PAYMENT_OWNER_IDS", "").strip()
if _pay_owners_env:
    PAYMENT_OWNER_IDS = [int(x) for x in _pay_owners_env.replace(" ", "").split(",") if x]
else:
    PAYMENT_OWNER_IDS = [i for i in (GLAVNIY_ADMIN_ID, 6787907623) if i]

# Past qoldiq ostonasi — bu sondan kam (yoki teng) qolgan tovarlar uchun
# avtomatik admin(lar)ga ogohlantirish yuboriladi.
# Standart 0 — faqat tovar TUGAGANDA xabar keladi.
LOW_STOCK_THRESHOLD = float(os.getenv("LOW_STOCK_THRESHOLD", "0"))

# Avtomatik backup oraliq vaqti (soatda). 0 bo'lsa o'chiriladi
BACKUP_INTERVAL_HOURS = int(os.getenv("BACKUP_INTERVAL_HOURS", "24"))

# ── Vaqt mintaqasi ────────────────────────────────────────────────────────
# Toshkent — UTC+5. Boshqa shaharga moslamoqchi bo'lsangiz .env da TZ_OFFSET_HOURS o'zgartiring.
TZ_OFFSET_HOURS = int(os.getenv("TZ_OFFSET_HOURS", "5"))

# ── USD kurs ──────────────────────────────────────────────────────────────
# Boshlang'ich (default) USD→so'm kursi. Bot ishga tushganda DB da
# settings.usd_rate yo'q bo'lsa shu qiymat saqlanadi. Keyinchalik
# admin "💱 Kurs" tugmasi orqali o'zgartira oladi.
USD_RATE_DEFAULT = float(os.getenv("USD_RATE", "12500"))

IMAGES_DIR = os.path.join(os.path.dirname(__file__), "images")
os.makedirs(IMAGES_DIR, exist_ok=True)

# ── Google Gemini AI (analitika uchun) ───────────────────────────────────
# AI 'analitika' tugmasi ishlashi uchun .env ga GEMINI_API_KEY qo'shing
# (https://aistudio.google.com/app/apikey dan oling). Bo'sh bo'lsa — AI
# tugmasi 'sozlanmagan' deb javob beradi.
# ── Mini App (WebApp) URL ────────────────────────────────────────────────
# Telegram WebApp Mini App'ning to'liq HTTPS manzili. Bot menyusiga
# '🌐 Mini App' tugmasi shu URL'ga oladi. .env da o'rnating:
#   MINI_APP_URL=https://silver-alfajores-15ae4c.netlify.app
# Bo'sh qoldirsangiz — Mini App tugmasi ko'rinmaydi.
MINI_APP_URL = os.getenv("MINI_APP_URL", "").strip()

# ── Kanal posti — aloqa va marketing (sozlanadigan) ───────────────────────
# Har bir mahsulot kanalga tashlanganda post tagiga qo'shiladigan ma'lumot.
# .env orqali o'zgartirish mumkin (bot qayta ishga tushirilsa yetadi).
CONTACT_PHONE_1 = os.getenv("CONTACT_PHONE_1", "+998 77 877 55 33").strip()
CONTACT_PHONE_2 = os.getenv("CONTACT_PHONE_2", "+998 77 877 55 99").strip()
SHOP_LOCATION   = os.getenv("SHOP_LOCATION", "Sergeli, Chinor bekati yoni").strip()
SHOP_HOURS      = os.getenv("SHOP_HOURS", "har kuni 8:00–22:00").strip()
# Diktor uchun — "8:00–22:00" ni TTS g'alati o'qiydi ("sakkiz nol nol tire…")
SHOP_HOURS_SAY  = os.getenv("SHOP_HOURS_SAY",
                            "har kuni ertalab soat sakkizdan kechki o'ngacha").strip()

# ── HTTPS API (Mini App uchun login endpoint) ────────────────────────────
# Bot bilan birga aiohttp serveri ishga tushadi. Frontend (Netlify) shu
# manzilga POST /api/login yuborib, javobiga qarab admin yoki klient
# sahifasini ochadi.
API_HOST  = os.getenv("API_HOST", "0.0.0.0").strip()
API_PORT  = int(os.getenv("API_PORT", "8765"))
API_ENABLED = os.getenv("API_ENABLED", "1").strip() not in ("0", "false", "no")
# Netlify domain — CORS uchun (yulduzcha xavfsizroq alternativa o'rniga).
# Bo'sh qoldirsangiz har qanday origin'ga ruxsat beriladi (faqat test uchun).
CORS_ALLOW_ORIGIN = os.getenv(
    "CORS_ALLOW_ORIGIN",
    "https://silver-alfajores-15ae4c.netlify.app"
).strip()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY   = os.getenv("GROQ_API_KEY", "")
# Groq ko'rish (vision) modeli — kanal posti uchun rasmdan tavsif yozadi.
# 2026-08-04: Groq `llama-4-scout` ni olib tashladi (404 model_not_found) va
# hisobda birorta ham vision model qolmadi, shuning uchun STANDART BO'SH —
# ya'ni Groq o'tkazib yuborilib, to'g'ridan-to'g'ri Gemini ishlatiladi.
# Groq vision modelini qaytarsa, .env ga GROQ_VISION_MODEL yozib yoqiladi.
GROQ_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "").strip()
# Gemini 1.5 oilasi 2025-yil oxiriga kelib eskirgan ("404 not found" xatosi
# kelib chiqadi). Hozirgi tavsiya — 2.5 flash (tez, arzon). Avtomatik
# yangilanishini xohlasangiz, 'gemini-flash-latest' ishlatishingiz mumkin.
GEMINI_MODEL   = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

ORDER_STATUSES = {
    "accepted":  "✅ Qabul qilindi",
    "on_way":    "🚚 Yo'lda",
    "delivered": "📦 Yetkazildi",
    "confirmed": "🎉 Klient tasdiqladi",
    "cancelled": "❌ Bekor qilindi",
}
STATUS_NEXT = {
    "accepted": "on_way",
    "on_way":   "delivered",
}
