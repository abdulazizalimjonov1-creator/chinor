import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GLAVNIY_ADMIN_ID = int(os.getenv("GLAVNIY_ADMIN_ID", "0"))
CHANNEL_ID = os.getenv("CHANNEL_ID", "")

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
# Gemini 1.5 oilasi 2025-yil oxiriga kelib eskirgan ("404 not found" xatosi
# kelib chiqadi). Hozirgi tavsiya — 2.5 flash (tez, arzon). Avtomatik
# yangilanishini xohlasangiz, 'gemini-flash-latest' ishlatishingiz mumkin.
GEMINI_MODEL   = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

ORDER_STATUSES = {
    "accepted":  "✅ Qabul qilindi",
    "on_way":    "🚚 Yo'lda",
    "delivered": "📦 Yetkazildi",
    "confirmed": "🎉 Klient tasdiqladi",
}
STATUS_NEXT = {
    "accepted": "on_way",
    "on_way":   "delivered",
}
