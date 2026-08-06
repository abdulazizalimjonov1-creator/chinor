"""Har bir rasmli tovarni AI bilan «boyitish»: aniq teglar + toza tavsif.

Ikki ish qiladi (ikkalasi ham RASMGA qarab):
  1. «Qayerda ishlatiladi» teglari — `brand_images.py` ularni BO'LIMDAN
     oladi (tez va tekin, lekin umumiy: hamma tozalash vositasiga bir xil).
     Bu skript esa aynan shu tovarga mos joylarni tanlaydi ("Pech · Grill").
     So'ng rasm yangi lenta bilan qayta chiziladi.
  2. Kanal tavsifi — eski tavsiflarda muammo bor edi: turkcha harflar
     ("peçetkalar"), kirill o'zbekcha ("Мистер Пропернинг…") va bir tilli
     uzun matnlar. Endi tekshiruvdan o'tmagani QAYTA YOZILADI: 🇺🇿 sof
     o'zbekcha (lotin) + 🇷🇺 ruscha, har biri bitta jumla.

Bepul AI kvotasi kichik, shuning uchun:
  • so'rovlar orasida tanaffus (`--sleep`)
  • ketma-ket bo'sh javoblardan keyin o'zi to'xtaydi (`--stop-after`)
  • ko'rilganlar `DATA_DIR/usage_tags_done.json` ga yoziladi → keyingi
    yugurishda QOLGANIDAN davom etadi

    python -m marketplace.enrich_products                 # sinov (5 ta)
    python -m marketplace.enrich_products --all --apply
    python -m marketplace.enrich_products --all --apply --again   # boshidan
    python -m marketplace.enrich_products --all --apply --no-desc # faqat teglar
"""

import argparse
import json
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

