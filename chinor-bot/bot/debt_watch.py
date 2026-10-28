"""Qarz harakati bo'lganda darhol xabar — mijozga ham, adminga ham.

Kassada ikki narsa qarzga tegadi:
  • mijoz nasiyaga tovar oldi  → qarzi OSHDI;
  • mijoz kassada pul to'ladi  → qarzi KAMAYDI.

Eski tizimda (kassadagi ChekHisobot) ikkalasi ham xabar qilinardi:
mijoz «to'lovim yetib bordimi?» deb qo'ng'iroq qilmasin, egasi esa
kassada nima bo'layotganini ko'rib tursin. Shu ish shu yerga ko'chirildi.

MANBA — cheklar bazasi (`marketplace.db`): yangi `receipts` (qarzga
yozilgani) va yangi `debt_payments` («ОПЛАТА»). Ular userbot orqali
har 30 daqiqada yangilanadi, shuning uchun xabar ham o'shancha
kechikadi — bu normal, chunki chekning o'zi shu yo'ldan keladi.

BIRINCHI ISHGA TUSHISH: eski yozuvlar bo'yicha xabar YUBORILMAYDI.
Aks holda tizim yoqilgan zahoti o'n minglab eski chek bo'yicha
hammaga xabar ketardi. Birinchi marta faqat «hozirgi joy» eslab
qolinadi, xabar keyingilaridan boshlanadi.
"""

import asyncio
import logging
import os
import sys

logger = logging.getLogger(__name__)

INTERVAL = int(os.getenv("DEBT_WATCH_MINUTES", "7")) * 60
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Bir siklda ko'pi bilan shuncha xabar — kassada ko'p chek bo'lsa ham
# bot Telegram cheklovига urilmasin.
MAX_PER_RUN = int(os.getenv("DEBT_WATCH_MAX", "25") or 25)

