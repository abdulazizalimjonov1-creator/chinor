"""ChannelDB poydevori — ulanish, sxema, migratsiya, kanal posti,
qator->dict aylantirgichlar, bulk o'qishlar va sozlamalar.

Bu `BaseDB` mixin'i — `ChannelDB` undan meros oladi (boshqa mixinlar bilan birga).
"""

import os
import json
import asyncio
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Callable, Tuple
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.config import CHANNEL_ID, LOW_STOCK_THRESHOLD, USD_RATE_DEFAULT
from database._helpers import DB_PATH, SCHEMA, now_local, _now, usd_to_sum


# ─── Doimiy ulanish o'ralmasi ────────────────────────────────────────────────

class _PersistentConn:
    """sqlite3.Connection ustidan yupqa o'ralma.

    Eski kod har so'rovda `conn = self._conn()` ... `finally: conn.close()`
    naqshini ishlatadi. Endi `_conn()` doimiy (bitta) ulanishni qaytaradi,
    shuning uchun `.close()` ATAYIN hech narsa qilmaydi — aks holda eski
    `finally` bloklari umumiy ulanishni yopib qo'yardi.

    Qolgan barcha atributlar (execute, executescript, commit, ...) to'g'ridan
    -to'g'ri haqiqiy ulanishga uzatiladi.
    """
    __slots__ = ("_real",)

    def __init__(self, real: sqlite3.Connection):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def close(self):
        # Doimiy ulanish — yopilmaydi (no-op).
        pass

    def _really_close(self):
        """Faqat bot to'xtaganda — haqiqiy yopish uchun."""
        try:
            self._real.close()
        except Exception:
            pass




