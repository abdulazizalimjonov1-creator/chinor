"""Tovarlarni kanalga SEKIN-ASTA chiqarish.

NEGA KERAK (2026-10-25, do'kon egasi aytdi). EPA katalogidan ~100 ta
tovarga o'z surati, xususiyatlari va «nima uchun kerak» izohi qo'shildi.
Hammasini birdan yuborsa kanal bir kunda 100 ta post bilan to'lib ketadi,
obunachilarning telefoni tinmay chirillaydi va odam obunani tashlaydi.
Shuning uchun kuniga bir nechtadan, belgilangan soatlarda chiqadi.

FAQAT O'Z SURATI BOR TOVAR CHIQADI. Bazada 2000 dan ortiq tovar
`type_<tur>.jpg` umumiy rasmga ulangan va ular aslida BOSHQA BRENDNING
suratlari (Panasonic dreli, Klein Tools ruletkasi) — bunaqasi kanalga
chiqsa mijoz boshqa narsa kutib keladi. Shuning uchun navbat faqat
`prod_<id>_...` fayllari bo'yicha yig'iladi.

Sozlamalar (`settings` jadvali, Mini App'dan o'zgartirsa bo'ladi):
    drip_enabled — "1"/"0" (sukut "1")
    drip_hours   — soatlar, vergul bilan (sukut "10,13,16,19")
    drip_filter  — nom bo'yicha cheklov (sukut "" — hammasi;
                   masalan "epa" bo'lsa faqat EPA tovarlari chiqadi)

Tartib: omborda ko'p qolgani oldin — reklama ishlasa, aynan o'sha
tovarning sotuvi boshlanadi.
"""

from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

CHECK_EVERY = 600          # har 10 daqiqada soat tekshiriladi
DEFAULT_HOURS = "10,13,16,19"


def _on(db) -> bool:
    return str(db.get_setting("drip_enabled", "1")).strip() in ("1", "true", "on")


def _hours(db) -> list:
    raw = db.get_setting("drip_hours", DEFAULT_HOURS) or DEFAULT_HOURS
    out = []
    for h in raw.split(","):
        h = h.strip()
        if h.isdigit() and 0 <= int(h) <= 23:
            out.append(int(h))
    return sorted(set(out)) or [10]


def _next_product(db) -> dict:
    """Navbatdagi tovar: o'z surati bor, omborda bor, kanalda hali yo'q."""
    import sqlite3

    from database._helpers import DB_PATH

    flt = (db.get_setting("drip_filter", "") or "").strip().lower()
    like = f"%{flt}%" if flt else ""
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        sql = ("SELECT id FROM products "
               "WHERE is_active=1 AND IFNULL(qty,0) > 0 "
               "  AND IFNULL(image_url,'') LIKE '%/prod_%' "
               "  AND IFNULL(channel_msg_id,0) = 0 ")
        args = []
        if like:
            sql += " AND LOWER(name) LIKE ? "
            args.append(like)
        sql += " ORDER BY qty DESC, id LIMIT 1"
        row = conn.execute(sql, args).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def _left(db) -> int:
    import sqlite3

    from database._helpers import DB_PATH

    conn = sqlite3.connect(DB_PATH, timeout=20)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM products WHERE is_active=1 AND IFNULL(qty,0) > 0 "
            "AND IFNULL(image_url,'') LIKE '%/prod_%' "
            "AND IFNULL(channel_msg_id,0) = 0").fetchone()[0]
    finally:
        conn.close()


async def post_one(db) -> dict:
    """Bitta tovarni kanalga chiqaradi. Natija: {"ok", "id", "name"} yoki xato."""
    row = _next_product(db)
    if not row:
        return {"ok": False, "error": "navbat bo'sh"}
    pid = int(row["id"])
    p = await db.get_product_any(pid)
    if not p:
        return {"ok": False, "error": f"#{pid} topilmadi"}
    from bot.web_api import _post_product_to_channel
    await _post_product_to_channel(db, p, pid)
    # Post muvaffaqiyatli bo'lsa `channel_msg_id` yoziladi — shu bo'yicha
    # tekshiramiz, aks holda xato jim ketib, navbat qotib qoladi.
    again = await db.get_product_any(pid)
    ok = bool((again or {}).get("channel_msg_id"))
    return {"ok": ok, "id": pid, "name": (p.get("display_name") or p.get("name", ""))[:40],
            "error": "" if ok else "kanalga chiqmadi"}


async def loop():
    """Belgilangan soatlarda bittadan tovar chiqaradi."""
    from database.channel_db import db

    await asyncio.sleep(120)
    while True:
        try:
            if _on(db) and db.is_channel_enabled():
                now = time.localtime()
                today = time.strftime("%Y-%m-%d", now)
                for h in _hours(db):
                    key = "drip_day_%d" % h
                    # Faqat O'SHA soat (va keyingi bir soat) ichida — bot
                    # kechqurun qayta ishga tushsa, kun davomidagi o'tib
                    # ketgan soatlarni birdan quvib yetmasin.
                    if h <= now.tm_hour < h + 2 and db.get_setting(key, "") != today:
                        db.set_setting(key, today)
                        res = await post_one(db)
                        if res.get("ok"):
                            logger.info("📢 Sekin-asta (%s:00): #%s %s | navbatda %s ta",
                                        h, res["id"], res["name"], _left(db))
                        else:
                            logger.info("📢 Sekin-asta (%s:00) chiqmadi: %s",
                                        h, res.get("error"))
                        break          # bir siklda bittadan ortiq emas
        except Exception:
            logger.exception("📢 Sekin-asta chiqarish xatosi")
        await asyncio.sleep(CHECK_EVERY)
