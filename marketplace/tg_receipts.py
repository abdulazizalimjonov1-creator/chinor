"""YesPOS botidan kelayotgan cheklarni o'qish — Telegram userbot.

NEGA USERBOT: Telegram'da bot boshqa botning xabarini KO'RA OLMAYDI. YesPOS
cheklarni do'kon egasining shaxsiy Telegramiga bot orqali yuboradi, shuning
uchun Chinor boti ularni o'qiy olmaydi. Userbot esa oddiy foydalanuvchi
akkaunti nomidan ishlaydi va o'sha xabarlarni ko'radi.

NEGA UMUMAN KERAK: YesPOS marketplace gateway'i savdo TARIXINI bermaydi —
faqat hozirgi qoldiqni. Shu sababli hozircha ketish tezligi qoldiqning
kamayishidan TAXMIN qilinadi (`velocity.py`). Chek xabarlari esa aniq savdo:
qaysi tovar, nechta, qachon. Ular yig'ilgach tahlil taxminiy bo'lmay qoladi.

MAXFIYLIK VA XAVFSIZLIK — ataylab tor doirada:
  • Sessiya fayli akkauntga TO'LIQ kalit. U `DATA_DIR` da, 0600 huquq bilan
    saqlanadi, git'ga tushmaydi va zaxira nusxalarga qo'shilmaydi.
  • Kod FAQAT YesPOS botining chatini o'qiydi (`YESPOS_BOT`). Boshqa
    suhbatlar umuman ochilmaydi — `dialogs` buyrug'i ham faqat BOT chatlarini
    ro'yxatlaydi, shaxsiy yozishmalarni emas.
  • Hech narsa yozilmaydi/yuborilmaydi: userbot faqat O'QIYDI.

Ishlatish (tartib bilan):
    python -m marketplace.tg_receipts login     # BIR MARTA, o'zingiz kiritasiz
    python -m marketplace.tg_receipts dialogs   # YesPOS botini topish
    python -m marketplace.tg_receipts peek --limit 20   # xabar ko'rinishini ko'rish
    python -m marketplace.tg_receipts sync --days 90    # cheklarni bazaga yozish

`.env` (repo ildizida yoki marketplace/.env):
    TG_API_ID=...        # my.telegram.org → API development tools
    TG_API_HASH=...
    YESPOS_BOT=@...      # chek yuboradigan botning useri (dialogs ko'rsatadi)
"""

import argparse
import asyncio
import os
import sys

from marketplace import config


def _client():
    """Telethon klientini tayyorlaydi (hali ulanmagan holda)."""
    try:
        from telethon import TelegramClient
    except ImportError:
        raise SystemExit(
            "Telethon o'rnatilmagan. O'rnatish:\n"
            "    pip install -r marketplace/requirements.txt"
        )

    if not config.TG_API_ID or not config.TG_API_HASH:
        raise SystemExit(
            "TG_API_ID / TG_API_HASH topilmadi. Ularni `.env` ga yozing "
            "(my.telegram.org → API development tools)."
        )
    return TelegramClient(
        config.TG_SESSION, int(config.TG_API_ID), config.TG_API_HASH
    )


def _protect_session() -> None:
    """Sessiya fayliga faqat egasi kira oladigan huquq qo'yadi."""
    for suffix in ("", ".session"):
        path = config.TG_SESSION + suffix
        if os.path.exists(path):
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass


# ─── Buyruqlar ───────────────────────────────────────────────────────────────

async def cmd_login(args) -> int:
    """Bir martalik kirish. Kodni FOYDALANUVCHI kiritadi — kod hech qayerga
    yozilmaydi, faqat Telegram'ga uzatiladi."""
    client = _client()
    if args.phone:
        # Raqam oldindan berilgan — foydalanuvchiga faqat KOD terish qoladi.
        print(f"Telegram'ga kirish: {args.phone}")
        print("Telegram ilovangizga kod keladi — o'shani kiriting.\n")
    else:
        print("Telegram'ga kirish. Telefon raqamingizni xalqaro ko'rinishda kiriting")
        print("(masalan +998778775533). Telegram kod yuboradi — kodni kiriting.\n")
    # Kod va (bo'lsa) ikki bosqichli parol INTERAKTIV so'raladi va hech qayerga
    # saqlanmaydi — ular to'g'ridan-to'g'ri Telegram'ga ketadi.
    await client.start(phone=args.phone or None)
    me = await client.get_me()
    _protect_session()
    print(f"\n✅ Kirildi: {me.first_name} (@{me.username or '—'}, id {me.id})")
    print(f"Sessiya: {config.TG_SESSION}.session  (huquq 600 — faqat siz)")
    print("Endi: python -m marketplace.tg_receipts dialogs")
    await client.disconnect()
    return 0


async def cmd_dialogs(args) -> int:
    """FAQAT bot chatlarini ro'yxatlaydi — YesPOS botini topish uchun.

    Shaxsiy yozishmalar va guruhlar ataylab ko'rsatilmaydi.
    """
    client = _client()
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit("Avval kiring: python -m marketplace.tg_receipts login")

    print(f"{'Bot':<28} {'ID':<14} Oxirgi xabar")
    print("-" * 78)
    async for d in client.iter_dialogs():
        ent = d.entity
        if not getattr(ent, "bot", False):
            continue          # faqat botlar
        last = (d.message.message or "").replace("\n", " ")[:36] if d.message else ""
        print(f"@{(ent.username or '—'):<27} {ent.id:<14} {last}")
    await client.disconnect()
    print("\nChek yuboradigan botni topib, `.env` ga yozing: YESPOS_BOT=@nomi")
    return 0


