"""Qarzdorlar bo'limi — BOTNING O'ZIDA, oddiy tugmalar bilan.

NEGA KERAK. Ilgari bu ish faqat Mini App'da edi: «📕 Qarzdorlar» tugmasi
ro'yxatning birinchi o'ntasini matn qilib ko'rsatardi, qolgani uchun
Mini App'ni ochish kerak bo'lardi. Mini App esa brauzer: chek tanlash,
fayl yuklab olish va uni mijozga qayta yuborish — har biri Telegramdan
chiqib-kirishni talab qilardi.

ENDI hammasi suhbatning ichida:
  • qarzdorlar ro'yxati — tugma-tugma, sahifalab;
  • mijozni bosilsa — qarzi, telefoni, oxirgi xaridi;
  • «Cheklarni tanlash» — har chek yonida ⬜/✅, belgilash XUDDI SHU
    xabarni tahrirlaydi (ro'yxat qaytadan yuklanmaydi);
  • tanlanganini PDF, rasm yoki Excel qilib O'ZIGA yoki MIJOZGA yuborish.

TANLOV QAYERDA TURADI. Xotirada, admin bo'yicha (`_HOLAT`). Bot qayta
ishga tushsa tanlov yo'qoladi — shuning uchun eski tugma bosilsa
«qaytadan oching» deyiladi, jim qolmaydi.

NOM O'RNIGA KALIT. Mijoz nomi uzun ('Daler aka metro'), Telegram esa
callback_data uchun 64 bayt beradi. Shuning uchun nomlar jadvalga
yoziladi va tugmada faqat kalit raqami ketadi.
"""

import asyncio
import logging
import os
import sys

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile, CallbackQuery, InlineKeyboardButton,
    InlineKeyboardMarkup, Message,
)

from bot.keyboards import ADMIN_BTN_QARZ
from bot.permissions import has_permission
from database.channel_db import db

logger = logging.getLogger(__name__)
router = Router()

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PER = 8          # bir sahifada nechta tugma
RECEIPT_PER = 8  # chek tanlashda bir sahifada nechta chek

_HOLAT: dict = {}     # uid → {"name": str, "picked": set, "page": int, "rpage": int}
_NOM: dict = {}       # kalit → mijoz nomi
_KALIT: dict = {}     # mijoz nomi → kalit


def _mk():
    if _REPO not in sys.path:
        sys.path.insert(0, _REPO)
    from marketplace import debt as D
    from marketplace.db import MarketplaceDB

    return D, MarketplaceDB


def _pul(v) -> str:
    return f"{round(float(v or 0)):,}".replace(",", " ")


def _key(name: str) -> int:
    """Mijoz nomiga qisqa, barqaror kalit."""
    if name in _KALIT:
        return _KALIT[name]
    k = len(_NOM) + 1
    _NOM[k] = name
    _KALIT[name] = k
    return k


def _holat(uid: int) -> dict:
    return _HOLAT.setdefault(uid, {"name": "", "picked": set(),
                                   "page": 0, "rpage": 0})


async def _ruxsat(target) -> bool:
    """Qarzdorlar bo'limi — «mijozlarni ko'rish» ruxsati bilan.

    Admin bo'lmagan odamga JAVOB BERILMAYDI: mijoz tasodifan /qarzdorlar
    yozsa, do'konning ichki bo'limi borligini bilishi shart emas.
    """
    uid = target.from_user.id
    if await has_permission(db, uid, "clients_view"):
        return True
    if isinstance(target, CallbackQuery):
        await target.answer("⛔ Ruxsat yo'q", show_alert=True)
    elif await db.is_admin(uid):
        await target.answer("⛔ Qarzdorlar bo'limi uchun ruxsatingiz yo'q.")
    return False


# ─── Ma'lumot o'qish (sinxron — executor orqali chaqiriladi) ────────────────

def _debtors() -> tuple:
    D, MarketplaceDB = _mk()
    with MarketplaceDB() as mdb:
        D.ensure_schema(mdb.conn)
        rows = D.clients_with_debt(mdb.conn, 1)
    return rows, sum(r["debt"] for r in rows)


