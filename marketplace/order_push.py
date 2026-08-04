"""Chinor'da urilgan cheklarni YesPOS'ga savdo qilib yuboradi.

Nima uchun kerak: sinxron bir tomonlama — YesPOS qoldig'i Chinor'ga ko'chiriladi.
Chinor'da sotuv bo'lsa, u faqat mahalliy qoldiqni kamaytiradi, keyingi sinxron
esa uni YesPOS qiymati bilan ustidan yozadi — ya'ni sotuv qoldiqqa ta'sirsiz
qolardi. Bu modul aylanani yopadi: chek YesPOS'ga yuboriladi, YesPOS qoldiqni
o'zi kamaytiradi, keyingi sinxron allaqachon to'g'ri raqamni olib keladi.

To'lov turi: hammasi bitta `PAYMENT_ID` bilan ketadi. To'lov turlarini ajratish
(naqd/karta/perechisleniye) Chinor tomonida, kassadagi pulni hisoblash uchun
yuritiladi — YesPOS uchun faqat qoldiq muhim.

XAVFSIZLIK: birinchi ishga tushirilganda hech narsa yuborilmaydi, faqat
"shu yergacha bo'lgani eski" deb belgi qo'yiladi. Aks holda bazadagi barcha
eski cheklar YesPOS'ga soxta savdo bo'lib tushardi.

Ishlatish:
    python -m marketplace.order_push              # ko'rsatadi (dry-run)
    python -m marketplace.order_push --apply      # haqiqatan yuboradi
    python -m marketplace.order_push --status     # holat
"""

import argparse
import json
import logging
import os
import sqlite3
import sys

from marketplace import config
from marketplace.client import YesposClient, YesposError
from marketplace.db import MarketplaceDB, now_local

log = logging.getLogger("marketplace.order_push")

