"""Ombor kuzatuvi — o'qishga tayyor hisobot, ixtiyoriy Telegram'ga yuborish.

To'rt bo'lim:
  1. Tugagan-u keltirilmayotgan — ilgari sotilardi, endi qoldiq 0. Har kuni
     yo'qotilayotgan savdo. (qoldiq tarixi kerak — 7 kun)
  2. Muzlagan tovarlar — qoldiq bor, qimirlamayapti. Qotib qolgan pul.
     (qoldiq tarixi kerak — 30 kun)
  3. Yo'q, lekin ostona belgilangan — do'kon zaxira ushlab turadigan tovar
     tugagan. TARIXSIZ ham ishlaydi, lekin qamrovi tor: ostona atigi ~1.5%
     tovarga qo'yilgan, shuning uchun bu 1-bo'limning o'rnini bosmaydi.
  4. Manfiy qoldiq — ombor hisobidagi teshik (kirim qilinmay sotilgan).

Ishlatish:
    python -m marketplace.report              # ekranga
    python -m marketplace.report --telegram   # Telegram'ga ham yuboradi
"""

import argparse
import os
import sys

import requests

from marketplace import config
from marketplace.db import MarketplaceDB
from marketplace import analytics


def _load_bot_env() -> None:
    """Telegram tokeni chinor-bot'ning o'z .env faylida turadi.

    Uni ikkinchi joyga ko'chirmaymiz — bitta maxfiy qiymat ikki faylda
    yotmagani ma'qul. Shu sabab kerak bo'lganda o'sha fayldan o'qiymiz.
    """
    if os.getenv("BOT_TOKEN"):
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for candidate in (
        os.path.join(root, "chinor-bot", ".env"),
        "/opt/chinor/chinor-bot/.env",
    ):
        if os.path.exists(candidate):
            load_dotenv(candidate, override=False)
            return


_load_bot_env()

TG_TOKEN = os.getenv("BOT_TOKEN", "").strip()
TG_CHAT = os.getenv("REPORT_CHAT_ID", os.getenv("GLAVNIY_ADMIN_ID", "")).strip()

TOP = 12


def _money(v: float) -> str:
    return f"{v:,.0f}".replace(",", " ")