def _client_info(name: str) -> dict:
    D, MarketplaceDB = _mk()
    with MarketplaceDB() as mdb:
        D.ensure_schema(mdb.conn)
        row = mdb.conn.execute(
            "SELECT * FROM debt_clients WHERE name = ?", (name,)).fetchone()
        n, oxirgi = mdb.conn.execute(
            "SELECT COUNT(*), MAX(sold_at) FROM receipts WHERE client = ?",
            (name,)).fetchone()
        return {
            "name": name,
            "debt": round(D.debt(mdb.conn, name)),
            "qpos": round(D.qpos_debt(mdb.conn, name)),
            "outside": round(float(row["outside_debt"]) if row else 0),
            "phone": (row["phone"] if row else "") or "",
            "chat_id": (row["chat_id"] if row else "") or "",
            "remind_open": int(row["remind_open"]) if row else 1,
            "soni": int(n or 0),
            "oxirgi": (oxirgi or "")[:16],
        }


def _receipts(name: str, page: int) -> tuple:
    from bot.api_debt import _receipt_rows, _rows_to_receipts

    _D, MarketplaceDB = _mk()
    with MarketplaceDB() as mdb:
        rows, counts, total = _receipt_rows(mdb.conn, [name], RECEIPT_PER,
                                            page * RECEIPT_PER)
        return _rows_to_receipts(rows, counts), total


def _fayl(name: str, ids, kind: str, fmt: str) -> tuple:
    from bot.api_debt import _build_file

    D, MarketplaceDB = _mk()
    with MarketplaceDB() as mdb:
        D.ensure_schema(mdb.conn)
        return _build_file(mdb.conn, name, ids, kind, fmt)


async def _ish(fn, *a):
    """Bazani va rasm chizishni alohida ipda bajaramiz — bot qotib
    qolmasin (PDF 30 ta chek bilan bir sekundga yaqin vaqt oladi)."""
    return await asyncio.get_running_loop().run_in_executor(None, fn, *a)


# ─── Ro'yxat ───────────────────────────────────────────────────────────────