class BaseDB:
    def __init__(self):
        self._lock = threading.RLock()
        self._bot: Optional[Bot] = None
        self._bot_username: str = ""
        self._low_stock_alert: Optional[Callable] = None

        # ── Doimiy ulanish ──────────────────────────────────────────────────
        raw = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA foreign_keys = ON")
        raw.execute("PRAGMA journal_mode = WAL")
        raw.execute("PRAGMA synchronous = NORMAL")  # WAL bilan xavfsiz, tezroq
        self._raw_conn = raw
        self._persistent = _PersistentConn(raw)

        # ── Og'ir o'qishlar uchun bitta ishchi oqim ─────────────────────────
        # max_workers=1 — barcha DB amallari navbatga tushadi, poyga holati yo'q.
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pos-db")

        self._init_db()

    async def _in_thread(self, fn: Callable, *args):
        """Sinxron DB funksiyasini ishchi oqimda ishga tushiradi —
        event loop bloklanmaydi."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._pool, fn, *args)

    def shutdown(self):
        """Bot to'xtaganda chaqirilsa — ulanishni va oqimni toza yopadi.
        Chaqirilmasa ham xavfsiz (WAL crash-safe)."""
        try:
            self._pool.shutdown(wait=True)
        except Exception:
            pass
        self._persistent._really_close()

    # ESLATMA: eski `_products`/`_clients`/`_sales`/`_orders`/`_payments`/
    # `_admins` xususiyatlari OLIB TASHLANDI — ular hech qaerda ishlatilmagan
    # o'lik kod edi va har chaqirilganda butun jadvalni skanlardi.
    # Kerak bo'lsa `get_all_products()` / `get_all_clients()` ... ishlating.

    # ── Init ─────────────────────────────────────────────────────────────────

    def _init_db(self):
        with self._lock:
            conn = self._conn()
            try:
                conn.executescript(SCHEMA)
                # ── Migratsiya: eski DB ga yangi ustunlarni qo'shish ─────────
                self._migrate(conn)
                conn.commit()
            finally:
                conn.close()

    def _migrate(self, conn: sqlite3.Connection):
        """Mavjud DB shemalarini xavfsiz yangilash (yangi ustunlar qo'shish)."""
        # products.wholesale_price
        prod_cols = {r[1] for r in conn.execute("PRAGMA table_info(products)").fetchall()}
        if "wholesale_price" not in prod_cols:
            conn.execute("ALTER TABLE products ADD COLUMN wholesale_price REAL DEFAULT 0")
        # products: USD narxlar
        for col in ("sell_price_usd", "wholesale_price_usd", "cost_price_usd"):
            if col not in prod_cols:
                conn.execute(f"ALTER TABLE products ADD COLUMN {col} REAL DEFAULT 0")
        # products.barcode (shtrix-kod)
        if "barcode" not in prod_cols:
            conn.execute("ALTER TABLE products ADD COLUMN barcode TEXT DEFAULT ''")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_products_barcode ON products(barcode)")
        # products.category_id / products.supplier_id (kategoriya / yetkazib beruvchi)
        if "category_id" not in prod_cols:
            conn.execute("ALTER TABLE products ADD COLUMN category_id INTEGER DEFAULT 0")
        if "supplier_id" not in prod_cols:
            conn.execute("ALTER TABLE products ADD COLUMN supplier_id INTEGER DEFAULT 0")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_products_supplier ON products(supplier_id)")
        # products.image_url — Mini App orqali yuklangan rasm (web uchun).
        # Bot esa image_file_id (Telegram) dan foydalanishda davom etadi.
        if "image_url" not in prod_cols:
            conn.execute("ALTER TABLE products ADD COLUMN image_url TEXT DEFAULT ''")
        # clients.client_type
        cli_info = conn.execute("PRAGMA table_info(clients)").fetchall()
        cli_cols = {r[1] for r in cli_info}
        if "client_type" not in cli_cols:
            conn.execute("ALTER TABLE clients ADD COLUMN client_type TEXT DEFAULT 'dona'")
        # clients.telegram_id ni NULL ruxsat berish (qayta tuzish kerak)
        tg_row = next((r for r in cli_info if r[1] == "telegram_id"), None)
        if tg_row is not None and tg_row[3] == 1:
            # NOT NULL bor — jadvalni qayta tuzamiz
            conn.execute("ALTER TABLE clients RENAME TO _clients_old")
            conn.execute("""
                CREATE TABLE clients (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id     INTEGER UNIQUE,
                    shop_name       TEXT    NOT NULL,
                    phone           TEXT    DEFAULT '',
                    debt            REAL    DEFAULT 0,
                    client_type     TEXT    DEFAULT 'dona',
                    registered_by   INTEGER,
                    created_at      TEXT    NOT NULL,
                    channel_msg_id  INTEGER DEFAULT 0
                )
            """)
            old_cols = [r[1] for r in cli_info]
            cols_csv = ",".join(old_cols)
            conn.execute(f"INSERT INTO clients({cols_csv}) SELECT {cols_csv} FROM _clients_old")
            conn.execute("DROP TABLE _clients_old")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_clients_tg ON clients(telegram_id)")
        # clients.debt_usd
        cli_cols2 = {r[1] for r in conn.execute("PRAGMA table_info(clients)").fetchall()}
        if "debt_usd" not in cli_cols2:
            conn.execute("ALTER TABLE clients ADD COLUMN debt_usd REAL DEFAULT 0")
        # sales — keng USD/chegirma ustunlari
        sale_cols = {r[1] for r in conn.execute("PRAGMA table_info(sales)").fetchall()}
        sale_new_cols = [
            ("total_usd",      "REAL DEFAULT 0"),
            ("subtotal",       "REAL DEFAULT 0"),
            ("subtotal_usd",   "REAL DEFAULT 0"),
            ("discount",       "REAL DEFAULT 0"),
            ("discount_usd",   "REAL DEFAULT 0"),
            ("usd_rate",       "REAL DEFAULT 0"),
            ("paid_cash_usd",  "REAL DEFAULT 0"),
            ("paid_card_usd",  "REAL DEFAULT 0"),
            ("paid_other_usd", "REAL DEFAULT 0"),
            ("paid_total_usd", "REAL DEFAULT 0"),
            ("paid_currency",  "TEXT DEFAULT 'sum'"),
            ("change_usd",     "REAL DEFAULT 0"),
        ]
        for col, ddl in sale_new_cols:
            if col not in sale_cols:
                conn.execute(f"ALTER TABLE sales ADD COLUMN {col} {ddl}")
        # payments USD ustunlari
        pay_cols = {r[1] for r in conn.execute("PRAGMA table_info(payments)").fetchall()}
        for col, ddl in [
            ("amount_usd", "REAL DEFAULT 0"),
            ("currency",   "TEXT DEFAULT 'sum'"),
            ("usd_rate",   "REAL DEFAULT 0"),
        ]:
            if col not in pay_cols:
                conn.execute(f"ALTER TABLE payments ADD COLUMN {col} {ddl}")
        # admins: rollar, ruxsatlar, valyuta rejimi va Mini App credentials
        admin_cols = {r[1] for r in conn.execute("PRAGMA table_info(admins)").fetchall()}
        for col, ddl in [
            ("role",          "TEXT DEFAULT 'full'"),
            ("permissions",   "TEXT DEFAULT ''"),
            ("currency_mode", "TEXT DEFAULT ''"),
            ("phone",         "TEXT DEFAULT ''"),
            ("username",      "TEXT DEFAULT ''"),
            ("password_hash", "TEXT DEFAULT ''"),
        ]:
            if col not in admin_cols:
                conn.execute(f"ALTER TABLE admins ADD COLUMN {col} {ddl}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_admins_username ON admins(username)")
        # clients: Mini App credentials
        cli_cols3 = {r[1] for r in conn.execute("PRAGMA table_info(clients)").fetchall()}
        for col, ddl in [
            ("username",      "TEXT DEFAULT ''"),
            ("password_hash", "TEXT DEFAULT ''"),
        ]:
            if col not in cli_cols3:
                conn.execute(f"ALTER TABLE clients ADD COLUMN {col} {ddl}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_clients_username ON clients(username)")
        # Eski adminlarning roli bo'sh bo'lsa — 'full' qilamiz (hech narsa buzilmasin)
        conn.execute(
            "UPDATE admins SET role='full' WHERE role IS NULL OR role=''"
        )
        # settings: usd_rate boshlang'ich qiymati
        row = conn.execute(
            "SELECT value FROM settings WHERE key='usd_rate'"
        ).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?)",
                ("usd_rate", str(USD_RATE_DEFAULT))
            )
        # Eski mahsulotlarda USD narxlari yo'q bo'lsa — joriy kurs bo'yicha
        # so'm narxidan hisoblab to'ldiramiz (tariximiz buzilmasin uchun bir martalik).
        try:
            cur_rate = float(conn.execute(
                "SELECT value FROM settings WHERE key='usd_rate'"
            ).fetchone()[0])
        except Exception:
            cur_rate = USD_RATE_DEFAULT
        if cur_rate > 0:
            conn.execute("""
                UPDATE products
                   SET sell_price_usd = ROUND(sell_price / ?, 4)
                 WHERE COALESCE(sell_price_usd, 0) = 0 AND COALESCE(sell_price, 0) > 0
            """, (cur_rate,))
            conn.execute("""
                UPDATE products
                   SET cost_price_usd = ROUND(cost_price / ?, 4)
                 WHERE COALESCE(cost_price_usd, 0) = 0 AND COALESCE(cost_price, 0) > 0
            """, (cur_rate,))
            conn.execute("""
                UPDATE products
                   SET wholesale_price_usd = ROUND(wholesale_price / ?, 4)
                 WHERE COALESCE(wholesale_price_usd, 0) = 0
                   AND COALESCE(wholesale_price, 0) > 0
            """, (cur_rate,))
            # Eski qarzni USDga aylantiramiz
            conn.execute("""
                UPDATE clients
                   SET debt_usd = ROUND(debt / ?, 4)
                 WHERE COALESCE(debt_usd, 0) = 0 AND COALESCE(debt, 0) > 0
            """, (cur_rate,))

    def _conn(self):
        """DOIMIY ulanishni qaytaradi (har safar yangi ochilmaydi).

        Eski kod `conn = self._conn()` ... `finally: conn.close()` naqshini
        ishlatadi — `_PersistentConn.close()` no-op bo'lgani uchun bu xavfsiz.
        Barcha chaqiriqlar `with self._lock:` ichida bo'lishi shart (thread-safe).
        """
        return self._persistent

    def set_bot(self, bot: Bot, username: str = ""):
        self._bot = bot
        if username:
            self._bot_username = username.lstrip("@")

    @property
    def bot_username(self) -> str:
        return self._bot_username

    def set_low_stock_alert(self, fn: Callable):
        """Past qoldiq bildirgichi — async funksiya: fn(product_dict)"""
        self._low_stock_alert = fn

    # ── Eski /channel_post sinxron metodi (endi kerak emas) ──────────────────
    async def sync(self, message):
        """Yangi tizimda ma'lumot SQLite da, kanalda yashirin kod yo'q."""
        return

    # ── Kanalga post yuboruvchi yordamchilar ─────────────────────────────────
    # MUHIM: Kanalga FAQAT mahsulot rasmlari (narxi bilan) yuboriladi.
    # Sotuv, buyurtma, mijoz, to'lov, admin postlari kanalga YUBORILMAYDI.
    # Buning uchun matn yuboruvchi `_send` har doim 0 qaytaradi (no-op),
    # rasm yuboruvchi `_send_photo` esa odatdagidek ishlaydi.

    async def _send(self, text: str) -> int:
        # Matn-only postlar kanalga yuborilmaydi (faqat rasmli mahsulotlar)
        return 0

    def _buy_markup(self, product_id: int) -> Optional[InlineKeyboardMarkup]:
        """Kanal postining ostida 'Sotib olish' tugmasi (bot deep-link).
        'Mijoz buyurtmalari' funksiyasi o'chirilgan bo'lsa — tugma qo'yilmaydi."""
        if not self._bot_username:
            return None
        if not self.is_client_orders_enabled():
            return None
        url = f"https://t.me/{self._bot_username}?start=buy_{product_id}"
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛒 Sotib olish", url=url)]
        ])

    async def _send_photo(self, file_id: str, caption: str,
                          product_id: int = 0) -> int:
        # 'Kanalga e'lon' funksiyasi o'chirilgan bo'lsa — kanalga post yuborilmaydi.
        if not self.is_channel_enabled():
            return 0
        if not self._bot or not CHANNEL_ID:
            return 0
        try:
            markup = self._buy_markup(product_id) if product_id else None
            msg = await self._bot.send_photo(
                CHANNEL_ID, file_id, caption=caption,
                parse_mode="HTML", reply_markup=markup
            )
            return msg.message_id
        except Exception as e:
            print(f"[Kanal foto xato] {e}")
            return 0

    async def _edit(self, mid: int, text: str):
        if not mid or not self._bot:
            return
        try:
            await self._bot.edit_message_text(
                text, chat_id=CHANNEL_ID, message_id=mid,
                parse_mode="HTML", disable_web_page_preview=True
            )
        except Exception:
            pass

    async def _edit_cap(self, mid: int, caption: str,
                        product_id: int = 0):
        if not mid or not self._bot:
            return
        try:
            markup = self._buy_markup(product_id) if product_id else None
            await self._bot.edit_message_caption(
                chat_id=CHANNEL_ID, message_id=mid,
                caption=caption, parse_mode="HTML",
                reply_markup=markup
            )
        except Exception:
            pass

    async def _delete_msg(self, mid: int):
        if not mid or not self._bot:
            return
        try:
            await self._bot.delete_message(CHANNEL_ID, mid)
        except Exception:
            pass

    # ── Ichki: row -> dict ───────────────────────────────────────────────────
    @staticmethod
    def _row_to_product(r) -> dict:
        return dict(r)

    @staticmethod
    def _row_to_client(r) -> dict:
        return dict(r)

    @staticmethod
    def _row_to_order(r) -> dict:
        d = dict(r)
        d["items"] = json.loads(d["items"]) if d.get("items") else []
        return d

    @staticmethod
    def _row_to_sale(r) -> dict:
        d = dict(r)
        d["items"] = json.loads(d["items"]) if d.get("items") else []
        # Eski APIga moslash: change_amount -> change
        d["change"] = d.pop("change_amount", 0)
        return d

    @staticmethod
    def _row_to_payment(r) -> dict:
        return dict(r)

    @staticmethod
    def _row_to_admin(r) -> dict:
        return dict(r)

    # ── Bulk read ────────────────────────────────────────────────────────────
    def _all_products(self, active_only=False) -> List[dict]:
        with self._lock:
            conn = self._conn()
            try:
                if active_only:
                    rows = conn.execute(
                        "SELECT * FROM products WHERE is_active=1 ORDER BY name"
                    ).fetchall()
                else:
                    rows = conn.execute("SELECT * FROM products ORDER BY id").fetchall()
                return [self._row_to_product(r) for r in rows]
            finally:
                conn.close()

    def _all_clients(self) -> List[dict]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute("SELECT * FROM clients ORDER BY shop_name").fetchall()
                return [self._row_to_client(r) for r in rows]
            finally:
                conn.close()

    def _all_orders(self) -> List[dict]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute("SELECT * FROM orders ORDER BY id").fetchall()
                return [self._row_to_order(r) for r in rows]
            finally:
                conn.close()

    def _all_sales(self) -> List[dict]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute("SELECT * FROM sales ORDER BY id").fetchall()
                return [self._row_to_sale(r) for r in rows]
            finally:
                conn.close()

    def _all_payments(self) -> List[dict]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute("SELECT * FROM payments ORDER BY id").fetchall()
                return [self._row_to_payment(r) for r in rows]
            finally:
                conn.close()

    def _all_admins(self) -> List[dict]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute("SELECT * FROM admins ORDER BY created_at").fetchall()
                return [self._row_to_admin(r) for r in rows]
            finally:
                conn.close()

    # ── Settings (USD kursi va boshqalar) ────────────────────────────────────
    def get_setting(self, key: str, default: str = "") -> str:
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT value FROM settings WHERE key=?", (key,)
                ).fetchone()
                return r["value"] if r else default
            finally:
                conn.close()

    def set_setting(self, key: str, value: str):
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO settings(key, value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, str(value))
                )
                conn.commit()
            finally:
                conn.close()

    def get_usd_rate(self) -> float:
        try:
            return float(self.get_setting("usd_rate", str(USD_RATE_DEFAULT)))
        except Exception:
            return USD_RATE_DEFAULT

    def set_usd_rate(self, rate: float):
        if rate <= 0:
            return
        self.set_setting("usd_rate", f"{rate:.4f}")

    # ── Optom/dona narx funksiyasi (yoqish/o'chirish) ────────────────────────
    def is_wholesale_enabled(self) -> bool:
        """Optom narx funksiyasi yoqilganmi? Standart — yoqilgan."""
        val = (self.get_setting("wholesale_enabled", "1") or "").strip().lower()
        return val not in ("0", "false", "no", "off", "")

    def set_wholesale_enabled(self, enabled: bool):
        """Optom narx funksiyasini yoqish/o'chirish."""
        self.set_setting("wholesale_enabled", "1" if enabled else "0")

    def is_dona_enabled(self) -> bool:
        """Dona narx funksiyasi yoqilganmi? Standart — yoqilgan."""
        val = (self.get_setting("dona_enabled", "1") or "").strip().lower()
        return val not in ("0", "false", "no", "off", "")

    def set_dona_enabled(self, enabled: bool):
        """Dona narx funksiyasini yoqish/o'chirish."""
        self.set_setting("dona_enabled", "1" if enabled else "0")

    # ── Universal funksiya kalitlari ─────────────────────────────────────────
    # Har bir do'kon o'ziga keraksiz funksiyalarni o'chirib qo'yishi mumkin.
    # Hammasi STANDART — YOQILGAN. Bosh admin "⚙️ Sozlamalar → 🧩 Funksiyalar"
    # bo'limidan boshqaradi.

    @staticmethod
    def _flag_on(val: str) -> bool:
        return (val or "").strip().lower() not in ("0", "false", "no", "off", "")

    def is_barcode_enabled(self) -> bool:
        """Shtrix-kod skaneri yoqilganmi? O'chirilsa — mahsulot qo'shishda
        shtrix-kod bosqichi so'ralmaydi, sotuv qidiruvi faqat ID/nom bilan."""
        return self._flag_on(self.get_setting("barcode_enabled", "1"))

    def set_barcode_enabled(self, enabled: bool):
        self.set_setting("barcode_enabled", "1" if enabled else "0")

    def is_channel_enabled(self) -> bool:
        """Kanalga e'lon qilish yoqilganmi? O'chirilsa — mahsulotlar
        Telegram kanalga rasm + 'Sotib olish' tugmasi bilan yuborilmaydi."""
        return self._flag_on(self.get_setting("channel_enabled", "1"))

    def set_channel_enabled(self, enabled: bool):
        self.set_setting("channel_enabled", "1" if enabled else "0")

    def is_client_orders_enabled(self) -> bool:
        """Mijoz buyurtmalari yoqilganmi? O'chirilsa — mijozlar bot orqali
        zakaz bera olmaydi, kanaldagi 'Sotib olish' havolasi ham yo'qoladi."""
        return self._flag_on(self.get_setting("client_orders_enabled", "1"))

    def set_client_orders_enabled(self, enabled: bool):
        self.set_setting("client_orders_enabled", "1" if enabled else "0")

    def is_nasiya_enabled(self) -> bool:
        """Nasiya (qarzga sotuv) yoqilganmi? O'chirilsa — kassada 'Nasiya'
        to'lov turi ko'rinmaydi."""
        return self._flag_on(self.get_setting("nasiya_enabled", "1"))

    def set_nasiya_enabled(self, enabled: bool):
        self.set_setting("nasiya_enabled", "1" if enabled else "0")

    # ── Kategoriya / Yetkazib beruvchi va tezlashtiruvchi funksiyalar ────────
    def is_categories_enabled(self) -> bool:
        """Kategoriyalar bo'limi yoqilganmi? O'chirilsa — '🗂 Kategoriyalar'
        menyusi, mahsulotdagi kategoriya maydoni va kategoriya filtri yo'qoladi."""
        return self._flag_on(self.get_setting("categories_enabled", "1"))

    def set_categories_enabled(self, enabled: bool):
        self.set_setting("categories_enabled", "1" if enabled else "0")

    def is_suppliers_enabled(self) -> bool:
        """Yetkazib beruvchilar bo'limi yoqilganmi? O'chirilsa —
        '🚚 Yetkazib beruvchilar' menyusi, mahsulotdagi yetkazib beruvchi
        maydoni va tezkor prixod yo'qoladi."""
        return self._flag_on(self.get_setting("suppliers_enabled", "1"))

    def set_suppliers_enabled(self, enabled: bool):
        self.set_setting("suppliers_enabled", "1" if enabled else "0")

    def is_cat_filter_enabled(self) -> bool:
        """Kategoriya bo'yicha filtr yoqilganmi? (Kategoriyalar yoqilgan bo'lsa
        ham alohida o'chirib qo'yish mumkin.)"""
        return self._flag_on(self.get_setting("cat_filter_enabled", "1"))

    def set_cat_filter_enabled(self, enabled: bool):
        self.set_setting("cat_filter_enabled", "1" if enabled else "0")

    def is_cart_edit_enabled(self) -> bool:
        """Kassada savatni tahrirlash (miqdor o'zgartirish / o'chirish) yoqilganmi?"""
        return self._flag_on(self.get_setting("cart_edit_enabled", "1"))

    def set_cart_edit_enabled(self, enabled: bool):
        self.set_setting("cart_edit_enabled", "1" if enabled else "0")

    def is_quick_restock_enabled(self) -> bool:
        """Past qoldiq ro'yxatidan tezkor to'ldirish tugmalari yoqilganmi?"""
        return self._flag_on(self.get_setting("quick_restock_enabled", "1"))

    def set_quick_restock_enabled(self, enabled: bool):
        self.set_setting("quick_restock_enabled", "1" if enabled else "0")

    def is_quick_prixod_enabled(self) -> bool:
        """Yetkazib beruvchi bo'limidan tezkor prixod yoqilganmi?"""
        return self._flag_on(self.get_setting("quick_prixod_enabled", "1"))

    def set_quick_prixod_enabled(self, enabled: bool):
        self.set_setting("quick_prixod_enabled", "1" if enabled else "0")

    def is_mini_app_enabled(self) -> bool:
        """🌐 Mini App (WebApp) yoqilganmi? Yoqilgan bo'lsa — foydalanuvchilar
        login/parol yaratib, WebApp orqali kira oladi."""
        return self._flag_on(self.get_setting("mini_app_enabled", "1"))

    def set_mini_app_enabled(self, enabled: bool):
        self.set_setting("mini_app_enabled", "1" if enabled else "0")

    def is_ai_consult_enabled(self) -> bool:
        """💬 AI sotuvchi-konsultant rejimi yoqilganmi?
        O'chirilsa — mijoz menyusida tugma ko'rinmaydi.
        Bu funksiya GEMINI_API_KEY talab qiladi."""
        return self._flag_on(self.get_setting("ai_consult_enabled", "1"))

    def set_ai_consult_enabled(self, enabled: bool):
        self.set_setting("ai_consult_enabled", "1" if enabled else "0")

    def is_client_search_enabled(self) -> bool:
        """🔎 Mijoz uchun mahsulot qidirish yoqilganmi? O'chirilsa —
        mijoz menyusida tugma ko'rinmaydi."""
        return self._flag_on(self.get_setting("client_search_enabled", "1"))

    def set_client_search_enabled(self, enabled: bool):
        self.set_setting("client_search_enabled", "1" if enabled else "0")

    def is_ai_analytics_enabled(self) -> bool:
        """🤖 AI Analitika tugmasi (Google Gemini) yoqilganmi?
        STANDART — YOQILGAN; lekin GEMINI_API_KEY bo'lmasa baribir
        handler 'sozlanmagan' deb javob beradi."""
        return self._flag_on(self.get_setting("ai_analytics_enabled", "1"))

    def set_ai_analytics_enabled(self, enabled: bool):
        self.set_setting("ai_analytics_enabled", "1" if enabled else "0")