_STATE = """
CREATE TABLE IF NOT EXISTS notify_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _mk():
    if _REPO not in sys.path:
        sys.path.insert(0, _REPO)
    from marketplace import debt as D
    from marketplace.db import MarketplaceDB

    return D, MarketplaceDB


def _pul(v) -> str:
    return f"{round(float(v or 0)):,}".replace(",", " ")


def _get(conn, key: str) -> int:
    conn.executescript(_STATE)
    row = conn.execute("SELECT value FROM notify_state WHERE key = ?",
                       (key,)).fetchone()
    try:
        return int(row[0]) if row else -1
    except (TypeError, ValueError):
        return -1


def _set(conn, key: str, value: int) -> None:
    conn.execute(
        "INSERT INTO notify_state (key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)))


def collect() -> list:
    """Yangi harakatlar ro'yxati. Har biri: (tur, mijoz, summa, qoldiq, izoh).

    Belgini shu yerda siljitamiz: xabar yuborilmay qolsa ham bir xil
    yozuv ikkinchi marta aylanib kelmasligi kerak — mijozga takror
    xabar yuborgandan ko'ra bittasini o'tkazib yuborgan yaxshi.
    """
    D, MarketplaceDB = _mk()
    out = []
    with MarketplaceDB() as mdb:
        c = mdb.conn
        D.ensure_schema(c)
        oxirgi_chek = _get(c, "watch_receipt")
        oxirgi_tolov = _get(c, "watch_payment")

        # Birinchi ishga tushish — faqat belgini qo'yamiz.
        if oxirgi_chek < 0 or oxirgi_tolov < 0:
            r = c.execute("SELECT COALESCE(MAX(msg_id),0) FROM receipts").fetchone()[0]
            p = c.execute("SELECT COALESCE(MAX(msg_id),0) FROM debt_payments").fetchone()[0]
            _set(c, "watch_receipt", r)
            _set(c, "watch_payment", p)
            c.commit()
            logger.info("💳 Qarz kuzatuvi boshlandi (chek=%s, to'lov=%s)", r, p)
            return []

        yangi_chek = oxirgi_chek
        for r in c.execute(
                """SELECT msg_id, client, sold_at, payable, paid_debt
                     FROM receipts
                    WHERE msg_id > ? AND paid_debt > 0 AND client <> ''
                    ORDER BY msg_id LIMIT ?""", (oxirgi_chek, MAX_PER_RUN)):
            out.append(("qarz", r["client"], float(r["paid_debt"] or 0),
                        None, r["sold_at"][:16]))
            yangi_chek = max(yangi_chek, int(r["msg_id"]))

        yangi_tolov = oxirgi_tolov
        for r in c.execute(
                """SELECT msg_id, client, paid_at, amount, balance_after, kinds
                     FROM debt_payments
                    WHERE msg_id > ? AND client <> ''
                    ORDER BY msg_id LIMIT ?""", (oxirgi_tolov, MAX_PER_RUN)):
            out.append(("tolov", r["client"], float(r["amount"] or 0),
                        r["balance_after"], (r["kinds"] or "").strip()))
            yangi_tolov = max(yangi_tolov, int(r["msg_id"]))

        # Joriy qarz va mijozning chat'i — xabar matni uchun.
        for i, (tur, nom, summa, qoldiq, izoh) in enumerate(out):
            row = c.execute(
                "SELECT chat_id, remind_open FROM debt_clients WHERE name = ?",
                (nom,)).fetchone()
            out[i] = {
                "tur": tur, "mijoz": nom, "summa": summa,
                "qoldiq": qoldiq if qoldiq is not None else D.debt(c, nom),
                "izoh": izoh,
                "chat_id": (row["chat_id"] if row else "") or "",
                "ochiq": (int(row["remind_open"]) if row else 1) == 1,
            }

        _set(c, "watch_receipt", yangi_chek)
        _set(c, "watch_payment", yangi_tolov)
        c.commit()
    return out


def _mijoz_matni(e: dict) -> str:
    if e["tur"] == "tolov":
        return (f"✅ <b>To'lovingiz qabul qilindi</b>\n\n"
                f"💵 Summa: <b>{_pul(e['summa'])}</b> so'm"
                + (f"  ({e['izoh']})" if e["izoh"] else "") + "\n"
                + (f"📕 Qoldiq qarz: <b>{_pul(e['qoldiq'])}</b> so'm"
                   if e["qoldiq"] > 0 else "🟢 <b>Qarzingiz yopildi</b>")
                + "\n\nRahmat! 🌳 Chinor")
    return (f"🤝 <b>Yangi xarid qarzga yozildi</b>\n\n"
            f"🧾 Summa: <b>{_pul(e['summa'])}</b> so'm\n"
            f"📕 Jami qarz: <b>{_pul(e['qoldiq'])}</b> so'm\n\n"
            f"Batafsil — «🛒 Do'kon va hisobim» tugmasi yoki /hisobim")


def _admin_matni(e: dict) -> str:
    if e["tur"] == "tolov":
        return (f"💵 <b>To'lov</b> — {e['mijoz']}\n"
                f"Summa: <b>{_pul(e['summa'])}</b>"
                + (f"  ({e['izoh']})" if e["izoh"] else "") + "\n"
                f"Qoldiq: <b>{_pul(e['qoldiq'])}</b>")
    return (f"🤝 <b>Nasiya</b> — {e['mijoz']}\n"
            f"Summa: <b>{_pul(e['summa'])}</b>\n"
            f"Jami qarz: <b>{_pul(e['qoldiq'])}</b>")


async def run_once(bot) -> int:
    """Yangi harakatlarni xabar qiladi. Qaytaradi: yuborilgan xabar soni."""
    from database.channel_db import db

    try:
        events = await asyncio.get_running_loop().run_in_executor(None, collect)
    except Exception as e:
        logger.warning("💳 Qarz kuzatuvi o'qilmadi: %s", e)
        return 0
    if not events:
        return 0

    # Adminlar ro'yxati — bir marta.
    adminlar = set()
    try:
        from bot.config import GLAVNIY_ADMIN_ID

        adminlar.add(int(GLAVNIY_ADMIN_ID))
        for a in (await db.get_all_admins() or []):
            try:
                adminlar.add(int(a.get("telegram_id") or 0))
            except (TypeError, ValueError):
                pass
    except Exception:
        pass
    adminlar.discard(0)

    yuborildi = 0
    for e in events:
        # Mijozga — faqat botda bo'lsa va xabarni o'chirmagan bo'lsa.
        # YUBORISH `tg_out` ORQALI: mijozlarning ko'pi @cheklars_bot ga
        # /start bosgan, asosiy bot ularga yoza olmaydi (403) va xabar
        # JIM yo'qolardi. `tg_out` ikkala tokenni ham sinaydi.
        if e["chat_id"] and e["ochiq"]:
            from bot import tg_out

            if await tg_out.send_text(e["chat_id"], _mijoz_matni(e)):
                yuborildi += 1
            else:
                logger.info("💳 Mijozga yetib bormadi: %s", e["mijoz"])
        for chat in adminlar:
            try:
                await bot.send_message(chat, _admin_matni(e), parse_mode="HTML")
                yuborildi += 1
            except Exception:
                pass
        await asyncio.sleep(0.6)      # Telegram tezlik cheklovi
    logger.info("💳 Qarz harakati: %s hodisa, %s xabar", len(events), yuborildi)
    return yuborildi


async def loop(bot):
    if not _flag_on():
        logger.info("💳 Qarz xabarlari o'chiq (sozlama: debt_notify)")
    await asyncio.sleep(240)
    while True:
        try:
            if _flag_on():
                await run_once(bot)
        except Exception:
            logger.exception("💳 Qarz kuzatuvi")
        await asyncio.sleep(INTERVAL)


def _flag_on() -> bool:
    """Sozlama orqali o'chirish mumkin: `debt_notify=0`."""
    try:
        from database.channel_db import db

        return str(db.get_setting("debt_notify", "1")).strip() not in ("0", "", "yo'q")
    except Exception:
        return True
