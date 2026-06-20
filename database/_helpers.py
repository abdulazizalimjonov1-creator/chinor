"""POS bazasi — umumiy yordamchilar: vaqt, pul formatlash, DB yo'li, SXEMA.

Bu modul HECH NARSAGA bog'liq emas (faqat stdlib + bot.config) — shuning uchun
uni boshqa barcha database/* modullari bemalol import qila oladi.
"""

import os
from datetime import datetime, timedelta, timezone

from bot.config import TZ_OFFSET_HOURS

# ─── DB joylashuvi ───────────────────────────────────────────────────────────
DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "pos.db"
)

# Toshkent vaqt mintaqasi
TASHKENT_TZ = timezone(timedelta(hours=TZ_OFFSET_HOURS))


def now_local() -> datetime:
    """Toshkent vaqti — naive datetime (DB ga yoziladigan formatga mos)."""
    return datetime.now(TASHKENT_TZ).replace(tzinfo=None)


def _now() -> str:
    """DB ga yoziladigan Toshkent vaqti (UTC+5)."""
    return now_local().strftime("%Y-%m-%d %H:%M:%S")


# ─── USD/so'm yordamchilari ──────────────────────────────────────────────────

def fmt_usd(usd: float) -> str:
    """1.25 → '$1.25', 0 → '$0.00'."""
    try:
        return f"${float(usd):,.2f}"
    except Exception:
        return "$0.00"


def fmt_sum(s: float) -> str:
    """150000 → '150,000 so'm'."""
    try:
        return f"{float(s):,.0f} so'm"
    except Exception:
        return "0 so'm"


def fmt_money(usd: float, summ: float) -> str:
    """USD va so'mni birga ko'rsatish: '$1.25 (≈ 15,625 so'm)'."""
    return f"{fmt_usd(usd)}  (≈ {fmt_sum(summ)})"


def usd_to_sum(usd: float, rate: float) -> float:
    """USD ni so'mga aylantiradi."""
    try:
        return float(usd) * float(rate)
    except Exception:
        return 0.0


def sum_to_usd(summ: float, rate: float) -> float:
    """So'mni USD ga aylantiradi."""
    try:
        r = float(rate)
        return (float(summ) / r) if r > 0 else 0.0
    except Exception:
        return 0.0


