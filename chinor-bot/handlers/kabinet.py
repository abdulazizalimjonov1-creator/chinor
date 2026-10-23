"""Mijoz kabineti — botdagi `/` buyruqlari va tugmalar.

Kassadagi eski bot (@cheklars_bot) mijozga tugmalar berardi: «Hisobim»,
«Xaridlarim», «Excel». Odamlar shunga o'rganган, shuning uchun o'sha
tugmalar saqlanadi — lekin endi ular Mini App'ning tayyor bo'limlarini
ochadi: rangli, qulay va telefonda chiroyli ko'rinadigan.

NEGA INLINE TUGMA, KLAVIATURA EMAS. Telegram oddiy klaviatura
tugmasidan ochilgan Mini App'ga `initData` imzosini BERMAYDI — ilova
foydalanuvchini tanimay qoladi. Inline tugma va menyu tugmasi esa
imzo beradi. Shuning uchun bu yerda hamma narsa inline.

Excel esa Mini App'dan emas, BOTDAN keladi: fayl to'g'ridan-to'g'ri
suhbatga tushsa, uni saqlash ham, boshqaga yuborish ham oson.
"""

import logging
import os
import sys

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile, CallbackQuery, InlineKeyboardButton,
    InlineKeyboardMarkup, Message, WebAppInfo,
)

from bot.config import CONTACT_PHONE_1, CONTACT_PHONE_2, MINI_APP_URL
from database.channel_db import db

logger = logging.getLogger(__name__)
router = Router()

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _mk():
    if _REPO not in sys.path:
        sys.path.insert(0, _REPO)
    from marketplace import debt as D
    from marketplace.db import MarketplaceDB

    return D, MarketplaceDB


def _pul(v) -> str:
    return f"{round(float(v or 0)):,}".replace(",", " ")


def _app_url(screen: str = "") -> str:
    """Mini App manzili, kerak bo'lsa bo'lim bilan."""
    if not MINI_APP_URL.startswith("https://"):
        return ""
    sep = "&" if "?" in MINI_APP_URL else "?"
    return MINI_APP_URL + (f"{sep}screen={screen}" if screen else "")


