"""Kassa kompyuteridan kelgan YesPOS eksportini import qilish (shtrix-kod, mijoz).

YesPOS marketplace API'si tovar shtrix-kodlarini va mijozlarni bermaydi.
Ular kassa kompyuterida (QPOS) bor: u yerdagi `C:\\chinor-sync\\sync.ps1`
har 30 daqiqada ikkita CSV tayyorlaydi va Tailscale orqali shu serverga
(`DATA_DIR/yespos_import/`) yuboradi. Kassaning kaliti serverda faqat fayl
uzatishga ruxsat oladi (`command="internal-sftp"`), buyruq bajara olmaydi.

Bu modul papkani kuzatadi va fayl yangilansa:
  * tovarlar CSV (`sku` + `barcodes`/`barcode` ustuni) → `product_barcodes`
    (Chinor `products.id` == YesPOS `sku`). Mavjud kodlar o'chirilmaydi va
    boshqa tovarga ko'chirilmaydi — faqat yangilari qo'shiladi. Tovarning
    asosiy `barcode` maydoni bo'sh bo'lsa, birinchi kod shu yerga yoziladi.
  * mijozlar CSV (`name`, `phone`) → `clients`. Telefon bo'yicha
    dublikat tekshiriladi (`add_client`), bor mijozga tegilmaydi.

Ustunlar nomi bo'yicha o'qiladi — tartibi muhim emas, ortiqcha ustunlar
e'tiborsiz qoldiriladi.
"""

import asyncio
import csv
import json
import logging
import os
import re
import sqlite3
import time

logger = logging.getLogger(__name__)

INTERVAL = int(os.getenv("YESPOS_IMPORT_MINUTES", "10")) * 60
_ITEM_FILES = ("export_items.csv", "items.csv")
_CLIENT_FILES = ("export_clients.csv", "clients.csv")


def _dir() -> str:
    from database._helpers import DB_PATH
    return os.path.join(os.path.dirname(DB_PATH), "yespos_import")


def _state_path() -> str:
    return os.path.join(_dir(), ".state.json")


def _load_state() -> dict:
    try:
        with open(_state_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(st: dict) -> None:
    try:
        with open(_state_path(), "w", encoding="utf-8") as f:
            json.dump(st, f)
    except OSError:
        pass


def _find(names) -> str:
    for n in names:
        p = os.path.join(_dir(), n)
        if os.path.isfile(p):
            return p
    return ""


def _rows(path: str) -> list:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
                for r in csv.DictReader(f)]


_CODE_RE = re.compile(r"^[0-9A-Za-z\-]{4,32}$")


def _codes(raw: str) -> list:
    out = []
    for c in re.split(r"[|,;\s]+", raw or ""):
        c = c.strip()
        if _CODE_RE.match(c) and c not in out:
            out.append(c)
    return out


def import_items(path: str) -> dict:
    """Shtrix-kodlarni qo'shadi. {'rows','codes_added','primary_set','no_product'}."""
    from database._helpers import DB_PATH
    rows = _rows(path)
    res = {"rows": len(rows), "codes_added": 0, "primary_set": 0, "no_product": 0,
           "no_barcode_column": False}
    if rows and not any(k in rows[0] for k in ("barcodes", "barcode")):
        res["no_barcode_column"] = True
        return res
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    res["reassigned"] = 0
    # Kod YesPOS'da qaysi tovarda turgani — haqiqat manbai. Bitta kod YesPOS'ning
    # o'zida ham ikki tovarda bo'lsa (xato kiritilgan), uni hech qayerga ko'chirmaymiz.
    owners: dict = {}
    for r in rows:
        try:
            sku = int(float(r.get("sku") or 0))
        except ValueError:
            continue
        for c in _codes(r.get("barcodes") or r.get("barcode") or ""):
            owners.setdefault(c, set()).add(sku)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        have = {r[0] for r in conn.execute("SELECT id FROM products")}
        mapped = dict(conn.execute("SELECT code, product_id FROM product_barcodes"))
        for code, skus in owners.items():
            if len(skus) != 1:
                continue
            new = next(iter(skus))
            old = mapped.get(code)
            if old is None or old == new or new not in have:
                continue
            # Eski ma'lumotda kod boshqa tovarga yozilib qolgan — YesPOS'dagidek qilamiz
            conn.execute("UPDATE product_barcodes SET product_id=? WHERE code=?", (new, code))
            conn.execute("UPDATE products SET barcode='' WHERE id=? AND barcode=?", (old, code))
            res["reassigned"] += 1
            logger.info("📥 Shtrix-kod %s: #%s → #%s (YesPOS bo'yicha)", code, old, new)
        for r in rows:
            try:
                pid = int(float(r.get("sku") or 0))
            except ValueError:
                continue
            codes = _codes(r.get("barcodes") or r.get("barcode") or "")
            if not pid or not codes:
                continue
            if pid not in have:
                res["no_product"] += 1
                continue
            for c in codes:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO product_barcodes(code, product_id, created_at) "
                    "VALUES(?,?,?)", (c, pid, now))
                res["codes_added"] += cur.rowcount
            # Asosiy kod — faqat haqiqatan SHU tovarga bog'langan kodlardan
            # (boshqa tovarda qolgan noaniq kod ikki joyda chiqmasin)
            own = [c for c in codes if conn.execute(
                "SELECT 1 FROM product_barcodes WHERE code=? AND product_id=?",
                (c, pid)).fetchone()]
            if own:
                cur = conn.execute(
                    "UPDATE products SET barcode=? WHERE id=? AND (barcode IS NULL OR barcode='')",
                    (own[0], pid))
                res["primary_set"] += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return res


async def import_clients(db, path: str) -> dict:
    rows = _rows(path)
    res = {"rows": len(rows), "added": 0}
    for r in rows:
        name = (r.get("name") or "").strip()
        phone = (r.get("phone") or "").strip()
        if not name or len("".join(ch for ch in phone if ch.isdigit())) < 7:
            continue
        try:
            if await db.add_client(None, name[:120], phone, registered_by=0):
                res["added"] += 1
        except Exception as e:
            logger.warning("👥 Mijoz qo'shilmadi: %s", e)
    return res


async def run_once(db) -> str:
    os.makedirs(_dir(), exist_ok=True)
    st = _load_state()
    out = []
    p = _find(_ITEM_FILES)
    if p and os.path.getmtime(p) > st.get("items", 0):
        r = await asyncio.get_running_loop().run_in_executor(None, import_items, p)
        st["items"] = os.path.getmtime(p)
        out.append("tovar: " + ("shtrix-kod ustuni YO'Q" if r["no_barcode_column"] else
                   f"{r['rows']} qator, +{r['codes_added']} kod, "
                   f"{r.get('reassigned', 0)} ko'chirildi, "
                   f"{r['primary_set']} asosiy, {r['no_product']} tovar topilmadi"))
    p = _find(_CLIENT_FILES)
    if p and os.path.getmtime(p) > st.get("clients", 0):
        r = await import_clients(db, p)
        st["clients"] = os.path.getmtime(p)
        out.append(f"mijoz: {r['rows']} qator, +{r['added']} yangi")
    if out:
        _save_state(st)
        try:
            from bot import api_recommend
            api_recommend._cache["at"] = 0
        except Exception:
            pass
    return " | ".join(out)


async def loop():
    from database.channel_db import db
    await asyncio.sleep(90)
    while True:
        try:
            res = await run_once(db)
            if res:
                logger.info("📥 Kassa eksporti import qilindi: %s", res)
        except Exception:
            logger.exception("📥 Kassa eksporti importi")
        await asyncio.sleep(INTERVAL)