async def _resolve_bot(client):
    if not config.YESPOS_BOT:
        raise SystemExit(
            "YESPOS_BOT belgilanmagan. Avval: "
            "python -m marketplace.tg_receipts dialogs"
        )
    return await client.get_entity(config.YESPOS_BOT)


async def cmd_peek(args) -> int:
    """Oxirgi xabarlarni XOM holda ko'rsatadi — ajratgichni yozish uchun.

    Hech narsa saqlanmaydi. Maqsad: chek qanday ko'rinishda kelishini bilish.
    """
    client = _client()
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit("Avval kiring: python -m marketplace.tg_receipts login")

    bot = await _resolve_bot(client)
    n = 0
    async for msg in client.iter_messages(bot, limit=args.limit):
        text = msg.message or ""
        if not text.strip():
            continue
        n += 1
        print(f"\n───── #{msg.id}  {msg.date:%Y-%m-%d %H:%M} ─────")
        print(text if args.full else text[:800])
    await client.disconnect()
    print(f"\nJami {n} ta matnli xabar ko'rsatildi.")
    return 0


async def cmd_sync(args) -> int:
    """Cheklarni o'qib bazaga yozadi.

    Xabar id'si birlamchi kalit, shuning uchun qayta ishga tushirish xavfsiz —
    bor chek ikkinchi marta yozilmaydi. Standart holatda faqat OXIRGI yozilgan
    chekdan keyingilari olinadi (tez); `--days` yoki `--all` bilan orqaga
    qarab to'ldiriladi.
    """
    from datetime import datetime, timedelta, timezone

    from marketplace import receipts as R
    from marketplace.db import MarketplaceDB

    client = _client()
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit("Avval kiring: python -m marketplace.tg_receipts login")
    bot = await _resolve_bot(client)

    with MarketplaceDB() as db:
        R.ensure_schema(db)
        index = R.catalog_index(db)
        have = {
            int(r[0]) for r in db.conn.execute("SELECT msg_id FROM receipts")
        }

        cutoff = None
        min_id = 0
        if args.all:
            pass                                  # butun tarix
        elif args.days:
            cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
        elif have and not args.reparse:
            min_id = max(have)                    # faqat yangilari
            print(f"Oxirgi yozilgan xabar: {min_id} — undan keyingilari olinadi.")

        stat = {"chek": 0, "qaytarish": 0, "nasiya": 0,
                "tovarsiz": 0, "tanilmadi": 0, "bog'lanmadi": 0, "bor edi": 0}
        seen = 0
        async for msg in client.iter_messages(bot, min_id=min_id):
            text = msg.message or ""
            if not text.strip():
                continue
            if cutoff and msg.date < cutoff:
                break
            seen += 1
            if msg.id in have and not args.reparse:
                stat["bor edi"] += 1
                continue

            r = R.parse(text)
            if r is None:
                # Chek emas (ОПЛАТА) yoki tanib bo'lmadi — ajratib sanaymiz.
                head = text.strip().splitlines()[0][:30]
                if head.startswith("ОПЛАТА"):
                    stat["tovarsiz"] += 1
                else:
                    stat["tanilmadi"] += 1
                    if args.verbose:
                        print(f"  ⚠ tanilmadi #{msg.id}: {head}")
                continue

            stat["bog'lanmadi"] += R.store(db, msg.id, r, index)
            stat[{"sale": "chek", "return": "qaytarish",
                  "sale_deferred": "nasiya"}[r.kind]] += 1
            if (stat["chek"] + stat["qaytarish"] + stat["nasiya"]) % 300 == 0:
                db.conn.commit()
                print(f"  … {seen} xabar ko'rildi, {stat['chek']} chek yozildi")

        db.conn.commit()
        rng = db.conn.execute(
            "SELECT MIN(day), MAX(day), COUNT(*) FROM receipts"
        ).fetchone()

    await client.disconnect()
    print("\nNatija:")
    for k, v in stat.items():
        if v:
            print(f"  {k:<12} {v}")
    if rng and rng[2]:
        print(f"\nBazada: {rng[2]} chek, {rng[0]} … {rng[1]}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="YesPOS cheklarini Telegram'dan o'qish")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("login", help="bir martalik kirish")
    p.add_argument("--phone", help="telefon (+998...) — faqat so'ramaslik uchun")
    p.set_defaults(fn=cmd_login)
    sub.add_parser("dialogs", help="bot chatlari ro'yxati").set_defaults(fn=cmd_dialogs)

    p = sub.add_parser("peek", help="xabarlarni xom holda ko'rish")
    p.add_argument("--limit", type=int, default=15)
    p.add_argument("--full", action="store_true", help="matnni qisqartirmaslik")
    p.set_defaults(fn=cmd_peek)

    p = sub.add_parser("sync", help="cheklarni bazaga yozish")
    p.add_argument("--days", type=int, help="shuncha kunlik tarixni to'ldirish")
    p.add_argument("--all", action="store_true", help="butun tarix (birinchi marta)")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="tanilmagan xabarlarni ko'rsatish")
    p.add_argument("--reparse", action="store_true",
                   help="bor cheklarni QAYTA o'qish (ajratgich yangilanganda)")
    p.set_defaults(fn=cmd_sync)

    args = ap.parse_args(argv)
    return asyncio.run(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