def _kabinet_kb(excel: bool = True) -> InlineKeyboardMarkup:
    """Mijoz tugmalari. Mini App manzili yo'q bo'lsa — faqat Excel."""
    rows = []
    if _app_url():
        rows.append([InlineKeyboardButton(
            text="💰 Hisobim", web_app=WebAppInfo(url=_app_url("hisob")))])
        rows.append([InlineKeyboardButton(
            text="🧾 Xaridlarim", web_app=WebAppInfo(url=_app_url("xaridlarim")))])
        rows.append([InlineKeyboardButton(
            text="🛒 Do'kon", web_app=WebAppInfo(url=_app_url()))])
    if excel:
        rows.append([InlineKeyboardButton(text="📥 Excel yuklab olish",
                                          callback_data="kab:excel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _hisob(tg_id: int) -> tuple:
    """(nomlar, qarz, xaridlar soni, oxirgi xarid) — kassadagi hisob."""
    D, MarketplaceDB = _mk()
    with MarketplaceDB() as mdb:
        D.ensure_schema(mdb.conn)
        names = D.client_by_chat(mdb.conn, str(tg_id))
        if not names:
            c = await db.get_client_by_tg(tg_id)
            phone = (c or {}).get("phone") or ""
            if phone:
                names = D.bind_phone(mdb.conn, phone, tg_id)
                mdb.conn.commit()
        if not names:
            return [], 0.0, 0, ""
        qarz = sum(D.debt(mdb.conn, n) for n in names)
        q = ",".join("?" * len(names))
        n, oxirgi = mdb.conn.execute(
            f"SELECT COUNT(*), MAX(sold_at) FROM receipts WHERE client IN ({q})",
            names).fetchone()
    return names, qarz, int(n or 0), (oxirgi or "")


def _salom(names, qarz, n, oxirgi) -> str:
    """«Zamonaviy» ko'rinish: belgilar bilan ajratilgan, qisqa va aniq."""
    if not names:
        return ("🧾 <b>Hisobingiz</b>\n\n"
                "Do'kon kassasida bu raqamga yozilgan hisob topilmadi.\n\n"
                f"Agar xaridlaringiz boshqa raqamga yozilgan bo'lsa, "
                f"{CONTACT_PHONE_1} ga qo'ng'iroq qiling.")
    holat = (f"🔴 <b>{_pul(qarz)}</b> so'm qarz" if qarz > 0
             else "🟢 <b>Qarzingiz yo'q</b>")
    satr = [f"🧾 <b>{', '.join(names)}</b>", "", holat]
    if n:
        satr.append(f"🛍 Xaridlar: <b>{n}</b> ta")
    if oxirgi:
        satr.append(f"🗓 Oxirgi xarid: {oxirgi[:16]}")
    satr += ["", "Batafsil — pastdagi tugmalardan 👇"]
    return "\n".join(satr)


# ─── Buyruqlar ──────────────────────────────────────────────────────────────

@router.message(Command("hisobim"))
async def cmd_hisobim(message: Message):
    names, qarz, n, oxirgi = await _hisob(message.from_user.id)
    await message.answer(_salom(names, qarz, n, oxirgi),
                         reply_markup=_kabinet_kb(bool(names)),
                         parse_mode="HTML")


@router.message(Command("xaridlarim"))
async def cmd_xaridlarim(message: Message):
    """Oxirgi 10 xarid — qisqa ro'yxat, batafsili Mini App'da."""
    names, qarz, n, _ = await _hisob(message.from_user.id)
    if not names:
        await message.answer(_salom(names, qarz, n, ""),
                             reply_markup=_kabinet_kb(False), parse_mode="HTML")
        return

    D, MarketplaceDB = _mk()
    with MarketplaceDB() as mdb:
        q = ",".join("?" * len(names))
        rows = mdb.conn.execute(
            f"""SELECT doc_no, kind, sold_at, payable, paid_debt
                  FROM receipts WHERE client IN ({q})
                 ORDER BY sold_at DESC LIMIT 10""", names).fetchall()

    belgi = {"sale": "🧾", "sale_deferred": "🤝", "return": "↩️"}
    satr = [f"🛍 <b>Oxirgi xaridlaringiz</b>  ({n} tadan 10 tasi)", ""]
    for r in rows:
        qarzga = (f"  ·  <i>qarzga {_pul(r['paid_debt'])}</i>"
                  if (r["paid_debt"] or 0) > 0 else "")
        satr.append(f"{belgi.get(r['kind'], '🧾')} {r['sold_at'][:16]}  "
                    f"<b>{_pul(r['payable'])}</b> so'm{qarzga}")
    satr += ["", "To'liq ro'yxat va chek tarkibi — «🧾 Xaridlarim» da 👇"]
    await message.answer("\n".join(satr), reply_markup=_kabinet_kb(),
                         parse_mode="HTML")


@router.message(Command("excel"))
async def cmd_excel(message: Message):
    await _send_excel(message.from_user.id, message)


@router.callback_query(F.data == "kab:excel")
async def cb_excel(call: CallbackQuery):
    await call.answer("Tayyorlanmoqda…")
    await _send_excel(call.from_user.id, call.message)


async def _send_excel(tg_id: int, target: Message) -> None:
    names, _qarz, _n, _o = await _hisob(tg_id)
    if not names:
        await target.answer("Kassada hisobingiz topilmadi.")
        return
    try:
        from bot import debt_export

        D, MarketplaceDB = _mk()
        with MarketplaceDB() as mdb:
            rows, jami = [], 0.0
            for n in names:
                rows += D.ledger(mdb.conn, n)
                jami += D.debt(mdb.conn, n)
            rows.sort(key=lambda r: r["at"] or "")
            path = debt_export.ledger_xlsx(names[0], rows, jami)
        with open(path, "rb") as f:
            data = f.read()
        await target.answer_document(
            BufferedInputFile(data, filename=os.path.basename(path)),
            caption="📥 Hisob-kitob daftaringiz")
    except Exception as e:
        logger.warning("Excel yuborilmadi (%s): %s", tg_id, e)
        await target.answer("Hisobot tayyorlanmadi. Birozdan keyin urinib ko'ring.")


@router.message(Command("aloqa"))
async def cmd_aloqa(message: Message):
    await message.answer(
        "📞 <b>Chinor do'koni</b>\n\n"
        f"☎️ {CONTACT_PHONE_1}\n"
        + (f"☎️ {CONTACT_PHONE_2}\n" if CONTACT_PHONE_2 else "")
        + "📍 Sergeli, Chinor bekati yoni\n"
          "🕗 Har kuni 8:00 – 22:00",
        reply_markup=_kabinet_kb(False), parse_mode="HTML")


@router.message(Command("yordam"))
async def cmd_yordam(message: Message):
    await message.answer(
        "ℹ️ <b>Yordam</b>\n\n"
        "/hisobim — qarzingiz va hisob holati\n"
        "/xaridlarim — oxirgi xaridlar\n"
        "/excel — hisob-kitob daftari (fayl)\n"
        "/aloqa — do'kon manzili va telefoni\n\n"
        "Katalog, savat va cheklar — pastdagi tugmalarda 👇",
        reply_markup=_kabinet_kb(False), parse_mode="HTML")


# ─── Admin uchun qisqa yo'l ─────────────────────────────────────────────────

@router.message(Command("qarzdorlar"))
async def cmd_qarzdorlar(message: Message):
    uid = message.from_user.id
    if not await db.is_admin(uid):
        return
    D, MarketplaceDB = _mk()
    with MarketplaceDB() as mdb:
        D.ensure_schema(mdb.conn)
        rows = D.clients_with_debt(mdb.conn, 1)
    jami = sum(r["debt"] for r in rows)
    satr = [f"📕 <b>Qarzdorlar:</b> {len(rows)} ta",
            f"💰 Jami: <b>{_pul(jami)}</b> so'm", ""]
    for r in rows[:10]:
        satr.append(f"🔴 {r['name']} — <b>{_pul(r['debt'])}</b>")
    if len(rows) > 10:
        satr.append(f"… yana {len(rows) - 10} ta")
    kb = None
    if _app_url():
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text="📕 Qarzdorlar bo'limi",
            web_app=WebAppInfo(url=_app_url("qarzdorlar")))]])
    await message.answer("\n".join(satr), reply_markup=kb, parse_mode="HTML")
