"""Rasmi bor tovarlarni RASMGA QARAB kategoriyalash.

Uch bosqichli tizimning uchinchisi:
    categorize.py         → nomdagi kalit so'z bo'yicha (tez, tekin, 66%)
    categorize_ai.py      → nomdan AI bilan (qolganiga)
    categorize_vision.py  → RASMDAN AI bilan (eng aniq)  ← shu fayl

Rasm nomdan aniqroq: "Krakadil knobkali" nima ekanini rasmga qarab bilish
mumkin, nomidan esa yo'q. Shuning uchun rasmi bor tovar rasmi bo'yicha
qayta ko'rib chiqiladi.

MAXFIYLIK: AI ga faqat rasm va tovar nomi boradi (narx/qoldiq emas).

    python -m marketplace.categorize_vision                  # sinov, 5 ta
    python -m marketplace.categorize_vision --all
    python -m marketplace.categorize_vision --all --apply
    python -m marketplace.categorize_vision --all --apply --recheck
        (--recheck: kategoriyasi BOR tovarlarni ham qayta ko'radi)
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from collections import Counter

from marketplace import config

config.load_bot_env()          # AI kalitlari chinor-bot/.env da

_BOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chinor-bot")
for _p in (_BOT_DIR, "/opt/chinor/chinor-bot"):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

_DATA_DIR = os.getenv("DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
UPLOAD_DIR = os.path.abspath(
    os.getenv("UPLOAD_DIR") or os.path.join(_DATA_DIR, "uploads"))


def _rows(pos_db: str, recheck: bool):
    conn = sqlite3.connect(f"file:{pos_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(products)")}
        orig = ("COALESCE(image_orig_url,'')" if "image_orig_url" in cols
                else "''")
        sql = (f"SELECT id, name, image_url, {orig} AS orig, "
               f"COALESCE(category_id,0) AS cid FROM products "
               f"WHERE is_active = 1 AND COALESCE(image_url,'') <> '' "
               f"ORDER BY id")
        rows = conn.execute(sql).fetchall()
    finally:
        conn.close()
    return rows if recheck else [r for r in rows if not r["cid"]]


def _categories(pos_db: str) -> list:
    conn = sqlite3.connect(f"file:{pos_db}?mode=ro", uri=True)
    try:
        return [r[0] for r in conn.execute(
            "SELECT name FROM categories ORDER BY name")]
    finally:
        conn.close()


def _local(url: str) -> str:
    """AI ga ASL (kesilmagan) rasmni beramiz — unda kontekst ko'proq."""
    name = os.path.basename((url or "").split("?")[0])
    p = os.path.join(UPLOAD_DIR, name)
    return p if name and os.path.isfile(p) else ""


# ─── Davom ettirish (bepul kvota bir yugurishga yetmaydi) ─────────────────
# Bepul AI kvotasi ~30 ta rasmdan keyin tugaydi (429 → 1 soat sovish). Shuning
# uchun qaysi tovar ALLAQACHON rasm bo'yicha ko'rilgani yozib boriladi: skriptni
# keyinroq yana ishga tushirsangiz, qolganidan davom etadi (boshidan emas).
_DONE_FILE = os.path.join(_DATA_DIR, "vision_cat_done.json")


def _done_load() -> set:
    try:
        with open(_DONE_FILE) as f:
            return set(json.load(f).get("ids", []))
    except (OSError, ValueError):
        return set()


def _done_save(ids: set):
    try:
        with open(_DONE_FILE, "w") as f:
            json.dump({"ids": sorted(ids)}, f)
    except OSError as e:
        print(f"(eslatma: {_DONE_FILE} yozilmadi: {e})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Rasmga qarab kategoriyalash")
    ap.add_argument("--apply", action="store_true", help="pos.db ga yozish")
    ap.add_argument("--all", action="store_true", help="hammasi (aks holda 5 ta)")
    ap.add_argument("--recheck", action="store_true",
                    help="kategoriyasi bor tovarlarni ham qayta ko'rish")
    ap.add_argument("--again", action="store_true",
                    help="ilgari ko'rilganlarni ham qaytadan (ro'yxatni e'tiborsiz qoldiradi)")
    ap.add_argument("--sleep", type=float, default=4.0,
                    help="so'rovlar orasidagi tanaffus, sek (kvota tez tugamasin)")
    ap.add_argument("--stop-after", type=int, default=8,
                    help="ketma-ket shuncha bo'sh javobdan keyin to'xtaydi (0 = to'xtamasin)")
    ap.add_argument("--pos-db", default=config.POS_DB)
    args = ap.parse_args(argv)

    if not os.path.exists(args.pos_db):
        print(f"pos.db topilmadi: {args.pos_db}")
        return 1

    from bot import ai_category

    # Bazadagi bo'limlar + standart ro'yxat (baza kam bo'lsa AI tanlay olsin).
    cats = ai_category.full_list(_categories(args.pos_db))
    rows = _rows(args.pos_db, args.recheck)
    done = set() if args.again else _done_load()
    if done:
        before = len(rows)
        rows = [r for r in rows if r["id"] not in done]
        print(f"Ilgari ko'rilgan: {before - len(rows)} ta o'tkazib yuborildi "
              f"(qaytadan uchun --again)")
    print(f"Bo'limlar    : {len(cats)} ta")
    print(f"Rasmli tovar : {len(rows):,} ta ko'riladi")
    if not rows:
        print("Hammasi ko'rilgan.")
        return 0
    if not args.all:
        rows = rows[:5]
        print("(sinov rejimi — 5 ta; hammasi uchun --all)")

    assign, stats, empty, streak = {}, Counter(), 0, 0
    stopped = False
    for i, r in enumerate(rows, 1):
        img = _local(r["orig"] or r["image_url"])
        cat = ai_category.suggest(cats, r["name"] or "", img)
        if cat:
            assign[r["id"]] = cat
            stats[cat] += 1
            done.add(r["id"])
            streak = 0
        else:
            empty += 1
            streak += 1
        note = "" if img else "  (rasm fayli topilmadi)"
        print(f"  [{i}/{len(rows)}] {(r['name'] or '')[:34]:34} → "
              f"{cat or '—'}{note}", flush=True)
        # Ketma-ket bo'sh javob = AI kvotasi tugagan (429 → 1 soat sovish).
        # Qolganini behuda urinmaymiz — bir soatdan keyin yana ishga tushiring.
        if args.stop_after and streak >= args.stop_after:
            stopped = True
            print(f"\n⏸  {streak} marta ketma-ket javob bo'lmadi — AI kvotasi "
                  f"tugagan ko'rinadi. To'xtatildi.\n   Bir soatdan keyin shu "
                  f"buyruqni yana ishga tushiring — qolganidan davom etadi.")
            break
        if args.sleep and i < len(rows):
            time.sleep(args.sleep)

    if args.apply and not args.again:
        _done_save(done)

    print(f"\nAniqlandi: {len(assign):,}   Aniqlanmadi: {empty}"
          f"{'  (to‘xtatildi)' if stopped else ''}")
    for c, k in stats.most_common():
        print(f"  {k:>5}  {c}")

    if not args.apply:
        print("\n[sinov] Hech narsa yozilmadi. Yozish uchun: --apply")
        return 0
    if not assign:
        return 0

    from marketplace.categorize import apply_to_db
    made, updated = apply_to_db(args.pos_db, assign)
    print(f"\n✓ {made} yangi bo'lim, {updated:,} tovar yangilandi.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
