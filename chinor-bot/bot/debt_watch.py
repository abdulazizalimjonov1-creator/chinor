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
import html
import logging
import os
import sys

from bot.config import CONTACT_PHONE_1

logger = logging.getLogger(__name__)

INTERVAL = int(os.getenv("DEBT_WATCH_MINUTES", "7")) * 60
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Bir siklda ko'pi bilan shuncha xabar — kassada ko'p chek bo'lsa ham
# bot Telegram cheklovига urilmasin.
MAX_PER_RUN = int(os.getenv("DEBT_WATCH_MAX", "25") or 25)

# Xabarda ko'rsatiladigan tovar qatorlari (Telegram chegarasi 4096 belgi).
MAX_ITEMS = int(os.getenv("DEBT_WATCH_ITEMS", "20") or 20)

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


async def _ish(fn, *a):
    """Bazaga yozadigan ishni alohida ipda bajaramiz — bot qotmasin."""
    return await asyncio.get_running_loop().run_in_executor(None, fn, *a)


def _tovarlar(conn, msg_id: int) -> list:
    """Chekdagi tovarlar ro'yxati.

    NEGA KERAK (2026-10-28, mijoz shikoyati: «nima mahsulot olingani
    kelmayapti»): xabarda faqat summa bor edi — odam nima olganini
    bilmay, do'konga qo'ng'iroq qilardi. Nomlar `receipt_items` da bor,
    shuning uchun ularni ham qo'shamiz.
    """
    try:
        rows = conn.execute(
            "SELECT name, qty, price, amount FROM receipt_items "
            "WHERE msg_id = ? ORDER BY line_no", (int(msg_id),)).fetchall()
    except Exception as e:
        logger.info("💳 Chek tarkibi o'qilmadi (%s): %s", msg_id, e)
        return []
    return [{"name": r["name"], "qty": r["qty"], "price": r["price"],
             "amount": r["amount"]} for r in rows]


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
        # `debt_notify_all=1` bo'lsa — mijoz nomiga yozilgan HAR QANDAY chek
        # (naqd ham) mijozga ketadi; sukut bo'yicha faqat qarzga olingani.
        faqat_qarz = "AND paid_debt > 0 " if not _hammasi() else ""
        for r in c.execute(
                f"""SELECT msg_id, client, sold_at, payable, paid_debt, doc_no
                     FROM receipts
                    WHERE msg_id > ? {faqat_qarz}AND client <> ''
                    ORDER BY msg_id LIMIT ?""", (oxirgi_chek, MAX_PER_RUN)):
            out.append({"tur": "qarz", "mijoz": r["client"],
                        "summa": float(r["paid_debt"] or 0), "qoldiq": None,
                        "izoh": (r["sold_at"] or "")[:16],
                        "msg_id": int(r["msg_id"]),
                        "jami": float(r["payable"] or 0),
                        "doc_no": (r["doc_no"] or "")})
            yangi_chek = max(yangi_chek, int(r["msg_id"]))

        yangi_tolov = oxirgi_tolov
        for r in c.execute(
                """SELECT msg_id, client, paid_at, amount, balance_after, kinds
                     FROM debt_payments
                    WHERE msg_id > ? AND client <> ''
                    ORDER BY msg_id LIMIT ?""", (oxirgi_tolov, MAX_PER_RUN)):
            out.append({"tur": "tolov", "mijoz": r["client"],
                        "summa": float(r["amount"] or 0),
                        "qoldiq": r["balance_after"],
                        "izoh": (r["kinds"] or "").strip(),
                        "msg_id": int(r["msg_id"]), "jami": 0.0, "doc_no": ""})
            yangi_tolov = max(yangi_tolov, int(r["msg_id"]))

        # Joriy qarz, mijozning chat'i va chek tarkibi — xabar matni uchun.
        for e in out:
            row = c.execute(
                "SELECT chat_id, remind_open, phone, phone2 FROM debt_clients "
                "WHERE name = ?", (e["mijoz"],)).fetchone()
            if e["qoldiq"] is None:
                e["qoldiq"] = D.debt(c, e["mijoz"])
            e["chat_id"] = (row["chat_id"] if row else "") or ""
            e["ochiq"] = (int(row["remind_open"]) if row else 1) == 1
            # Botda bo'lmagan mijozga shaxsiy akkauntdan yozish uchun.
            e["telefon"] = (((row["phone"] if row else "") or "")
                            or ((row["phone2"] if row else "") or ""))
            e["tovarlar"] = (_tovarlar(c, e["msg_id"])
                             if e["tur"] == "qarz" else [])

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
    bosh = ("🤝 <b>Yangi xarid qarzga yozildi</b>" if float(e.get("summa") or 0) > 0
            else "🧾 <b>Xaridingiz uchun chek</b>")
    if e.get("doc_no"):
        bosh += f"  №{html.escape(str(e['doc_no']))}"
    satrlar = [bosh]
    if e.get("izoh"):
        satrlar.append(f"🗓 {html.escape(str(e['izoh']))}")

    tovarlar = e.get("tovarlar") or []
    if tovarlar:
        satrlar.append("")
        for i, it in enumerate(tovarlar[:MAX_ITEMS], 1):
            nom = html.escape(str(it.get("name") or ""))[:60]
            satrlar.append(f"{i}) {nom}\n"
                           f"     {float(it.get('qty') or 0):g} × "
                           f"{_pul(it.get('price'))} = {_pul(it.get('amount'))}")
        if len(tovarlar) > MAX_ITEMS:
            satrlar.append(f"… va yana {len(tovarlar) - MAX_ITEMS} ta tovar")

    satrlar.append("")
    jami = float(e.get("jami") or 0)
    # Aralash to'lovda chekning bir qismi naqd ketgan bo'lishi mumkin —
    # shunda ikkala raqam ham ko'rsatiladi, aks holda bittasi yetarli.
    if jami > 0 and abs(jami - float(e["summa"])) > 1:
        satrlar.append(f"💵 Chek jami: <b>{_pul(jami)}</b> so'm")
    if float(e.get("summa") or 0) > 0:
        satrlar.append(f"🤝 Qarzga yozildi: <b>{_pul(e['summa'])}</b> so'm")
        satrlar.append(f"📕 Jami qarz: <b>{_pul(e['qoldiq'])}</b> so'm")
    elif jami > 0:
        satrlar.append(f"💵 Jami: <b>{_pul(jami)}</b> so'm")
    satrlar += ["", "Batafsil — «🛒 Do'kon va hisobim» tugmasi yoki /hisobim"]
    matn = "\n".join(satrlar)
    return matn if len(matn) <= 4000 else matn[:3900] + "\n…"