# Barcha cheklar shu to'lov turi bilan ketadi — YesPOS uchun qoldiq muhim,
# pul taqsimoti Chinor tomonida yuritiladi.
PAYMENT_ID = int(os.getenv("YESPOS_PAYMENT_ID", "1"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS pushed_sales (
    sale_id    INTEGER PRIMARY KEY,
    pushed_at  TEXT    NOT NULL,
    type       INTEGER NOT NULL,      -- 1 savdo, 2 qaytarish
    total      REAL    NOT NULL DEFAULT 0,
    ok         INTEGER NOT NULL DEFAULT 0,
    error      TEXT
);
-- Boshlang'ich chegara: shu id dan keyingi cheklargina yuboriladi.
CREATE TABLE IF NOT EXISTS push_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _ensure_schema(db: MarketplaceDB) -> None:
    db.conn.executescript(SCHEMA)
    db.conn.commit()


def _sku_to_yespos_id(db: MarketplaceDB) -> dict:
    """Chinor product_id (== YesPOS sku) → YesPOS product id."""
    return {
        str(r["sku"]): int(r["id"])
        for r in db.conn.execute("SELECT id, sku FROM products WHERE sku != ''")
    }


def _watermark(db: MarketplaceDB) -> int:
    r = db.conn.execute(
        "SELECT value FROM push_state WHERE key = 'watermark'"
    ).fetchone()
    return int(r["value"]) if r else -1


def _set_watermark(db: MarketplaceDB, sale_id: int) -> None:
    db.conn.execute(
        "INSERT INTO push_state (key, value) VALUES ('watermark', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(sale_id),),
    )
    db.conn.commit()


def pending_sales(db: MarketplaceDB, pos_db_path: str) -> list:
    """Hali yuborilmagan cheklar (chegaradan keyingi, muvaffaqiyatsizlar ham)."""
    wm = _watermark(db)
    done = {
        r["sale_id"]
        for r in db.conn.execute("SELECT sale_id FROM pushed_sales WHERE ok = 1")
    }

    conn = sqlite3.connect(f"file:{pos_db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, items, total, created_at, is_return, is_internal, "
            "receipt_no FROM sales WHERE id > ? ORDER BY id",
            (wm,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows if r["id"] not in done]


def build_payload(sale: dict, id_map: dict) -> tuple:
    """(payload, xato) — chek uchun gateway body'sini yig'adi."""
    try:
        items = json.loads(sale["items"] or "[]")
    except json.JSONDecodeError:
        return None, "items JSON o'qilmadi"
    if not items:
        return None, "items bo'sh"

    out, missing = [], []
    for it in items:
        sku = str(it.get("product_id"))
        ypid = id_map.get(sku)
        if ypid is None:
            missing.append(sku)
            continue
        qty = abs(float(it.get("qty") or 0))
        price = abs(float(it.get("price") or 0))
        if qty <= 0:
            continue
        out.append({"item": ypid, "qty": qty, "price": price})

    if missing:
        return None, f"YesPOS'da yo'q tovar(lar): {', '.join(missing[:5])}"
    if not out:
        return None, "yuboriladigan qator qolmadi"

    # Gateway talabi: items yig'indisi payments yig'indisiga TENG bo'lishi shart.
    # Shuning uchun to'lov summasi qatorlardan hisoblanadi, chekning `total`
    # maydonidan emas — chegirma bo'lsa ular farq qiladi.
    total = round(sum(i["qty"] * i["price"] for i in out), 2)
    type_ = 2 if int(sale.get("is_return") or 0) else 1
    note = f"Chinor #{sale.get('receipt_no') or sale['id']}"
    if int(sale.get("is_internal") or 0):
        note += " (ichki rasxod)"

    return {
        "type": type_,
        "note": note,
        "delivery_info": None,
        "items": out,
        "payments": [{"payment_id": PAYMENT_ID, "value": total}],
    }, None


def push(db: MarketplaceDB, client: YesposClient, sales: list, id_map: dict) -> tuple:
    ok = fail = 0
    for s in sales:
        payload, err = build_payload(s, id_map)
        if err:
            log.warning("chek #%s o'tkazib yuborildi — %s", s["id"], err)
            db.conn.execute(
                "INSERT INTO pushed_sales (sale_id, pushed_at, type, total, ok, error) "
                "VALUES (?,?,?,?,0,?) ON CONFLICT(sale_id) DO UPDATE SET "
                "pushed_at=excluded.pushed_at, error=excluded.error",
                (s["id"], now_local().isoformat(timespec="seconds"), 0, 0.0, err),
            )
            db.conn.commit()
            fail += 1
            continue

        total = payload["payments"][0]["value"]
        try:
            client.create_order(
                items=payload["items"],
                payments=payload["payments"],
                type_=payload["type"],
                note=payload["note"],
            )
        except YesposError as e:
            log.error("chek #%s yuborilmadi: %s", s["id"], e)
            db.conn.execute(
                "INSERT INTO pushed_sales (sale_id, pushed_at, type, total, ok, error) "
                "VALUES (?,?,?,?,0,?) ON CONFLICT(sale_id) DO UPDATE SET "
                "pushed_at=excluded.pushed_at, error=excluded.error",
                (
                    s["id"],
                    now_local().isoformat(timespec="seconds"),
                    payload["type"],
                    total,
                    str(e)[:400],
                ),
            )
            db.conn.commit()
            fail += 1
            continue

        db.conn.execute(
            "INSERT INTO pushed_sales (sale_id, pushed_at, type, total, ok, error) "
            "VALUES (?,?,?,?,1,NULL) ON CONFLICT(sale_id) DO UPDATE SET "
            "pushed_at=excluded.pushed_at, ok=1, error=NULL",
            (
                s["id"],
                now_local().isoformat(timespec="seconds"),
                payload["type"],
                total,
            ),
        )
        db.conn.commit()
        log.info("chek #%s yuborildi (type %s, %s so'm)", s["id"], payload["type"], f"{total:,.0f}")
        ok += 1
    return ok, fail


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Chinor cheklarini YesPOS'ga yuborish")
    ap.add_argument("--apply", action="store_true", help="haqiqatan yuborish")
    ap.add_argument("--status", action="store_true", help="holatni ko'rsatish")
    ap.add_argument("--pos-db", default=config.POS_DB)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )

    if not os.path.exists(args.pos_db):
        print(f"pos.db topilmadi: {args.pos_db}")
        return 1

    with MarketplaceDB() as db:
        _ensure_schema(db)

        if args.status:
            wm = _watermark(db)
            n_ok = db.conn.execute(
                "SELECT COUNT(*) n FROM pushed_sales WHERE ok=1"
            ).fetchone()["n"]
            n_bad = db.conn.execute(
                "SELECT COUNT(*) n FROM pushed_sales WHERE ok=0"
            ).fetchone()["n"]
            print(f"Chegara (bundan keyingi cheklar yuboriladi): #{wm}")
            print(f"Yuborilgan: {n_ok}   |   Xato: {n_bad}")
            for r in db.conn.execute(
                "SELECT sale_id, pushed_at, type, total, ok, error FROM pushed_sales "
                "ORDER BY sale_id DESC LIMIT 10"
            ):
                mark = "✓" if r["ok"] else "✗"
                tail = f" — {r['error']}" if r["error"] else ""
                print(f"  {mark} #{r['sale_id']:<6} {r['pushed_at']}  type {r['type']}  "
                      f"{r['total']:>12,.0f}{tail}")
            return 0

        # Birinchi ishga tushirish — eski cheklarni yubormaslik uchun chegara.
        if _watermark(db) < 0:
            conn = sqlite3.connect(f"file:{args.pos_db}?mode=ro", uri=True)
            try:
                last = conn.execute("SELECT COALESCE(MAX(id), 0) FROM sales").fetchone()[0]
            finally:
                conn.close()
            _set_watermark(db, int(last))
            print(
                f"Birinchi ishga tushirish: chegara #{last} qilib belgilandi.\n"
                f"Bazadagi {last} ta eski chek YUBORILMAYDI (ular YesPOS'da allaqachon\n"
                f"hisobga olingan yoki sinov cheklari). Bundan keyingi yangi cheklar\n"
                f"yuboriladi. Keyingi ishga tushirishda ko'rasan."
            )
            return 0

        id_map = _sku_to_yespos_id(db)
        if not id_map:
            print("Katalog bo'sh. Avval: python -m marketplace.sync catalog")
            return 1

        sales = pending_sales(db, args.pos_db)
        print(f"Yuborilishi kerak bo'lgan chek: {len(sales)}\n")
        for s in sales[:20]:
            payload, err = build_payload(s, id_map)
            if err:
                print(f"  ✗ #{s['id']}  {s['created_at']}  — {err}")
            else:
                t = payload["payments"][0]["value"]
                kind = "QAYTARISH" if payload["type"] == 2 else "savdo"
                print(f"  → #{s['id']}  {s['created_at']}  {kind:<9} "
                      f"{len(payload['items'])} qator  {t:>12,.0f} so'm")

        if not args.apply:
            print("\n[dry-run] Hech narsa yuborilmadi. Yuborish uchun: --apply")
            return 0
        if not sales:
            return 0

        client = YesposClient()
        ok, fail = push(db, client, sales, id_map)
        print(f"\n✓ yuborildi: {ok}   ✗ xato: {fail}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
