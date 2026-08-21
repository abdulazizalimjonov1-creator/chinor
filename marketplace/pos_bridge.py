"""Ko'prik: YesPOS qoldiq/narxini Chinor POS bazasiga (`pos.db`) ko'chiradi.

Ulash kaliti — **Chinor `products.id` == YesPOS `sku`**. Bu tasodif emas:
Chinor bazasi o'sha do'kon artikullari asosida qurilgan (98.8% moslik,
93.6% da nom ham bir xil).

Yo'nalish bir tomonlama: YesPOS — haqiqat manbai, Chinor — ko'zgu.

Nima yoziladi:
  • `qty`         ← YesPOS `stock`
  • `sell_price`  ← YesPOS `price`
  • YesPOS'da bor, Chinor'da yo'q tovar — YANGI qator bo'lib qo'shiladi
    (`id` = o'sha sku). Tannarx/optom 0 bo'ladi: API ularni bermaydi.
    Narxi 0 bo'lgan tovar qo'shilmaydi — mijozga "0 so'm" ko'rinmasin.

Nima TEGILMAYDI: `cost_price` (tannarx) va `wholesale_price` (optom) —
marketplace API ularni bermaydi, ustidan yozsak ma'lumot yo'qoladi.

USD narx ishlatilmaydi. Narx endi so'mda kanonik. Ilgari kurs o'zgarganda
`reprice_all_with_rate()` so'm narxni USD'dan qayta hisoblab, sinxronlangan
qiymatni bekor qilardi — o'sha zanjir uzildi (`_products.py`, `_base.py`).

Ishlatish:
    python -m marketplace.pos_bridge            # faqat ko'rsatadi (dry-run)
    python -m marketplace.pos_bridge --apply    # haqiqatan yozadi
"""

import argparse
import os
import shutil
import sqlite3
import sys

from marketplace import config
from marketplace.db import MarketplaceDB, now_local

def _fetch_marketplace_rows(db: MarketplaceDB, day: str = None) -> dict:
    """sku (matn) → {"stock", "price", "name", "yespos_id"} — oxirgi surat."""
    day = day or db.conn.execute(
        "SELECT MAX(day) d FROM stock_daily WHERE branch = ?", (str(config.BRANCH),)
    ).fetchone()["d"]
    if not day:
        return {}, None

    rows = db.conn.execute(
        """SELECT p.sku, p.id AS yespos_id, p.name, s.stock, s.price
             FROM stock_daily s
             JOIN products p ON p.id = s.product_id
            WHERE s.branch = ? AND s.day = ?""",
        (str(config.BRANCH), day),
    ).fetchall()
    return {
        str(r["sku"]): {
            "stock": float(r["stock"]),
            "price": float(r["price"]),
            "name": r["name"],
            "yespos_id": r["yespos_id"],
        }
        for r in rows
        if str(r["sku"])
    }, day


def plan(pos_db_path: str, mp_rows: dict, known_barcodes: dict = None) -> dict:
    """Nima o'zgarishini hisoblaydi — hech narsa yozmaydi."""
    known_barcodes = known_barcodes or {}
    conn = sqlite3.connect(f"file:{pos_db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        local = conn.execute(
            "SELECT id, name, qty, sell_price, barcode FROM products"
        ).fetchall()
    finally:
        conn.close()

    # Shtrix-kod: pos.db dagilarni yig'amiz, saqlangan-u bu yerda yo'qlarini
    # qaytarish uchun ajratamiz. Ustidan hech qachon yozilmaydi.
    harvest = {
        str(r["id"]): (r["barcode"] or "").strip()
        for r in local
        if (r["barcode"] or "").strip()
    }
    barcode_restore = [
        (int(sku), known_barcodes[sku])
        for sku in known_barcodes
        if sku not in harvest and sku.isdigit()
    ]

    qty_changes, price_changes, zero_price, negative = [], [], [], []
    name_changes = []
    matched = 0
    local_ids = set()

    for r in local:
        sku = str(r["id"])
        local_ids.add(sku)
        m = mp_rows.get(sku)
        if not m:
            continue
        matched += 1

        if abs(float(r["qty"] or 0) - m["stock"]) > 1e-9:
            qty_changes.append((r["id"], r["name"], float(r["qty"] or 0), m["stock"]))
        if m["stock"] < 0:
            negative.append((r["id"], r["name"], m["stock"]))

        # Nom ham YesPOS'niki — u yerda tovar qayta nomlansa, bizda eskisi
        # qolib ketardi (2026-08-20 da 512 ta shunday nom to'planib qolgandi).
        # Eskirgan nom faqat ko'rinishga emas, YETKAZIB BERUVCHI ro'yxatini
        # moslashtirishga ham xalaqit beradi — nakladnoy YesPOS nomi bilan
        # keladi, bizniki esa boshqacha.
        if (m["name"] or "").strip() and (m["name"] or "").strip() != (r["name"] or "").strip():
            name_changes.append((r["id"], r["name"], m["name"]))

        if m["price"] <= 0:
            zero_price.append((r["id"], r["name"], float(r["sell_price"] or 0)))
        elif abs(float(r["sell_price"] or 0) - m["price"]) > 0.01:
            price_changes.append(
                (r["id"], r["name"], float(r["sell_price"] or 0), m["price"])
            )

    # YesPOS'da bor, Chinor'da yo'q — YANGI TOVARLAR.
    # Ilgari ular shunchaki sanalar edi va bazaga hech qachon tushmasdi:
    # do'konda yangi tovar kiritilsa, u botda/Mini App'da UMUMAN ko'rinmasdi
    # (2026-08-07 da 237 ta shunday tovar to'planib qolgan edi).
    only_mp = sorted(set(mp_rows) - local_ids, key=lambda s: int(s) if s.isdigit() else 0)
    new_products, new_zero_price = [], []
    for sku in only_mp:
        if not sku.isdigit():
            continue           # id sifatida ishlatib bo'lmaydi
        m = mp_rows[sku]
        if m["price"] <= 0:
            # Narxsiz tovar mijozga "0 so'm" bo'lib ko'rinadi — qo'shmaymiz,
            # narxi qo'yilishi bilan keyingi aylanishda o'zi tushadi.
            new_zero_price.append((sku, m["name"]))
            continue
        new_products.append((int(sku), m["name"], m["price"], m["stock"]))

    return {
        "harvest": harvest,
        "barcode_restore": barcode_restore,
        "matched": matched,
        "name_changes": name_changes,
        "local_total": len(local),
        "mp_total": len(mp_rows),
        "only_local": sorted(local_ids - set(mp_rows)),
        "only_mp": only_mp,
        "new_products": new_products,
        "new_zero_price": new_zero_price,
        "qty_changes": qty_changes,
        "price_changes": price_changes,
        "zero_price": zero_price,
        "negative": negative,
    }