_DATA_DIR = os.getenv("DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
UPLOAD_DIR = os.path.abspath(
    os.getenv("UPLOAD_DIR") or os.path.join(_DATA_DIR, "uploads"))
DONE_FILE = os.path.join(_DATA_DIR, "enrich_done.json")


def _done_load() -> set:
    try:
        with open(DONE_FILE) as f:
            return set(json.load(f).get("ids", []))
    except (OSError, ValueError):
        return set()


def _done_save(ids: set):
    try:
        with open(DONE_FILE, "w") as f:
            json.dump({"ids": sorted(ids)}, f)
    except OSError as e:
        print(f"(eslatma: {DONE_FILE} yozilmadi: {e})")


def _rows(pos_db: str):
    conn = sqlite3.connect(f"file:{pos_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT p.id, p.name, p.image_url, "
            "COALESCE(p.image_orig_url,'') AS orig, "
            "COALESCE(p.usage_tags,'') AS tags, COALESCE(c.name,'') AS cat, "
            "COALESCE(p.description,'') AS descr "
            "FROM products p LEFT JOIN categories c ON c.id = p.category_id "
            "WHERE p.is_active = 1 AND COALESCE(p.image_url,'') <> '' "
            "ORDER BY p.id"
        ).fetchall()
    finally:
        conn.close()


def _local(url: str) -> str:
    name = os.path.basename((url or "").split("?")[0])
    p = os.path.join(UPLOAD_DIR, name)
    return p if name and os.path.isfile(p) else ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AI bilan aniq «qayerda» teglari")
    ap.add_argument("--apply", action="store_true", help="bazaga yozish + rasmni qayta chizish")
    ap.add_argument("--all", action="store_true", help="hammasi (aks holda 5 ta)")
    ap.add_argument("--again", action="store_true", help="ko'rilganlarni ham qaytadan")
    ap.add_argument("--no-desc", action="store_true",
                    help="tavsifga tegilmasin (faqat teglar)")
    ap.add_argument("--redo-desc", action="store_true",
                    help="HAMMA tavsifni rasmdan qayta yozish (eskisi nomdan "
                         "yozilgan bo'lsa shu kerak)")
    ap.add_argument("--redo-tags", action="store_true",
                    help="hamma tegni ham qaytadan (rasmdan)")
    ap.add_argument("--sleep", type=float, default=4.0)
    ap.add_argument("--stop-after", type=int, default=8)
    ap.add_argument("--pos-db", default=config.POS_DB)
    args = ap.parse_args(argv)

    if not os.path.exists(args.pos_db):
        print(f"pos.db topilmadi: {args.pos_db}")
        return 1

    from bot import ai_category, imageproc

    import asyncio as _aio          # noqa: F401  (pastda ishlatiladi)
    from bot import web_api as _wa

    rows = _rows(args.pos_db)
    done = set() if args.again else _done_load()

    # Har bir tovar uchun NIMA kerakligini oldindan aniqlaymiz — allaqachon
    # bajarilganiga AI so'rovi ketmasin (kvota qimmat).
    todo = []
    for r in rows:
        need_tags = args.redo_tags or r["id"] not in done
        need_desc = (not args.no_desc) and (
            args.redo_desc or not _wa._desc_ok(r["descr"]))
        if need_tags or need_desc:
            todo.append((r, need_tags, need_desc))

    print(f"Rasmli tovar : {len(rows)}   |   ish bor: {len(todo)}"
          f"   (teg: {sum(1 for _, t, _ in todo if t)}, "
          f"tavsif: {sum(1 for _, _, d in todo if d)})")
    if not todo:
        print("Hammasi tayyor (--again bilan qaytadan).")
        return 0
    if not args.all:
        todo = todo[:5]
        print("(sinov rejimi — 5 ta; hammasi uchun --all)")

    if args.apply:
        bak = f"{args.pos_db}.bak-tags-{datetime.now():%Y%m%d-%H%M%S}"
        shutil.copy2(args.pos_db, bak)
        print(f"Zaxira nusxa: {bak}\n")

    import asyncio
    web_api = _wa

    ok = fixed = streak = 0
    for i, (r, need_tags, need_desc) in enumerate(todo, 1):
        img = _local(r["orig"] or r["image_url"])
        name = r["name"] or ""
        line = f"  [{i}/{len(todo)}] {name[:28]:30}"
        tags = ai_category.usage_tags(name, img) if need_tags else []

        # ── Tavsif: tekshiruvdan o'tmasa qayta yoziladi ──────────────────
        new_desc = ""
        if need_desc:
            try:
                new_desc = asyncio.run(
                    web_api._gemini_describe_image(img, name)) if img else ""
            except Exception as e:
                print(f"{line} tavsif xatosi: {e}", flush=True)

        if not tags and not new_desc:
            streak += 1
            print(f"{line} → —", flush=True)
        else:
            streak = 0
            done.add(r["id"])
            bits = []
            if tags:
                ok += 1
                bits.append(", ".join(tags))
            if new_desc:
                fixed += 1
                bits.append("✎ " + new_desc.splitlines()[0][:44])
            print(f"{line} → " + "  |  ".join(bits), flush=True)
            if args.apply:
                if tags:
                    _save(args.pos_db, r["id"], tags)
                    _redraw(imageproc, r, tags)
                if new_desc:
                    _save_desc(args.pos_db, r["id"], new_desc)

        if args.stop_after and streak >= args.stop_after:
            print(f"\n⏸  {streak} marta ketma-ket javob yo'q — AI kvotasi "
                  f"tugagan ko'rinadi. Keyinroq shu buyruqni qayta ishga "
                  f"tushiring, qolganidan davom etadi.")
            break
        if args.sleep and i < len(todo):
            time.sleep(args.sleep)

    if args.apply and not args.again:
        _done_save(done)
    print(f"\nAniq teg qo'yildi: {ok} ta   |   tavsif qayta yozildi: {fixed} ta"
          + ("" if args.apply else "   [sinov — hech narsa yozilmadi]"))
    return 0


def _save_desc(pos_db: str, pid: int, desc: str):
    conn = sqlite3.connect(pos_db, timeout=30)
    try:
        conn.execute("UPDATE products SET description=? WHERE id=?", (desc, pid))
        conn.commit()
    finally:
        conn.close()


def _save(pos_db: str, pid: int, tags: list):
    conn = sqlite3.connect(pos_db, timeout=30)
    try:
        conn.execute("UPDATE products SET usage_tags=? WHERE id=?",
                     (", ".join(tags), pid))
        conn.commit()
    finally:
        conn.close()


def _redraw(imageproc, row, tags: list):
    """Rasmni yangi lenta bilan qayta chizadi (har doim ASL rasmdan)."""
    src = _local(row["orig"] or row["image_url"])
    if not src:
        return
    bname = f"prod_{row['id']}_{int(time.time())}_br.jpg"
    info = imageproc.brand_file(src, os.path.join(UPLOAD_DIR, bname), tags=tags,
                                category=(row["cat"] if "cat" in row.keys() else ""))
    if not info.get("ok"):
        return
    conn = sqlite3.connect(config.POS_DB, timeout=30)
    try:
        conn.execute("UPDATE products SET image_url=? WHERE id=?",
                     (f"/api/img/{bname}", row["id"]))
        conn.commit()
    finally:
        conn.close()
    # Eski brendlangan nusxalarni tozalaymiz (disk to'lmasin).
    import glob
    for f in glob.glob(os.path.join(UPLOAD_DIR, f"prod_{row['id']}_*_br.jpg")):
        if os.path.basename(f) != bname:
            try:
                os.remove(f)
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