def _userbot_matni(e: dict, bot_nomi: str = "") -> str:
    """Botda BO'LMAGAN mijozga shaxsiy akkauntdan yoziladigan matn.

    Bot HTML ishlatadi, userbot esa oddiy matn — Telethon teglarni
    ko'rsatib qo'yadi, shuning uchun bu matn alohida yoziladi.

    Oxirida bot YUMSHOQ taklif qilinadi: ko'p odam qarzini telefon
    qilib so'raydi, botda esa o'zi ko'ra oladi.
    """
    qoldiq = float(e.get("qoldiq") or 0)
    usul = (e.get("izoh") or "").strip()
    satrlar = ["Assalomu alaykum! Chinor do'konidan xabar.", "",
               f"✅ To'lovingiz qabul qilindi: {_pul(e['summa'])} so'm"
               + (f" ({usul})" if usul else "")]
    if qoldiq > 0:
        satrlar.append(f"📕 Qoldiq qarzingiz: {_pul(qoldiq)} so'm")
    else:
        satrlar.append("🟢 Qarzingiz to'liq yopildi. Rahmat!")
    satrlar.append("")
    if bot_nomi:
        satrlar.append(
            f"Xohlasangiz, xaridlaringiz va qarz daftaringizni botimizdan "
            f"o'zingiz ko'rib turishingiz mumkin: t.me/{bot_nomi} — "
            f"«Start» bosib raqamingizni tasdiqlasangiz bo'ldi.")
    else:
        satrlar.append("Xohlasangiz, xaridlaringiz va qarzingizni "
                       "botimizdan o'zingiz ko'rib turishingiz mumkin.")
    satrlar += ["", f"Savol bo'lsa: {CONTACT_PHONE_1}"]
    return "\n".join(satrlar)