KEEP_BACKUPS = 10          # nechta oxirgi zaxira nusxa saqlanadi


def _rotate_backups(pos_db_path: str, keep: int = KEEP_BACKUPS) -> None:
    """Eski `.bak-mp-*` nusxalarini o'chiradi, oxirgi `keep` tasini qoldiradi.

    Nima uchun: bu ko'prik har ishga tushganda butun bazadan nusxa oladi
    (~2.5 MB). Kuniga bir marta ishlaganda muammo yo'q edi, lekin 20 daqiqada
    bir marta = kuniga 72 nusxa = ~170 MB. Bir necha oyda disk to'lardi.
    Kunlik to'liq zaxira ALOHIDA ishlaydi (chinor-backup.timer → backups/),
    bu yerdagilar esa faqat "ko'prik buzsa darrov qaytarish" uchun.
    """
    import glob
    files = sorted(glob.glob(f"{pos_db_path}.bak-mp-*"))
    for old in files[:-keep] if keep > 0 else files:
        try:
            os.remove(old)
        except OSError:
            pass


def apply(pos_db_path: str, mp_rows: dict, p: dict) -> int:
    """Rejani bazaga yozadi. Avval zaxira nusxa oladi. Qaytaradi: tegilgan qator."""
    stamp = now_local().strftime("%Y%m%d-%H%M%S")
    backup = f"{pos_db_path}.bak-mp-{stamp}"
    shutil.copy2(pos_db_path, backup)
    print(f"Zaxira nusxa: {backup}")
    _rotate_backups(pos_db_path)

    conn = sqlite3.connect(pos_db_path, timeout=30)
    touched = 0
    try:
        conn.execute("BEGIN")
        # YANGI TOVARLAR — id sifatida YesPOS sku'si qo'yiladi, chunki butun
        # ko'prik shu moslikka tayanadi (id == sku). Tannarx va optom narx
        # 0 bo'lib qoladi: marketplace API ularni bermaydi, keyin do'kon
        # o'zi to'ldiradi. Rasm ham yo'q — shuning uchun bunday tovar
        # kanalga avtomatik e'lon QILINMAYDI (e'lon rasm talab qiladi).
        created = now_local().strftime("%Y-%m-%d %H:%M:%S")
        for pid, name, price, stock in p.get("new_products") or []:
            conn.execute(
                "INSERT OR IGNORE INTO products "
                "(id, name, description, sell_price, wholesale_price, "
                " cost_price, qty, unit, is_active, created_at, category_id) "
                "VALUES (?, ?, '', ?, 0, 0, ?, 'dona', 1, ?, 0)",
                (pid, name, round(price, 2), stock, created),
            )
            touched += 1
        for pid, _name, _old, new_qty in p["qty_changes"]:
            conn.execute("UPDATE products SET qty = ? WHERE id = ?", (new_qty, pid))
            touched += 1
        for pid, _name, _old, new_price in p["price_changes"]:
            conn.execute(
                "UPDATE products SET sell_price = ? WHERE id = ?",
                (round(new_price, 2), pid),
            )
            touched += 1
        # Nom — YesPOS'niki. `display_name` (mijozga chiroyli nom) TEGILMAYDI:
        # u qo'lda yozilgan va sinxron uni yuvib ketmasligi kerak.
        for pid, _old, new_name in p.get("name_changes") or []:
            conn.execute("UPDATE products SET name = ? WHERE id = ?", (new_name, pid))
            touched += 1
        # Saqlangan shtrix-kodlarni qaytaramiz — faqat bo'sh joylarga.
        for pid, bc in p["barcode_restore"]:
            conn.execute(
                "UPDATE products SET barcode = ? WHERE id = ? "
                "AND COALESCE(barcode,'') = ''",
                (bc, pid),
            )
            touched += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return touched


