"""Marketplace integratsiyasi sozlamalari.

Barcha maxfiy qiymatlar (API-Key) faqat `.env` dan o'qiladi — kodga yozilmaydi.
Namuna uchun `.env.example` ga qarang.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── YesPOS marketplace gateway ────────────────────────────────────────────
BASE_URL = os.getenv("YESPOS_BASE_URL", "https://marketplace.yestask.uz/api/v1").rstrip("/")
API_KEY = os.getenv("YESPOS_API_KEY", "").strip()
# Filial (Branch header). `branch/list` dan olinadi — Chinor uchun 1.
BRANCH = os.getenv("YESPOS_BRANCH", "1").strip()
# Buyurtma yaratishda yuboriladigan AppName header.
APP_NAME = os.getenv("YESPOS_APP_NAME", "YesPOS").strip()

# So'rov timeout (sekund) va tarmoq xatosida qayta urinishlar soni.
TIMEOUT = int(os.getenv("YESPOS_TIMEOUT", "60"))
RETRIES = int(os.getenv("YESPOS_RETRIES", "3"))

# ── Ma'lumotlar bazasi ────────────────────────────────────────────────────
# Standart: repo ildizidagi data/marketplace.db (chinor-bot bilan bir xil odat).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.getenv("DATA_DIR", os.path.join(_REPO_ROOT, "data"))
DB_PATH = os.getenv("MARKETPLACE_DB", os.path.join(_DATA_DIR, "marketplace.db"))
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# Tovar rasmlari — API rasm bermasligi mumkin, shuning uchun id bo'yicha
# o'zimizda saqlaymiz: IMAGES_DIR/<product_id>.jpg
IMAGES_DIR = os.getenv("MARKETPLACE_IMAGES", os.path.join(_DATA_DIR, "marketplace-images"))
os.makedirs(IMAGES_DIR, exist_ok=True)

# ── Vaqt mintaqasi ────────────────────────────────────────────────────────
# Toshkent — UTC+5. Kunlik snapshot sanasi shu mintaqa bo'yicha hisoblanadi.
TZ_OFFSET_HOURS = int(os.getenv("TZ_OFFSET_HOURS", "5"))

# ── Analitika ostonalari ──────────────────────────────────────────────────
# Muzlagan tovar: qoldig'i shuncha kundan beri umuman kamaymagan.
DEAD_STOCK_DAYS = int(os.getenv("DEAD_STOCK_DAYS", "30"))
# Tugagan-u keltirilmayotgan tovar: qoldig'i shuncha kundan beri 0.
OUT_OF_STOCK_DAYS = int(os.getenv("OUT_OF_STOCK_DAYS", "7"))

# ── Telegram userbot (YesPOS chek xabarlarini o'qish) ─────────────────────
# Bot boshqa botning xabarini ko'rmaydi, shuning uchun cheklar shaxsiy akkaunt
# nomidan o'qiladi (`tg_receipts.py`). Kalitlar my.telegram.org dan olinadi va
# FAQAT `.env` da turadi.
TG_API_ID = os.getenv("TG_API_ID", "").strip()
TG_API_HASH = os.getenv("TG_API_HASH", "").strip()
# Sessiya fayli — akkauntga to'liq kalit. DATA_DIR ichida, git'dan tashqarida.
TG_SESSION = os.getenv("TG_SESSION", os.path.join(_DATA_DIR, "tg_userbot"))
# Chek yuboradigan bot (masalan "@yespos_bot") — `dialogs` buyrug'i topadi.
YESPOS_BOT = os.getenv("YESPOS_BOT", "").strip()

# ── Chinor POS bazasi (ko'prik uchun) ─────────────────────────────────────
# YesPOS qoldiq/narxi shu bazaga ko'chiriladi. Ulash kaliti:
# Chinor `products.id` == YesPOS `sku` (98.8% moslik tekshirilgan).
POS_DB = os.getenv("POS_DB", os.path.join(_DATA_DIR, "pos.db"))


def load_bot_env() -> None:
    """chinor-bot/.env dagi kalitlarni yuklaydi (BOT_TOKEN, GEMINI_API_KEY, ...).

    Nega kerak: marketplace vositalari repo ildizidagi `.env` ni o'qiydi
    (YesPOS kaliti, DATA_DIR). Bot kalitlari esa ALOHIDA faylda —
    `chinor-bot/.env`. Ularni ikkinchi joyga ko'chirmaymiz (bitta maxfiy
    qiymat bitta joyda tursin), shuning uchun kerak bo'lganda shu yerdan
    o'qiymiz. Buni unutish jim nosozlikka olib keladi: AI "kalit yo'q" deb
    yiqiladi, sababi esa ko'rinmaydi.
    """
    if os.getenv("BOT_TOKEN") and os.getenv("GEMINI_API_KEY"):
        return
    for candidate in (
        os.path.join(_REPO_ROOT, "chinor-bot", ".env"),
        "/opt/chinor/chinor-bot/.env",
    ):
        if os.path.exists(candidate):
            load_dotenv(candidate, override=False)
            return