def _userbot_navbatga(e: dict, bot_nomi: str) -> bool:
    """To'lov xabarini userbot navbatiga qo'yadi (sinxron — executor'da).

    NEGA NAVBAT. Shaxsiy akkauntdan ketma-ket yozish Telegram
    spam-qo'riqchisini uyg'otadi (PEER_FLOOD), shuning uchun xabar
    `marketplace/tg_notify.py` navbatiga tushadi: u bittalab, tanaffus
    va kunlik chegara bilan yuboradi (`bot/notify_worker.py` har 5
    daqiqada navbatni bo'shatib turadi).
    """
    try:
        _D, MarketplaceDB = _mk()
        from marketplace import tg_notify as N

        with MarketplaceDB() as mdb:
            return bool(N.enqueue(mdb, e["mijoz"], e["telefon"],
                                  _userbot_matni(e, bot_nomi),
                                  kind="payment", author="auto"))
    except Exception as ex:
        logger.info("💳 Userbot navbatiga qo'shilmadi (%s): %s",
                    e["mijoz"], ex)
        return False


def _userbot_yoqilgan() -> bool:
    """`debt_userbot=0` — botda bo'lmagan mijozga yozish o'chiriladi."""
    try:
        from database.channel_db import db

        return str(db.get_setting("debt_userbot", "1")).strip() \
            not in ("0", "", "yo'q")
    except Exception:
        return True


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

    # Botda bo'lmagan mijozga shaxsiy akkauntdan yozish uchun.
    userbot_ochiq = _userbot_yoqilgan()
    try:
        bot_nomi = (db.bot_username or "").lstrip("@")
    except Exception:
        bot_nomi = ""

    yuborildi = navbatga = 0
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
                logger.info("💳 Mijozga yetib bormadi (%s): %s",
                            e["mijoz"], tg_out.last_error())
        elif (e["ochiq"] and e.get("telefon") and e["tur"] == "tolov"
                and userbot_ochiq):
            # BOTDA YO'Q MIJOZ. Bot unga yoza olmaydi (Telegram faqat
            # /start bosganga ruxsat beradi), lekin raqami bor — shuning
            # uchun to'lov xabari SHAXSIY akkaunt navbatiga tushadi.
            # Faqat TO'LOV: odam pulini bergandan keyin «yetib bordimi?»
            # deb qo'ng'iroq qiladi; qolgani so'ralmagan xabar bo'lardi.
            if await _ish(_userbot_navbatga, e, bot_nomi):
                navbatga += 1
        for chat in adminlar:
            try:
                await bot.send_message(chat, _admin_matni(e), parse_mode="HTML")
                yuborildi += 1
            except Exception:
                pass
        await asyncio.sleep(0.6)      # Telegram tezlik cheklovi
    logger.info("💳 Qarz harakati: %s hodisa, %s xabar%s", len(events),
                yuborildi,
                f", {navbatga} ta userbot navbatida" if navbatga else "")
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


def _hammasi() -> bool:
    """`debt_notify_all=1` — naqd xaridga ham chek yuboriladi.

    Sukut o'chiq: hozircha faqat qarzga olingan xarid xabar qilinadi,
    chunki kassada chek mijoz nomiga ko'pincha nasiya uchun yoziladi.
    """
    try:
        from database.channel_db import db

        return str(db.get_setting("debt_notify_all", "0")).strip() in ("1", "true", "on")
    except Exception:
        return False


def _flag_on() -> bool:
    """Sozlama orqali o'chirish mumkin: `debt_notify=0`."""
    try:
        from database.channel_db import db

        return str(db.get_setting("debt_notify", "1")).strip() not in ("0", "", "yo'q")
    except Exception:
        return True