def _report(p: dict, pos_db_path: str, day: str) -> None:
    print(f"POS bazasi:  {pos_db_path}")
    print(f"Surat sanasi: {day}\n")
    print(f"  Chinor tovar : {p['local_total']:,}")
    print(f"  YesPOS tovar : {p['mp_total']:,}")
    print(f"  Mos kelgan   : {p['matched']:,}")
    print(f"  Faqat Chinor'da : {len(p['only_local']):,}  (YesPOS'da yo'q — tegilmaydi)")
    print(f"  Faqat YesPOS'da : {len(p['only_mp']):,}  (Chinor'da yo'q)\n")

    new = p.get("new_products") or []
    print(f"  YANGI qo'shiladi : {len(new):,}")
    if p.get("new_zero_price"):
        print(f"  Yangi, lekin narxi 0 (qo'shilmaydi) : {len(p['new_zero_price']):,}")
    for pid, name, price, stock in new[:10]:
        print(f"    + [{pid}] {name[:34]:<34} {price:>10,.0f} so'm  {stock:>7,.1f}")

    print(f"  Qoldiq o'zgaradi : {len(p['qty_changes']):,}")
    print(f"  Narx o'zgaradi   : {len(p['price_changes']):,}")
    if p["zero_price"]:
        print(f"  Narxi 0 (o'tkazib yuboriladi) : {len(p['zero_price']):,}")
    if p["negative"]:
        print(f"  Manfiy qoldiq (o'sha holicha yoziladi) : {len(p['negative']):,}")
    print(f"  Shtrix-kod: {len(p['harvest']):,} ta saqlanadi"
          + (f", {len(p['barcode_restore']):,} tasi qaytariladi"
             if p["barcode_restore"] else ""))

    if p["qty_changes"]:
        print("\n  Eng katta qoldiq o'zgarishlari:")
        for pid, name, old, new in sorted(
            p["qty_changes"], key=lambda x: abs(x[3] - x[2]), reverse=True
        )[:10]:
            print(f"    [{pid}] {name[:34]:<34} {old:>10,.1f} → {new:>10,.1f}")

    names = p.get("name_changes") or []
    if names:
        print(f"\n  NOM o'zgarishi: {len(names)}")
        for pid, old_n, new_n in names[:8]:
            print(f"    [{pid}] «{old_n[:30]}» → «{new_n[:30]}»")
        if len(names) > 8:
            print(f"    … yana {len(names) - 8} ta")

    if p["price_changes"]:
        print("\n  Eng katta narx o'zgarishlari:")
        for pid, name, old, new in sorted(
            p["price_changes"], key=lambda x: abs(x[3] - x[2]), reverse=True
        )[:10]:
            print(f"    [{pid}] {name[:34]:<34} {old:>10,.0f} → {new:>10,.0f}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="YesPOS → Chinor POS ko'prigi")
    ap.add_argument("--apply", action="store_true", help="haqiqatan yozish")
    ap.add_argument("--pos-db", default=config.POS_DB)
    args = ap.parse_args(argv)

    if not os.path.exists(args.pos_db):
        print(f"pos.db topilmadi: {args.pos_db}\nPOS_DB muhit o'zgaruvchisi bilan ko'rsating.")
        return 1

    with MarketplaceDB() as db:
        mp_rows, day = _fetch_marketplace_rows(db)
        if not mp_rows:
            print("Marketplace bazasida qoldiq surati yo'q. Avval: python -m marketplace.sync all")
            return 1

        p = plan(args.pos_db, mp_rows, db.get_barcodes())
        _report(p, args.pos_db, day)

        if not args.apply:
            print("\n[dry-run] Hech narsa yozilmadi. Yozish uchun: --apply")
            return 0

        # Avval yig'amiz (yo'qolmasin), keyin yozamiz.
        db.save_barcodes(p["harvest"])
        # O'zgarish bo'lmasa bazaga ham, diskka ham tegmaymiz. 20 daqiqada bir
        # ishlaganda o'zgarishsiz aylanishlar ko'p bo'ladi — har biriga nusxa
        # olish bekorga disk yeydi.
        if not (p["qty_changes"] or p["price_changes"] or p["barcode_restore"]
                or p["new_products"] or p.get("name_changes")):
            print("\n✓ O'zgarish yo'q — bazaga tegilmadi.")
            return 0
        n = apply(args.pos_db, mp_rows, p)
        print(f"\n✓ {n:,} qator yangilandi.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