def _list_kb(rows, page: int) -> InlineKeyboardMarkup:
    sahifa = max(1, (len(rows) + PER - 1) // PER)
    page = max(0, min(page, sahifa - 1))
    kb = []
    for r in rows[page * PER:(page + 1) * PER]:
        jim = " 🔕" if not r["remind_open"] else ""
        bog = " 💬" if r["chat_id"] else ""
        kb.append([InlineKeyboardButton(
            text=f"{r['name'][:22]}{bog}{jim} — {_pul(r['debt'])}",
            callback_data=f"qz:c:{_key(r['name'])}")])
    if sahifa > 1:
        nav = []
        nav.append(InlineKeyboardButton(
            text="◀️" if page > 0 else "·",
            callback_data=f"qz:l:{page - 1}" if page > 0 else "qz:noop"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{sahifa}",
                                        callback_data="qz:noop"))
        nav.append(InlineKeyboardButton(
            text="▶️" if page < sahifa - 1 else "·",
            callback_data=f"qz:l:{page + 1}" if page < sahifa - 1 else "qz:noop"))
        kb.append(nav)
    kb.append([InlineKeyboardButton(text="📨 Hammasiga eslatma",
                                    callback_data="qz:bulk")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def _list_text(rows, jami) -> str:
    return (f"📕 <b>Qarzdorlar</b> — {len(rows)} ta\n"
            f"💰 Umumiy qarz: <b>{_pul(jami)}</b> so'm\n\n"
            f"Mijozni bosing: cheklari, hisob-kitobi va yuborish tugmalari "
            f"shu yerda.\n"
            f"<i>💬 — botda bor,  🔕 — eslatma o'chirilgan</i>")


async def _korsat_royxat(target, page: int = 0, edit: bool = False):
    rows, jami = await _ish(_debtors)
    if not rows:
        matn = "📕 <b>Qarzdorlar</b>\n\n✅ Hozir qarzdor yo'q."
        kb = None
    else:
        matn, kb = _list_text(rows, jami), _list_kb(rows, page)
    if edit:
        await _edit(target, matn, kb)
    else:
        await target.answer(matn, reply_markup=kb, parse_mode="HTML")


async def _edit(call: CallbackQuery, matn: str, kb):
    """Xabarni JOYIDA tahrirlaydi — yangi xabar yuborilmaydi, shuning
    uchun suhbat ro'yxat bilan to'lib ketmaydi."""
    try:
        await call.message.edit_text(matn, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        # «message is not modified» — normal holat, e'tibor bermaymiz.
        if "not modified" not in str(e):
            logger.info("Qarzdorlar xabari tahrirlanmadi: %s", e)


# ─── Mijoz kartasi ─────────────────────────────────────────────────────────

def _client_kb(c: dict, belgilangan: int = 0) -> InlineKeyboardMarkup:
    tanlov = f" ({belgilangan})" if belgilangan else ""
    kb = [
        [InlineKeyboardButton(text=f"🧾 Cheklarni tanlash{tanlov}",
                              callback_data="qz:r:0")],
        [InlineKeyboardButton(text="📄 Daftar PDF", callback_data="qz:f:pdf:me:ledger"),
         InlineKeyboardButton(text="🖼 Daftar rasm", callback_data="qz:f:png:me:ledger")],
        [InlineKeyboardButton(text="📥 Daftar Excel",
                              callback_data="qz:f:xlsx:me:ledger")],
    ]
    if c["debt"] > 0 and c["remind_open"]:
        kb.append([InlineKeyboardButton(text="📨 Qarz eslatmasi",
                                        callback_data="qz:rem")])
    kb.append([InlineKeyboardButton(text="◀️ Ro'yxat", callback_data="qz:l:0")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def _client_text(c: dict) -> str:
    holat = (f"🔴 Qarz: <b>{_pul(c['debt'])}</b> so'm" if c["debt"] > 0
             else "🟢 Qarzi yo'q")
    satr = [f"👤 <b>{c['name']}</b>", "", holat]
    if c["outside"]:
        satr.append(f"📒 Tashqi qarz: {_pul(c['outside'])}")
    satr.append(f"🏪 Kassa bo'yicha: {_pul(c['qpos'])}")
    satr.append(f"📱 {c['phone'] or 'telefon yo‘q'}")
    satr.append("💬 Telegram: " + ("bog'langan ✅" if c["chat_id"]
                                   else "yo'q — fayl yuborib bo'lmaydi"))
    if c["soni"]:
        satr.append(f"🛍 Xaridlar: {c['soni']} ta · oxirgisi {c['oxirgi']}")
    if not c["remind_open"]:
        satr.append("🔕 Eslatma o'chirilgan")
    return "\n".join(satr)


async def _korsat_mijoz(call: CallbackQuery, name: str):
    h = _holat(call.from_user.id)
    if h["name"] != name:
        h["name"], h["picked"], h["rpage"] = name, set(), 0
    c = await _ish(_client_info, name)
    await _edit(call, _client_text(c), _client_kb(c, len(h["picked"])))


# ─── Chek tanlash ──────────────────────────────────────────────────────────

def _receipts_kb(items, total: int, page: int, picked: set) -> InlineKeyboardMarkup:
    kb = []
    for r in items:
        on = r["id"] in picked
        belgi = {"qaytarish": "↩️", "nasiya": "🤝"}.get(r["kind"], "🧾")
        kb.append([InlineKeyboardButton(
            text=f"{'✅' if on else '⬜️'} {belgi} {r['at'][5:16]} · {_pul(r['payable'])}",
            callback_data=f"qz:t:{r['id']}")])

    sahifa = max(1, (total + RECEIPT_PER - 1) // RECEIPT_PER)
    if sahifa > 1:
        kb.append([
            InlineKeyboardButton(
                text="◀️" if page > 0 else "·",
                callback_data=f"qz:r:{page - 1}" if page > 0 else "qz:noop"),
            InlineKeyboardButton(text=f"{page + 1}/{sahifa}",
                                 callback_data="qz:noop"),
            InlineKeyboardButton(
                text="▶️" if page < sahifa - 1 else "·",
                callback_data=f"qz:r:{page + 1}" if page < sahifa - 1 else "qz:noop"),
        ])
    kb.append([InlineKeyboardButton(text="✅ Sahifani belgilash",
                                    callback_data="qz:all"),
               InlineKeyboardButton(text="🗑 Tozalash", callback_data="qz:none")])
    if picked:
        kb.append([InlineKeyboardButton(text="📄 PDF — mijozga",
                                        callback_data="qz:f:pdf:cl:receipts"),
                   InlineKeyboardButton(text="🖼 Rasm — mijozga",
                                        callback_data="qz:f:png:cl:receipts")])
        kb.append([InlineKeyboardButton(text="📄 PDF — menga",
                                        callback_data="qz:f:pdf:me:receipts"),
                   InlineKeyboardButton(text="🖼 Rasm — menga",
                                        callback_data="qz:f:png:me:receipts")])
        kb.append([InlineKeyboardButton(text="📥 Excel — menga",
                                        callback_data="qz:f:xlsx:me:receipts"),
                   InlineKeyboardButton(text="🧾 Matn — mijozga",
                                        callback_data="qz:txt")])
    kb.append([InlineKeyboardButton(text="◀️ Mijoz", callback_data="qz:back")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


async def _korsat_cheklar(call: CallbackQuery, page: int):
    h = _holat(call.from_user.id)
    name = h["name"]
    if not name:
        await call.answer("Avval mijozni tanlang", show_alert=True)
        return
    h["rpage"] = max(0, page)
    items, total = await _ish(_receipts, name, h["rpage"])
    h["sahifa_idlari"] = [r["id"] for r in items]

    jami = sum(r["payable"] for r in items if r["id"] in h["picked"])
    matn = [f"🧾 <b>{name}</b> — cheklari: {total} ta", ""]
    if h["picked"]:
        matn.append(f"✅ Belgilangan: <b>{len(h['picked'])}</b> ta")
        if jami:
            matn.append(f"💵 Shu sahifada: {_pul(jami)} so'm")
        matn.append("")
        matn.append("Pastdagi tugmalar bilan PDF, rasm yoki Excel qilib "
                    "yuborasiz.")
    else:
        matn.append("Chekni bosib belgilang — belgilash shu xabarni "
                    "yangilaydi, ro'yxat qaytadan yuklanmaydi.")
    await _edit(call, "\n".join(matn),
                _receipts_kb(items, total, h["rpage"], h["picked"]))


# ─── Buyruq va tugmalar ────────────────────────────────────────────────────

@router.message(Command("qarzdorlar"))
async def cmd_qarzdorlar(message: Message):
    if not await _ruxsat(message):
        return
    await _korsat_royxat(message)


@router.message(F.text == ADMIN_BTN_QARZ)
async def btn_qarzdorlar(message: Message):
    if not await _ruxsat(message):
        return
    await _korsat_royxat(message)


@router.callback_query(F.data == "qz:noop")
async def cb_noop(call: CallbackQuery):
    await call.answer()


@router.callback_query(F.data.startswith("qz:l:"))
async def cb_list(call: CallbackQuery):
    if not await _ruxsat(call):
        return
    await call.answer()
    try:
        page = int(call.data.split(":")[-1])
    except ValueError:
        page = 0
    _holat(call.from_user.id)["page"] = page
    await _korsat_royxat(call, page, edit=True)


@router.callback_query(F.data.startswith("qz:c:"))
async def cb_client(call: CallbackQuery):
    if not await _ruxsat(call):
        return
    try:
        k = int(call.data.split(":")[-1])
    except ValueError:
        k = 0
    name = _NOM.get(k, "")
    if not name:
        await call.answer("Ro'yxat eskirgan — «📕 Qarzdorlar» ni qaytadan "
                          "bosing", show_alert=True)
        return
    await call.answer()
    await _korsat_mijoz(call, name)


@router.callback_query(F.data == "qz:back")
async def cb_back(call: CallbackQuery):
    if not await _ruxsat(call):
        return
    name = _holat(call.from_user.id)["name"]
    if not name:
        await call.answer()
        await _korsat_royxat(call, 0, edit=True)
        return
    await call.answer()
    await _korsat_mijoz(call, name)


@router.callback_query(F.data.startswith("qz:r:"))
async def cb_receipts(call: CallbackQuery):
    if not await _ruxsat(call):
        return
    await call.answer()
    try:
        page = int(call.data.split(":")[-1])
    except ValueError:
        page = 0
    await _korsat_cheklar(call, page)


@router.callback_query(F.data.startswith("qz:t:"))
async def cb_toggle(call: CallbackQuery):
    """Ptichka. Faqat TUGMALAR yangilanadi — ro'yxat qaytadan o'qilmaydi,
    shuning uchun bosilganda ekran «sakramaydi»."""
    if not await _ruxsat(call):
        return
    h = _holat(call.from_user.id)
    try:
        rid = int(call.data.split(":")[-1])
    except ValueError:
        await call.answer()
        return
    if rid in h["picked"]:
        h["picked"].discard(rid)
        await call.answer("⬜️ olindi")
    else:
        h["picked"].add(rid)
        await call.answer("✅ belgilandi")
    await _korsat_cheklar(call, h["rpage"])


@router.callback_query(F.data == "qz:all")
async def cb_all(call: CallbackQuery):
    if not await _ruxsat(call):
        return
    h = _holat(call.from_user.id)
    h["picked"].update(h.get("sahifa_idlari") or [])
    await call.answer("✅ Sahifa belgilandi")
    await _korsat_cheklar(call, h["rpage"])


@router.callback_query(F.data == "qz:none")
async def cb_none(call: CallbackQuery):
    if not await _ruxsat(call):
        return
    h = _holat(call.from_user.id)
    h["picked"] = set()
    await call.answer("🗑 Tanlov tozalandi")
    await _korsat_cheklar(call, h["rpage"])


# ─── Yuborish ──────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("qz:f:"))
async def cb_file(call: CallbackQuery):
    """`qz:f:<fmt>:<kim>:<nima>` — PDF/rasm/Excel ni menga yoki mijozga."""
    if not await _ruxsat(call):
        return
    h = _holat(call.from_user.id)
    name = h["name"]
    if not name:
        await call.answer("Avval mijozni tanlang", show_alert=True)
        return
    try:
        _, _, fmt, kim, kind = call.data.split(":")
    except ValueError:
        await call.answer()
        return

    ids = sorted(h["picked"])
    if kind == "receipts" and not ids:
        await call.answer("Avval chek belgilang", show_alert=True)
        return

    await call.answer("Tayyorlanmoqda…")
    try:
        path, rasmmi = await _ish(_fayl, name, ids, kind, fmt)
    except Exception as e:
        logger.warning("Qarz fayli yasalmadi (%s): %s", name, e)
        await call.message.answer("Fayl tayyorlanmadi. Birozdan keyin "
                                  "urinib ko'ring.")
        return
    if not path:
        await call.message.answer("Chek topilmadi.")
        return

    if kim == "cl":
        c = await _ish(_client_info, name)
        if not c["chat_id"]:
            await call.message.answer(
                f"⚠️ <b>{name}</b> botda ro'yxatdan o'tmagan — unga fayl "
                f"yuborib bo'lmaydi.\n\nMijoz @cheklars_bot yoki shu botga "
                f"/start bosib, raqamini tasdiqlashi kerak.",
                parse_mode="HTML")
            return
        from bot import tg_out

        sarlavha = ("🧾 <b>Chinor</b> — xaridlaringiz"
                    if kind == "receipts" else
                    "💰 <b>Chinor</b> — hisob-kitob daftaringiz")
        if kind == "receipts":
            sarlavha += f"\n{len(ids)} ta chek"
        if c["debt"] > 0:
            sarlavha += f"\n📕 Joriy qarz: <b>{_pul(c['debt'])}</b> so'm"
        ok = await tg_out.send_file(c["chat_id"], path, caption=sarlavha,
                                    as_photo=rasmmi)
        await call.message.answer(
            f"✅ <b>{name}</b> ga yuborildi ({fmt.upper()})" if ok else
            f"❌ Yuborilmadi — mijoz botni bloklagan bo'lishi mumkin.",
            parse_mode="HTML")
        return

    # O'ziga — aiogram orqali (admin asosiy botda, token aniq ishlaydi).
    with open(path, "rb") as f:
        blob = f.read()
    fayl = BufferedInputFile(blob, filename=os.path.basename(path))
    sarlavha = f"📎 <b>{name}</b> · " + ("cheklar" if kind == "receipts"
                                         else "hisob-kitob")
    if rasmmi:
        from bot import debt_export

        rasmmi = debt_export.photo_yaroqli(path)
    if rasmmi:
        await call.message.answer_photo(fayl, caption=sarlavha,
                                        parse_mode="HTML")
    else:
        await call.message.answer_document(fayl, caption=sarlavha,
                                           parse_mode="HTML")


@router.callback_query(F.data == "qz:txt")
async def cb_text(call: CallbackQuery):
    """Tanlangan cheklarni MATN qilib mijozga — fayl ochish shart emas."""
    if not await _ruxsat(call):
        return
    h = _holat(call.from_user.id)
    name, ids = h["name"], sorted(h["picked"])
    if not name or not ids:
        await call.answer("Avval chek belgilang", show_alert=True)
        return
    await call.answer("Yuborilmoqda…")

    from bot.api_debt import _collect, _receipt_text, _tg_send

    def _oq():
        D, MarketplaceDB = _mk()
        with MarketplaceDB() as mdb:
            D.ensure_schema(mdb.conn)
            row = mdb.conn.execute(
                "SELECT chat_id FROM debt_clients WHERE name = ?",
                (name,)).fetchone()
            return ((row["chat_id"] if row else "") or "",
                    _collect(mdb.conn, [name], ids),
                    round(D.debt(mdb.conn, name)))

    chat_id, recs, qarz = await _ish(_oq)
    if not chat_id:
        await call.message.answer(f"⚠️ {name} botda ro'yxatdan o'tmagan.")
        return
    yuborildi = 0
    for rec in recs:
        if await _tg_send(chat_id, _receipt_text(rec)):
            yuborildi += 1
    if len(recs) > 1:
        jami = sum(float(r["payable"] or 0) for r in recs)
        await _tg_send(chat_id,
                       f"📦 Yuborildi: {len(recs)} ta chek\n"
                       f"💵 Jami: <b>{_pul(jami)}</b> so'm\n"
                       f"📕 Joriy qarz: <b>{_pul(qarz)}</b> so'm")
    await call.message.answer(f"✅ {yuborildi}/{len(recs)} chek yuborildi.")


@router.callback_query(F.data == "qz:rem")
async def cb_remind(call: CallbackQuery):
    """Bitta mijozga qarz eslatmasi — botda bo'lsa bot yozadi."""
    if not await _ruxsat(call):
        return
    name = _holat(call.from_user.id)["name"]
    if not name:
        await call.answer("Avval mijozni tanlang", show_alert=True)
        return
    await call.answer("Yuborilmoqda…")

    from bot.api_debt import _remind_text, _tg_send

    c = await _ish(_client_info, name)
    if c["debt"] <= 0:
        await call.message.answer("Mijozning qarzi yo'q.")
        return
    if not c["remind_open"]:
        await call.message.answer("Bu mijozga eslatma o'chirilgan.")
        return
    if not c["chat_id"]:
        await call.message.answer(
            f"⚠️ {name} botda yo'q. Shaxsiy akkauntdan yozish Mini App'dagi "
            f"«📱 Shaxsiy akkauntdan yozish» tugmasida (navbat bilan ketadi).")
        return
    ok = await _tg_send(c["chat_id"], _remind_text(c["debt"]))
    await call.message.answer(f"✅ {name} ga eslatma yuborildi." if ok else
                              "❌ Yuborilmadi.")


@router.callback_query(F.data == "qz:bulk")
async def cb_bulk(call: CallbackQuery):
    """Hammasiga eslatma — botdagilarga darhol, qolgani Mini App navbatida.

    Bu yerda ATAYLAB faqat botdagilarga yuboriladi: shaxsiy akkauntdan
    ommaviy yozish Telegram cheklovига urinadi, u navbat bilan ishlaydi
    va holati Mini App'da ko'rinadi.
    """
    if not await _ruxsat(call):
        return
    await call.answer("Yuborilmoqda…")
    from bot.api_debt import _remind_text, _tg_send

    rows, _jami = await _ish(_debtors)
    yuborildi = otkazildi = 0
    for r in rows:
        if not r["chat_id"] or not r["remind_open"]:
            otkazildi += 1
            continue
        if await _tg_send(r["chat_id"], _remind_text(round(r["debt"]))):
            yuborildi += 1
        else:
            otkazildi += 1
        await asyncio.sleep(0.4)       # Telegram tezlik cheklovi
    await call.message.answer(
        f"📨 Eslatma: <b>{yuborildi}</b> ta yuborildi, {otkazildi} ta "
        f"o'tkazildi (botda yo'q yoki 🔕).\n\n"
        f"Botda yo'qlariga shaxsiy akkauntdan yozish — Mini App'dagi "
        f"«📨 Eslatma yuborish (hammasiga)» bo'limida.",
        parse_mode="HTML")
