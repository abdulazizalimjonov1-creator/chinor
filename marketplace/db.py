"""Marketplace bazasi — katalog keshi va kunlik qoldiq tarixi.

Poydevor g'oyasi: gateway faqat HOZIRGI qoldiqni beradi, tarixni bermaydi.
Muzlagan va tugab-keltirilmayotgan tovarni topish uchun tarix kerak, shuning
uchun har kuni qoldiqni shu yerga yozib boramiz. Qancha erta boshlansa,
shuncha erta natija ko'rinadi.
"""

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional

from marketplace import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY,
    parent      INTEGER DEFAULT 0,
    name        TEXT    NOT NULL DEFAULT '',
    image       TEXT    DEFAULT '',
    description TEXT    DEFAULT '',
    updated_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id           INTEGER PRIMARY KEY,
    category_id  INTEGER,
    name         TEXT    NOT NULL DEFAULT '',
    sku          TEXT    DEFAULT '',
    image        TEXT    DEFAULT '',
    description  TEXT    DEFAULT '',
    classcode    TEXT    DEFAULT '',
    package_code INTEGER,
    first_seen   TEXT    NOT NULL,
    last_seen    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_id);
CREATE INDEX IF NOT EXISTS idx_products_sku      ON products(sku);

-- Kunlik qoldiq surati. Bir kunda bir necha marta yozilsa — oxirgisi qoladi.
CREATE TABLE IF NOT EXISTS stock_daily (
    product_id  INTEGER NOT NULL,
    branch      TEXT    NOT NULL,
    day         TEXT    NOT NULL,          -- YYYY-MM-DD (mahalliy vaqt)
    price       REAL    NOT NULL DEFAULT 0,
    stock       REAL    NOT NULL DEFAULT 0,
    low_stock   REAL    NOT NULL DEFAULT 0,
    captured_at TEXT    NOT NULL,
    PRIMARY KEY (product_id, branch, day)
);
CREATE INDEX IF NOT EXISTS idx_stock_day     ON stock_daily(day);
CREATE INDEX IF NOT EXISTS idx_stock_product ON stock_daily(product_id, branch, day);

