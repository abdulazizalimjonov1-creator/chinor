"""Mavjud tovar rasmlarini brendlash (fonni kesish + och yashil fon + logotip).

Yangi rasmlar yuklanganda `bot/web_api.py` buni o'zi qiladi. Bu skript esa
ALLAQACHON yuklangan rasmlar uchun — bir marta yugurtiriladi.

Nima qiladi:
  • har bir rasmli tovar uchun ASL faylni topadi
  • `bot/imageproc.brand()` orqali brendlangan nusxa yasaydi (`..._br.jpg`)
  • bazaga yozadi: image_url = brendlangan, image_orig_url = ASL
  • asl fayl O'CHIRILMAYDI

Qayta yugurtirish xavfsiz: brendlangani o'tkazib yuboriladi (--force bilan
qaytadan qilinadi, va u ham ASLIDAN boshlaydi — sifat pasaymaydi).

    python -m marketplace.brand_images              # sinov (5 ta)
    python -m marketplace.brand_images --all
    python -m marketplace.brand_images --all --apply
"""

import argparse
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime

from marketplace import config

config.load_bot_env()

_BOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chinor-bot")
for _p in (_BOT_DIR, "/opt/chinor/chinor-bot"):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

# Rasmlar bot bilan BIR XIL papkada — .env dagi DATA_DIR/UPLOAD_DIR.
_DATA_DIR = os.getenv("DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
UPLOAD_DIR = os.path.abspath(
    os.getenv("UPLOAD_DIR") or os.path.join(_DATA_DIR, "uploads"))


def _ensure_column(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(products)")}
    for col in ("image_orig_url", "usage_tags"):
        if col not in cols:
            conn.execute(f"ALTER TABLE products ADD COLUMN {col} TEXT DEFAULT ''")
    conn.commit()


def _rows(pos_db: str, force: bool):
    conn = sqlite3.connect(pos_db, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_column(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(products)")}
        tags_col = "COALESCE(p.usage_tags,'')" if "usage_tags" in cols else "''"
        sql = (f"SELECT p.id, p.name, p.image_url, "
               f"COALESCE(p.image_orig_url,'') AS orig, {tags_col} AS tags, "
               f"COALESCE(c.name,'') AS cat "
               f"FROM products p LEFT JOIN categories c ON c.id = p.category_id "
               f"WHERE p.is_active = 1 AND COALESCE(p.image_url,'') <> '' "
               f"ORDER BY p.id")
        rows = conn.execute(sql).fetchall()
    finally:
        conn.close()
    if force:
        return rows
    # Brendlangani — image_orig_url to'ldirilgan bo'ladi.
    return [r for r in rows if not r["orig"]]


def _tags_for(row) -> list:
    """Tovarning «qayerda ishlatiladi» teglari: saqlangani, bo'lmasa bo'limdan.

    AI shart emas — bo'lim allaqachon ma'lum bo'lgani uchun mantiqiy joylar
    ro'yxati tayyor. AI faqat yangi yuklangan rasmlarda aniqroq variant beradi.
    """
    stored = [t.strip() for t in (row["tags"] or "").split(",") if t.strip()]
    if stored:
        return stored[:3]
    try:
        from bot.ai_category import CATEGORY_PLACES
    except Exception:
        return []
    return CATEGORY_PLACES.get(row["cat"], [])[:3]


def _local(url: str) -> str:
    name = os.path.basename((url or "").split("?")[0])
    p = os.path.join(UPLOAD_DIR, name)
    return p if name and os.path.isfile(p) else ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Rasmlarni brendlash")
    ap.add_argument("--apply", action="store_true", help="bazaga yozish")
    ap.add_argument("--all", action="store_true", help="hammasi (aks holda 5 ta)")
    ap.add_argument("--force", action="store_true",
                    help="brendlanganini ham qayta ishlash")
    ap.add_argument("--pos-db", default=config.POS_DB)
    args = ap.parse_args(argv)

    if not os.path.exists(args.pos_db):
        print(f"pos.db topilmadi: {args.pos_db}")
        return 1

    from bot import imageproc
    if not imageproc.has_cutout():
        print("⚠️  onnxruntime o'rnatilmagan — fon KESILMAYDI, faqat fon+logotip "
              "qo'yiladi.\n    O'rnatish: pip install onnxruntime\n")

    rows = _rows(args.pos_db, args.force)
    print(f"Rasm papkasi : {UPLOAD_DIR}")
    print(f"Ishlanadigan : {len(rows):,} ta rasm")
    if not rows:
        return 0
    if not args.all:
        rows = rows[:5]
        print("(sinov rejimi — 5 ta; hammasi uchun --all)")

    updates, cut_n, miss = [], 0, 0
    t0 = time.time()
    for i, r in enumerate(rows, 1):
        src_url = r["orig"] or r["image_url"]
        src = _local(src_url)
        if not src:
            miss += 1
            continue
        tags = _tags_for(r)
        bname = f"prod_{r['id']}_{int(time.time())}_br.jpg"
        info = imageproc.brand_file(src, os.path.join(UPLOAD_DIR, bname),
                                    tags=tags, category=r["cat"])
        if not info.get("ok"):
            print(f"  [{i}/{len(rows)}] #{r['id']} xato: {info.get('error')}")
            continue
        cut_n += 1 if info.get("cut") else 0
        updates.append((f"/api/img/{bname}", src_url, ", ".join(tags), r["id"]))
        if i % 20 == 0 or i == len(rows):
            print(f"  [{i}/{len(rows)}] {time.time() - t0:.0f}s")

    print(f"\nBrendlandi   : {len(updates):,}  (foni kesilgani: {cut_n:,})")
    if miss:
        print(f"Fayli yo'q   : {miss}")
    if not args.apply:
        print("\n[sinov] Bazaga yozilmadi. Yozish uchun: --apply")
        print(f"Natijani ko'rish: {UPLOAD_DIR}/*_br.jpg")
        return 0
    if not updates:
        return 0

    bak = f"{args.pos_db}.bak-img-{datetime.now():%Y%m%d-%H%M%S}"
    shutil.copy2(args.pos_db, bak)
    print(f"Zaxira nusxa: {bak}")

    conn = sqlite3.connect(args.pos_db, timeout=30)
    try:
        _ensure_column(conn)
        conn.executemany(
            "UPDATE products SET image_url = ?, image_orig_url = ?, "
            "usage_tags = ? WHERE id = ?", updates)
        conn.commit()
    finally:
        conn.close()
    print(f"✓ {len(updates):,} tovar rasmi yangilandi.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
