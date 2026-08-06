"""Rasmli tovarlarni kanalga qayta e'lon qilish (yangi ko'rinishda).

Nima uchun: post ko'rinishi yangilandi (ikki rasmli albom, «qayerda
ishlatiladi», qoldiq, bo'lim hashtagi). Eski postlar esa eski ko'rinishda
qolgan. Bu skript ularni yangilaydi.

ESKI POST O'CHIRILADI — `products.channel_msg_ids` da saqlangan xabarlar
o'chirilib, o'rniga yangisi yuboriladi. Ya'ni kanalda takror post paydo
bo'lmaydi.

Tartib: BO'LIM bo'yicha guruhlab yuboriladi — kanal lentasi tartibli
ko'rinadi (santexnika santexnika bilan, bo'yoq bo'yoq bilan).

Telegram cheklovi: kanalga daqiqasiga ~20 xabar. Albom = 2 xabar, shuning
uchun standart tanaffus 6 soniya (≈ 20 xabar/daqiqa).

    python -m marketplace.channel_repost                 # sinov (3 ta)
    python -m marketplace.channel_repost --all --send
    python -m marketplace.channel_repost --all --send --only-new
"""

import argparse
import asyncio
import os
import sqlite3
import sys

from marketplace import config

config.load_bot_env()

_BOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chinor-bot")
for _p in (_BOT_DIR, "/opt/chinor/chinor-bot"):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)


def _rows(pos_db: str, only_new: bool):
    conn = sqlite3.connect(f"file:{pos_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT p.id, p.name, COALESCE(c.name,'') AS cat, "
            "COALESCE(p.channel_msg_ids,'') AS mids, "
            "COALESCE(p.channel_msg_id,0) AS mid "
            "FROM products p LEFT JOIN categories c ON c.id = p.category_id "
            "WHERE p.is_active = 1 AND COALESCE(p.image_url,'') <> '' "
            "AND COALESCE(p.qty,0) > 0 "
            "ORDER BY c.name, p.id"
        ).fetchall()
    finally:
        conn.close()
    if only_new:
        rows = [r for r in rows if not r["mids"] and not r["mid"]]
    return rows


async def _run(args) -> int:
    from aiogram import Bot
    from bot.config import BOT_TOKEN, CHANNEL_ID
    from database.channel_db import db
    from bot import web_api

    if not BOT_TOKEN or not CHANNEL_ID:
        print("BOT_TOKEN yoki CHANNEL_ID yo'q (.env)")
        return 1

    rows = _rows(args.pos_db, args.only_new)
    print(f"Yuboriladigan: {len(rows)} ta tovar (bo'lim bo'yicha tartiblangan)")
    if not rows:
        return 0
    if not args.all:
        rows = rows[:3]
        print("(sinov rejimi — 3 ta; hammasi uchun --all)")
    if not args.send:
        for r in rows[:20]:
            print(f"  {r['cat'] or '—':22} #{r['id']:<6} {r['name'][:40]}")
        print("\n[sinov] Hech narsa yuborilmadi. Yuborish uchun: --send")
        return 0

    bot = Bot(token=BOT_TOKEN)
    me = await bot.get_me()
    db._bot = bot
    db._bot_username = me.username

    sent = fail = 0
    cur_cat = None
    try:
        for i, r in enumerate(rows, 1):
            if r["cat"] != cur_cat:
                cur_cat = r["cat"]
                print(f"\n── {cur_cat or 'Bo‘limsiz'} ──")
            p = await db.get_product_any(r["id"])
            if not p:
                continue
            try:
                await web_api._post_product_to_channel(
                    db, p, r["id"], silent=not args.notify)
                sent += 1
                print(f"  [{i}/{len(rows)}] ✓ {r['name'][:42]}", flush=True)
            except Exception as e:
                fail += 1
                print(f"  [{i}/{len(rows)}] ✗ {r['name'][:30]} — {e}", flush=True)
            if i < len(rows):
                await asyncio.sleep(args.sleep)
    finally:
        await bot.session.close()

    print(f"\n✓ Yuborildi: {sent}   Xato: {fail}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Kanalga qayta e'lon qilish")
    ap.add_argument("--send", action="store_true", help="haqiqatan yuborish")
    ap.add_argument("--all", action="store_true", help="hammasi (aks holda 3 ta)")
    ap.add_argument("--only-new", action="store_true",
                    help="faqat hali e'lon qilinmaganlar")
    ap.add_argument("--notify", action="store_true",
                    help="obunachilarga ovozli bildirishnoma bilan yuborish")
    ap.add_argument("--sleep", type=float, default=6.0,
                    help="postlar orasidagi tanaffus (Telegram cheklovi)")
    ap.add_argument("--pos-db", default=config.POS_DB)
    args = ap.parse_args(argv)

    if not os.path.exists(args.pos_db):
        print(f"pos.db topilmadi: {args.pos_db}")
        return 1
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
