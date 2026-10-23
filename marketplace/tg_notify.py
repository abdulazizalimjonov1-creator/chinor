"""Qarzdorlarga userbot orqali xabar — NAVBAT bilan, chegaralangan.

NEGA USERBOT. Telegram boti faqat o'ziga `/start` bosgan odamga yoza
oladi. Do'kon qarzdorlarining ko'pi botga kirmagan, lekin raqami bor —
ularga faqat SHAXSIY akkaunt yoza oladi.

NEGA EHTIYOT KERAK. Shaxsiy akkauntdan kontaktda bo'lmagan odamlarga
ketma-ket yozish — Telegram spam-qo'riqchisini uyg'otadigan asosiy
harakat. Oqibati `PEER_FLOOD`: akkaunt begonalarga umuman yoza olmay
qoladi va uni tiklash oson emas. Shu sababli bu yerda:

  • xabarlar NAVBATGA yoziladi, bittalab va tanaffus bilan yuboriladi;
  • kunlik chegara bor (`NOTIFY_DAILY_LIMIT`, sukut 20);
  • `PEER_FLOOD` kelsa navbat DARHOL to'xtaydi, qolganlari keyinga
    qoladi (xato yashirilmaydi — egasi ko'rishi kerak);
  • botda ro'yxatdan o'tgan mijozga userbot YOZMAYDI — unga bot
    yuboradi, bu butunlay xavfsiz.

SESSIYA QULFI. Telethon sessiya fayli bir vaqtda bitta jarayonda
ochiladi. Cheklar sinxroni ham o'shani ishlatadi, shuning uchun bu
modul ALOHIDA jarayon sifatida va cheklar sinxroni bilan BIR NAVBATDA
chaqiriladi (`bot/notify_worker.py` ikkalasiga bitta qulf beradi).

Ishlatish:
    python -m marketplace.tg_notify send          # navbatni yuboradi
    python -m marketplace.tg_notify status        # navbat holati
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
from typing import Optional

from marketplace import config
from marketplace.db import MarketplaceDB, now_local

SCHEMA = """
CREATE TABLE IF NOT EXISTS notify_queue (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    client     TEXT NOT NULL,
    phone      TEXT NOT NULL,
    text       TEXT NOT NULL,
    kind       TEXT DEFAULT 'debt',
    status     TEXT DEFAULT 'pending',   -- pending|sent|failed|skipped
    error      TEXT DEFAULT '',
    author     TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    sent_at    TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_nq_status ON notify_queue(status, id);
"""

# Xabarlar orasidagi tanaffus (soniya) — tasodifiy, bir xil ritm
# qo'riqchiga «robot» bo'lib ko'rinadi.
PAUSE_MIN = int(os.getenv("NOTIFY_PAUSE_MIN", "25") or 25)
PAUSE_MAX = int(os.getenv("NOTIFY_PAUSE_MAX", "70") or 70)
DAILY_LIMIT = int(os.getenv("NOTIFY_DAILY_LIMIT", "20") or 20)


def ensure_schema(db: MarketplaceDB) -> None:
    db.conn.executescript(SCHEMA)
    db.conn.commit()


def _digits(phone: str) -> str:
    return "".join(ch for ch in (phone or "") if ch.isdigit())


def enqueue(db: MarketplaceDB, client: str, phone: str, text: str,
            kind: str = "debt", author: str = "") -> int:
    """Navbatga qo'shadi. Qaytaradi: qator id (0 — qo'shilmadi).

    Shu mijozga HALI YUBORILMAGAN xabar navbatda tursa, ikkinchisi
    qo'shilmaydi — aks holda bitta odam ikki marta bezovta qilinardi.
    """
    ensure_schema(db)
    if len(_digits(phone)) < 9:
        return 0
    bor = db.conn.execute(
        "SELECT id FROM notify_queue WHERE client = ? AND status = 'pending'",
        (client,)).fetchone()
    if bor:
        return 0
    cur = db.conn.execute(
        """INSERT INTO notify_queue (client, phone, text, kind, author, created_at)
           VALUES (?,?,?,?,?,?)""",
        (client, phone, text, kind, author,
         now_local().isoformat(timespec="seconds")))
    db.conn.commit()
    return int(cur.lastrowid)


def _sent_today(db: MarketplaceDB) -> int:
    day = now_local().strftime("%Y-%m-%d")
    row = db.conn.execute(
        "SELECT COUNT(*) FROM notify_queue WHERE status='sent' AND sent_at LIKE ?",
        (day + "%",)).fetchone()
    return int(row[0] or 0)


def status(db: MarketplaceDB) -> dict:
    ensure_schema(db)
    out = {"pending": 0, "sent": 0, "failed": 0, "skipped": 0}
    for st, n in db.conn.execute(
            "SELECT status, COUNT(*) FROM notify_queue GROUP BY status"):
        out[st] = int(n)
    out["today"] = _sent_today(db)
    out["limit"] = DAILY_LIMIT
    return out


# ─── Yuborish ────────────────────────────────────────────────────────────────

def _client():
    from telethon import TelegramClient

    if not config.TG_API_ID or not config.TG_API_HASH:
        raise SystemExit("TG_API_ID / TG_API_HASH topilmadi (marketplace/.env)")
    return TelegramClient(config.TG_SESSION, int(config.TG_API_ID),
                          config.TG_API_HASH)


async def _resolve(client, phone: str, name: str):
    """Telefon bo'yicha Telegram foydalanuvchisini topadi.

    Kontaktga qo'shish orqali: Telegram begona raqamga shunchaki yozishga
    ruxsat bermaydi. Kontakt HAQIQIY nom bilan qo'shiladi — telefon
    kitobida «Mijoz» deb turgan o'nlab yozuv keyin chalkashtiradi.
    """
    from telethon.tl.functions.contacts import ImportContactsRequest
    from telethon.tl.types import InputPhoneContact

    d = _digits(phone)
    if len(d) == 9:
        d = "998" + d
    res = await client(ImportContactsRequest(
        [InputPhoneContact(client_id=0, phone="+" + d,
                           first_name=(name or "Mijoz")[:60], last_name="")]))
    return res.users[0] if res.users else None


async def cmd_send(args) -> int:
    ensure = True
    sent = failed = skipped = 0
    with MarketplaceDB() as db:
        ensure_schema(db)
        bugun = _sent_today(db)
        qoldi = max(0, DAILY_LIMIT - bugun)
        if qoldi <= 0:
            print(f"Kunlik chegara to'ldi ({bugun}/{DAILY_LIMIT}) — to'xtadi")
            return 0
        limit = min(qoldi, int(args.limit or qoldi))
        rows = db.conn.execute(
            "SELECT * FROM notify_queue WHERE status='pending' ORDER BY id LIMIT ?",
            (limit,)).fetchall()
        if not rows:
            print("Navbat bo'sh")
            return 0
        print(f"Navbatda {len(rows)} ta (bugun yuborilgan: {bugun}/{DAILY_LIMIT})")

        client = _client()
        await client.connect()
        if not await client.is_user_authorized():
            print("XATO: userbot kirmagan — python -m marketplace.tg_receipts login")
            return 1

        for i, r in enumerate(rows):
            if args.dry:
                print(f"  [sinov] {r['client']} ({r['phone']})")
                continue
            try:
                user = await _resolve(client, r["phone"], r["client"])
                if user is None:
                    db.conn.execute(
                        "UPDATE notify_queue SET status='skipped', error=? WHERE id=?",
                        ("raqam Telegramda topilmadi", r["id"]))
                    skipped += 1
                    print(f"  ? {r['client']}: Telegramda yo'q")
                else:
                    await client.send_message(user, r["text"])
                    db.conn.execute(
                        "UPDATE notify_queue SET status='sent', sent_at=? WHERE id=?",
                        (now_local().isoformat(timespec="seconds"), r["id"]))
                    sent += 1
                    print(f"  + {r['client']} ({r['phone']})")
                db.conn.commit()
            except Exception as e:
                msg = str(e)
                db.conn.execute(
                    "UPDATE notify_queue SET status='failed', error=? WHERE id=?",
                    (msg[:300], r["id"]))
                db.conn.commit()
                failed += 1
                print(f"  ! {r['client']}: {msg[:120]}")
                # PEER_FLOOD — akkaunt cheklandi. Qolganini YUBORMAYMIZ:
                # davom etsak cheklov uzayadi va akkaunt butunlay
                # yoziolmay qolishi mumkin.
                if "PEER_FLOOD" in msg.upper() or "FLOOD" in msg.upper():
                    print("  !! Telegram cheklovi — navbat to'xtatildi")
                    break

            if i < len(rows) - 1:
                await asyncio.sleep(random.randint(PAUSE_MIN, PAUSE_MAX))

        await client.disconnect()

    print(f"\nYuborildi: {sent} | o'tkazildi: {skipped} | xato: {failed}")
    return 0


async def cmd_status(args) -> int:
    with MarketplaceDB() as db:
        st = status(db)
    print(" | ".join(f"{k}: {v}" for k, v in st.items()))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Qarzdorlarga userbot xabari")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("send", help="navbatni yuboradi")
    s.add_argument("--limit", type=int, default=0)
    s.add_argument("--dry", action="store_true", help="yubormasdan ko'rsatadi")
    s.set_defaults(fn=cmd_send)
    t = sub.add_parser("status", help="navbat holati")
    t.set_defaults(fn=cmd_status)
    args = ap.parse_args(argv)
    return asyncio.run(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