# ─── Sxema ───────────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT    NOT NULL,
    description         TEXT    DEFAULT '',
    sell_price          REAL    NOT NULL,
    wholesale_price     REAL    DEFAULT 0,
    cost_price          REAL    NOT NULL,
    sell_price_usd      REAL    DEFAULT 0,
    wholesale_price_usd REAL    DEFAULT 0,
    cost_price_usd      REAL    DEFAULT 0,
    qty                 REAL    NOT NULL DEFAULT 0,
    unit                TEXT    DEFAULT 'dona',
    image_file_id       TEXT    DEFAULT '',
    barcode             TEXT    DEFAULT '',
    is_active           INTEGER DEFAULT 1,
    created_at          TEXT    NOT NULL,
    channel_msg_id      INTEGER DEFAULT 0
);
-- DIQQAT: idx_products_barcode indeksi bu yerda EMAS, _migrate() ichida
-- yaratiladi — chunki eski DB'larda 'barcode' ustuni hali bo'lmasligi mumkin
-- (avval ustun qo'shilishi, keyin indeks qurilishi kerak).

CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS suppliers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    phone       TEXT    DEFAULT '',
    note        TEXT    DEFAULT '',
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS clients (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id     INTEGER UNIQUE,
    shop_name       TEXT    NOT NULL,
    phone           TEXT    DEFAULT '',
    debt            REAL    DEFAULT 0,
    debt_usd        REAL    DEFAULT 0,
    client_type     TEXT    DEFAULT 'dona',
    registered_by   INTEGER,
    created_at      TEXT    NOT NULL,
    channel_msg_id  INTEGER DEFAULT 0,
    is_internal     INTEGER DEFAULT 0,   -- 1 = «Chinor» (do'konning o'zi): sotuv = ichki rasxod
    allow_credit    INTEGER DEFAULT 0    -- 1 = bu mijozga qarzga (nasiya) savdo qilsa bo'ladi
);

CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL,
    client_tg_id    INTEGER,
    shop_name       TEXT,
    phone           TEXT,
    items           TEXT    NOT NULL,
    total           REAL    NOT NULL,
    note            TEXT    DEFAULT '',
    status          TEXT    DEFAULT 'accepted',
    created_at      TEXT    NOT NULL,
    channel_msg_id  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sales (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cashier_id      INTEGER,
    cashier_name    TEXT,
    items           TEXT    NOT NULL,
    total           REAL    NOT NULL,
    total_usd       REAL    DEFAULT 0,
    subtotal        REAL    DEFAULT 0,
    subtotal_usd    REAL    DEFAULT 0,
    discount        REAL    DEFAULT 0,
    discount_usd    REAL    DEFAULT 0,
    usd_rate        REAL    DEFAULT 0,
    paid_cash       REAL    DEFAULT 0,
    paid_cash_usd   REAL    DEFAULT 0,
    paid_card       REAL    DEFAULT 0,
    paid_card_usd   REAL    DEFAULT 0,
    paid_other      REAL    DEFAULT 0,
    paid_other_usd  REAL    DEFAULT 0,
    paid_total      REAL    DEFAULT 0,
    paid_total_usd  REAL    DEFAULT 0,
    paid_currency   TEXT    DEFAULT 'sum',
    change_amount   REAL    DEFAULT 0,
    change_usd      REAL    DEFAULT 0,
    is_nasiya       INTEGER DEFAULT 0,
    client_id       INTEGER DEFAULT 0,
    client_name     TEXT    DEFAULT '',
    created_at      TEXT    NOT NULL,
    channel_msg_id  INTEGER DEFAULT 0,
    source          TEXT    DEFAULT '',
    receipt_no      TEXT    DEFAULT '',
    is_internal     INTEGER DEFAULT 0,   -- 1 = «Chinor» ichki rasxod (foyda/daromadga kirmaydi)
    is_return       INTEGER DEFAULT 0    -- 1 = qaytarish (refund): summa/miqdor manfiy, qoldiq tiklanadi
);

CREATE TABLE IF NOT EXISTS payments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL,
    client_tg_id    INTEGER,
    shop_name       TEXT,
    amount          REAL    NOT NULL,
    amount_usd      REAL    DEFAULT 0,
    currency        TEXT    DEFAULT 'sum',
    usd_rate        REAL    DEFAULT 0,
    note            TEXT    DEFAULT '',
    created_at      TEXT    NOT NULL,
    channel_msg_id  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS admins (
    telegram_id     INTEGER PRIMARY KEY,
    full_name       TEXT,
    added_by        INTEGER,
    created_at      TEXT    NOT NULL,
    channel_msg_id  INTEGER DEFAULT 0,
    role            TEXT    DEFAULT 'full',
    permissions     TEXT    DEFAULT '',
    currency_mode   TEXT    DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_prod_active   ON products(is_active);
CREATE INDEX IF NOT EXISTS idx_prod_name     ON products(name);
CREATE INDEX IF NOT EXISTS idx_clients_tg    ON clients(telegram_id);
CREATE INDEX IF NOT EXISTS idx_orders_cli    ON orders(client_id);
CREATE INDEX IF NOT EXISTS idx_orders_dt     ON orders(created_at);
CREATE INDEX IF NOT EXISTS idx_sales_dt      ON sales(created_at);
CREATE INDEX IF NOT EXISTS idx_pay_cli       ON payments(client_id);

CREATE TABLE IF NOT EXISTS search_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER,
    query        TEXT    NOT NULL,
    query_norm   TEXT    NOT NULL,   -- lowercased + trimmed (group by uchun)
    found_count  INTEGER DEFAULT 0,
    source       TEXT    DEFAULT '', -- 'sale' | 'admin_products' | ...
    created_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_search_dt    ON search_log(created_at);
CREATE INDEX IF NOT EXISTS idx_search_qnorm ON search_log(query_norm);
CREATE INDEX IF NOT EXISTS idx_search_found ON search_log(found_count);
-- DIQQAT: idx_products_category / idx_products_supplier indekslari bu yerda
-- EMAS, _migrate() ichida yaratiladi — eski DB'larda 'category_id' /
-- 'supplier_id' ustunlari hali bo'lmasligi mumkin.
"""