def build(db: MarketplaceDB) -> str:
    days = db.snapshot_days()
    c = db.conn
    L = []

    row = c.execute(
        "SELECT MAX(day) d FROM stock_daily WHERE branch = ?", (str(config.BRANCH),)
    ).fetchone()
    day = row["d"] if row else "-"

    tot = c.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(stock*price),0) v FROM stock_daily WHERE day = ?",
        (day,),
    ).fetchone()

    L.append(f"📦 <b>Ombor kuzatuvi</b> — {day}")
    L.append(f"Tovar: {tot['n']:,}  |  Ombor qiymati: {_money(tot['v'])} so'm")
    L.append(f"Yig'ilgan tarix: {days} kun")
    L.append("")

    # ── 1. Tugagan-u keltirilmayotgan ─────────────────────────────────────
    need = config.OUT_OF_STOCK_DAYS
    L.append("━━━━━━━━━━━━━━━━━━━━")
    L.append("🔴 <b>Tugagan-u keltirilmayotgan</b>")
    if days < need:
        L.append(f"Yana {need - days} kun ma'lumot kerak "
                 f"({days}/{need}). Har kuni avtomatik yig'ilyapti.")
    else:
        rows = analytics.out_of_stock_gap(db, need)
        if not rows:
            L.append("Bunday tovar topilmadi — yaxshi.")
        else:
            lost = sum(r["lost_per_day"] for r in rows)
            L.append(f"{len(rows)} ta tovar. Taxminiy yo'qotish: "
                     f"~{_money(lost)} so'm/kun")
            L.append("")
            for r in rows[:TOP]:
                L.append(f"• {r['name'][:38]}")
                L.append(f"   {r['zero_days']} kundan beri yo'q · "
                         f"~{_money(r['lost_per_day'])} so'm/kun")
    L.append("")

    # ── 2. Muzlagan ───────────────────────────────────────────────────────
    need = config.DEAD_STOCK_DAYS
    L.append("━━━━━━━━━━━━━━━━━━━━")
    L.append("🧊 <b>Muzlagan tovarlar</b>")
    if days < need:
        L.append(f"Yana {need - days} kun ma'lumot kerak ({days}/{need}).")
    else:
        rows = analytics.dead_stock(db, need)
        if not rows:
            L.append("Bunday tovar topilmadi — yaxshi.")
        else:
            locked = sum(r["locked"] for r in rows)
            L.append(f"{len(rows)} ta tovar. Qotib qolgan pul: "
                     f"{_money(locked)} so'm")
            L.append("")
            for r in rows[:TOP]:
                L.append(f"• {r['name'][:38]}")
                L.append(f"   {r['days']} kun qimirlamagan · "
                         f"{_money(r['locked'])} so'm")
    L.append("")

    # ── 3. Ostona belgilangan-u tugagan (tarixsiz ishlaydi) ───────────────
    rows = c.execute(
        """SELECT p.name, p.sku, s.stock, s.low_stock, s.price
             FROM stock_daily s JOIN products p ON p.id = s.product_id
            WHERE s.day = ? AND s.stock <= 0 AND s.low_stock > 0
            ORDER BY s.low_stock DESC, s.price DESC""",
        (day,),
    ).fetchall()
    L.append("━━━━━━━━━━━━━━━━━━━━")
    L.append("⚠️ <b>Zaxira ushlanadigan-u tugagan</b>")
    if not rows:
        L.append("Yo'q.")
    else:
        L.append(f"{len(rows)} ta tovar (do'kon ular uchun minimal qoldiq belgilagan):")
        L.append("")
        for r in rows[:TOP]:
            L.append(f"• {r['name'][:38]}")
            L.append(f"   ostona {r['low_stock']:g} · qoldiq {r['stock']:g} · "
                     f"{_money(r['price'])} so'm")
    L.append("")

    # ── 4. Manfiy qoldiq ──────────────────────────────────────────────────
    neg = c.execute(
        """SELECT p.name, s.stock FROM stock_daily s
             JOIN products p ON p.id = s.product_id
            WHERE s.day = ? AND s.stock < 0 ORDER BY s.stock""",
        (day,),
    ).fetchall()
    L.append("━━━━━━━━━━━━━━━━━━━━")
    L.append("❗️ <b>Manfiy qoldiq</b> (ombor hisobidagi teshik)")
    if not neg:
        L.append("Yo'q — ombor toza.")
    else:
        L.append(f"{len(neg)} ta tovar. Kirim qilinmay sotilgan bo'lishi mumkin:")
        L.append("")
        for r in neg[:TOP]:
            L.append(f"• {r['name'][:38]} → {r['stock']:g}")

    return "\n".join(L)


def send_telegram(text: str) -> bool:
    if not TG_TOKEN or not TG_CHAT:
        print("BOT_TOKEN yoki REPORT_CHAT_ID/GLAVNIY_ADMIN_ID topilmadi — yuborilmadi.")
        return False
    # Telegram bitta xabarda 4096 belgi qabul qiladi.
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 3800:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur:
        chunks.append(cur)

    for part in chunks:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": part, "parse_mode": "HTML"},
            timeout=30,
        )
        if not r.ok or not r.json().get("ok"):
            print(f"Telegram xatosi: {r.text[:200]}")
            return False
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Ombor kuzatuv hisoboti")
    ap.add_argument("--telegram", action="store_true", help="Telegram'ga yuborish")
    args = ap.parse_args(argv)

    with MarketplaceDB() as db:
        text = build(db)

    # Ekranga — HTML teglarisiz
    print(text.replace("<b>", "").replace("</b>", ""))

    if args.telegram:
        print("\n— Telegram'ga yuborilmoqda…")
        print("✓ yuborildi" if send_telegram(text) else "✗ yuborilmadi")
    return 0


if __name__ == "__main__":
    sys.exit(main())