-- Shtrix-kodlar. Bu YAGONA nusxada yashaydigan ma'lumot: YesPOS marketplace
-- API uni bermaydi (u yerda faqat `classcode` = IKPU soliq kodi va ketma-ket
-- `sku` bor). Ya'ni bazani YesPOS'dan qayta qursak, shtrix-kod yo'qolardi.
-- Shuning uchun u shu yerda sku bo'yicha saqlanadi va pos.db ga qaytariladi.
CREATE TABLE IF NOT EXISTS product_barcodes (
    sku        TEXT PRIMARY KEY,
    barcode    TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Har sinxron urinishining natijasi (nima qachon ishladi/yiqildi).
CREATE TABLE IF NOT EXISTS sync_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT    NOT NULL,          -- 'catalog' | 'stock'
    started_at  TEXT    NOT NULL,
    finished_at TEXT,
    ok          INTEGER NOT NULL DEFAULT 0,
    items       INTEGER NOT NULL DEFAULT 0,
    error       TEXT
);
"""


_LOCAL_TZ = timezone(timedelta(hours=config.TZ_OFFSET_HOURS))


def now_local() -> datetime:
    """Mahalliy (Toshkent) vaqt — snapshot sanasi shunga qarab belgilanadi."""
    return datetime.now(_LOCAL_TZ)


def today_local() -> str:
    return now_local().strftime("%Y-%m-%d")


class MarketplaceDB:
    """SQLite ustidan yupqa qatlam. Kontekst-menejer sifatida ishlatiladi."""

    def __init__(self, path: str = config.DB_PATH):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def __enter__(self) -> "MarketplaceDB":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    # ── Katalog ───────────────────────────────────────────────────────────

    def upsert_categories(self, categories: Iterable[dict]) -> int:
        """Kategoriyalarni yozadi/yangilaydi. Qaytaradi: nechtasi tegdi."""
        ts = now_local().isoformat(timespec="seconds")
        rows = [
            (
                int(c["id"]),
                int(c.get("parent") or 0),
                c.get("name") or "",
                c.get("image") or "",
                c.get("description") or "",
                ts,
            )
            for c in categories
        ]
        self.conn.executemany(
            """INSERT INTO categories (id, parent, name, image, description, updated_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                   parent=excluded.parent, name=excluded.name, image=excluded.image,
                   description=excluded.description, updated_at=excluded.updated_at""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def upsert_products(self, products: Iterable[dict]) -> int:
        """Tovarlarni yozadi/yangilaydi.

        `first_seen` faqat birinchi marta yoziladi — yangi tovarni ajratish
        uchun kerak (masalan "rasmi yo'q yangi tovarlar" ro'yxati).
        """
        ts = now_local().isoformat(timespec="seconds")
        rows = [
            (
                int(p["id"]),
                int(p["category"]) if p.get("category") is not None else None,
                p.get("name") or "",
                str(p.get("sku") or ""),
                p.get("image") or "",
                p.get("description") or "",
                str(p.get("classcode") or ""),
                p.get("package_code"),
                ts,
                ts,
            )
            for p in products
        ]
        self.conn.executemany(
            """INSERT INTO products
                   (id, category_id, name, sku, image, description,
                    classcode, package_code, first_seen, last_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                   category_id=excluded.category_id, name=excluded.name,
                   sku=excluded.sku, image=excluded.image,
                   description=excluded.description, classcode=excluded.classcode,
                   package_code=excluded.package_code, last_seen=excluded.last_seen""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    # ── Qoldiq tarixi ─────────────────────────────────────────────────────

    def save_stock_snapshot(
        self, rows: Iterable[dict], branch: Optional[str] = None, day: Optional[str] = None
    ) -> int:
        """Bir kunlik qoldiq suratini yozadi.

        `rows` — gateway `products/info` javobidagi yozuvlar
        ({"product_id", "price", "stock", "low_stock"}).
        Shu kunda takror ishga tushirilsa — qiymat yangilanadi (oxirgisi qoladi).
        """
        branch = str(branch if branch is not None else config.BRANCH)
        day = day or today_local()
        ts = now_local().isoformat(timespec="seconds")
        payload = [
            (
                int(r["product_id"]),
                branch,
                day,
                float(r.get("price") or 0),
                float(r.get("stock") or 0),
                float(r.get("low_stock") or 0),
                ts,
            )
            for r in rows
        ]
        self.conn.executemany(
            """INSERT INTO stock_daily
                   (product_id, branch, day, price, stock, low_stock, captured_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(product_id, branch, day) DO UPDATE SET
                   price=excluded.price, stock=excluded.stock,
                   low_stock=excluded.low_stock, captured_at=excluded.captured_at""",
            payload,
        )
        self.conn.commit()
        return len(payload)

    def snapshot_days(self, branch: Optional[str] = None) -> int:
        """Nechta kunlik surat yig'ilgan (analitika tayyorligini bildiradi)."""
        branch = str(branch if branch is not None else config.BRANCH)
        cur = self.conn.execute(
            "SELECT COUNT(DISTINCT day) AS n FROM stock_daily WHERE branch = ?", (branch,)
        )
        return int(cur.fetchone()["n"] or 0)

    # ── Shtrix-kodlar ─────────────────────────────────────────────────────

    def save_barcodes(self, mapping: dict) -> int:
        """pos.db dan yig'ilgan shtrix-kodlarni saqlaydi ({sku: barcode}).

        Mavjud yozuv ustidan yoziladi — pos.db dagi qiymat asosiy hisoblanadi,
        chunki shtrix-kod o'sha yerda skaner orqali kiritiladi.
        """
        ts = now_local().isoformat(timespec="seconds")
        rows = [(str(k), str(v).strip(), ts) for k, v in mapping.items() if str(v).strip()]
        self.conn.executemany(
            """INSERT INTO product_barcodes (sku, barcode, updated_at) VALUES (?,?,?)
               ON CONFLICT(sku) DO UPDATE SET
                   barcode=excluded.barcode, updated_at=excluded.updated_at""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def get_barcodes(self) -> dict:
        """{sku: barcode} — saqlangan barcha shtrix-kodlar."""
        return {
            r["sku"]: r["barcode"]
            for r in self.conn.execute("SELECT sku, barcode FROM product_barcodes")
        }

    # ── Sinxron jurnali ───────────────────────────────────────────────────

    def start_run(self, kind: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO sync_runs (kind, started_at) VALUES (?,?)",
            (kind, now_local().isoformat(timespec="seconds")),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, ok: bool, items: int = 0, error: str = "") -> None:
        self.conn.execute(
            "UPDATE sync_runs SET finished_at=?, ok=?, items=?, error=? WHERE id=?",
            (
                now_local().isoformat(timespec="seconds"),
                1 if ok else 0,
                items,
                error[:500],
                run_id,
            ),
        )
        self.conn.commit()

    # ── Yordamchi so'rovlar ───────────────────────────────────────────────

    def products_without_image(self) -> List[sqlite3.Row]:
        """Rasmi yo'q tovarlar — na API'da, na o'z papkamizda.

        Oldingi kelishuv: rasmni id bo'yicha o'zimizda saqlaymiz
        (IMAGES_DIR/<id>.jpg), API rasm bermasa ham do'kon buzilmaydi.
        """
        rows = self.conn.execute(
            "SELECT id, name, sku, image FROM products ORDER BY name"
        ).fetchall()
        out = []
        for r in rows:
            if r["image"]:
                continue
            has_local = any(
                os.path.exists(os.path.join(config.IMAGES_DIR, f"{r['id']}{ext}"))
                for ext in (".jpg", ".jpeg", ".png", ".webp")
            )
            if not has_local:
                out.append(r)
        return out
