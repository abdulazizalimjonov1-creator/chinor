"""Qarz daftari va mijoz kabineti — HTTP API.

Bu bo'lim kassa kompyuteridagi ChekHisobot (@cheklars_bot) o'rnini bosadi.
U yerda mijoz Telegram tugmalari orqali «Hisobim / Xaridlarim / Excel» ni
olardi; bu yerda xuddi shu narsa Mini App ekranida, admin tomoni esa
qarzdorlar ro'yxati, mijoz daftari va cheklarni birlashtirib yuborish.

MIJOZ QANDAY TANILADI. Chekda mijozning id si yo'q, faqat «Клиент : ...»
nomi bor — shuning uchun qarz daftarida kalit NOM. Telegram foydalanuvchisi
o'sha nomga ikki yo'l bilan bog'lanadi:

  1. `debt_clients.chat_id` — bog'langani yozib qo'yilgan (eski bot'dan
     ko'chirilgan 14 ta mijoz shunday keldi);
  2. telefon raqami — Mini App mijozining `pos.db` dagi raqami qarz
     daftaridagi raqam bilan oxirgi 9 raqami bo'yicha mos tushsa.

Ikkinchi yo'l bilan topilsa, bog'lanish BIR MARTA yozib qo'yiladi — keyingi
safar qidirilmaydi. Bitta odamning kassada bir nechta nomi bo'lishi mumkin
(«Daler aka», «Daler aka metro»), shuning uchun natija har doim RO'YXAT.

MAXFIYLIK: mijoz faqat O'ZIGA bog'langan nomlarning chekini va qarzini
ko'radi. Nom so'rov parametridan OLINMAYDI — aks holda boshqa nomni yozib
begonaning hisobini ko'rish mumkin bo'lardi.
"""

import os
import sys

from aiohttp import web

DEBT_ROUTES = web.RouteTableDef()

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

_KIND = {"sale": "savdo", "sale_deferred": "nasiya", "return": "qaytarish"}


def _mdb():
    """`marketplace.db` — cheklar, to'lovlar va qarz daftari shu yerda."""
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)
    from marketplace.db import MarketplaceDB

    return MarketplaceDB()


def _debt():
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)
    from marketplace import debt as D

    return D


# ─── Kim so'rayapti ─────────────────────────────────────────────────────────

async def _identity(request):
    """(auth, names, err) — so'rovchi kim va unga qaysi nomlar bog'langan.

    `names` — qarz daftaridagi nomlar ro'yxati (bo'sh bo'lishi mumkin).
    Admin uchun ham o'z nomlari qaytadi: admin ham do'kondan xarid qiladi.
    """
    from database.channel_db import db as _db_pos
    from bot.web_api import _authenticate, _err

    auth = await _authenticate(request, _db_pos)
    if not auth:
        return None, [], _err("Avtorizatsiya talab qilinadi", 401)

    D = _debt()
    tg_id = str(auth["tg_id"])
    phone = str((auth.get("user") or {}).get("phone") or "")

    with _mdb() as mdb:
        D.ensure_schema(mdb.conn)
        names = D.client_by_chat(mdb.conn, tg_id)
        if not names and phone:
            names = D.client_by_phone(mdb.conn, D._digits9(phone))
            # Topildi — bog'lanishni yozib qo'yamiz, keyingi safar
            # telefon bo'yicha qidirish shart bo'lmaydi.
            for n in names:
                D.upsert_client(mdb.conn, n, chat_id=tg_id)
            if names:
                mdb.conn.commit()
    return auth, names, None


def _rows_to_receipts(rows, counts=None):
    out = []
    for r in rows:
        out.append({
            "id": r["msg_id"],
            "doc_no": r["doc_no"] or "",
            "kind": _KIND.get(r["kind"], r["kind"]),
            "raw_kind": r["kind"],
            "at": r["sold_at"],
            "day": r["day"],
            "cashier": (r["cashier"] or "") if "cashier" in r.keys() else "",
            "client": (r["client"] or "").strip(),
            "payable": round(r["payable"] or 0),
            "debt": round(r["paid_debt"] or 0),
            "client_debt": (None if r["client_debt"] is None
                            else round(r["client_debt"])),
            "lines": int((counts or {}).get(r["msg_id"], 0)),
        })
    return out


def _receipt_rows(conn, names, limit, offset):
    """Berilgan nomlarga tegishli cheklar (yangi → eski)."""
    if not names:
        return [], {}, 0
    q = ",".join("?" * len(names))
    total = conn.execute(
        f"SELECT COUNT(*) FROM receipts WHERE client IN ({q})", names
    ).fetchone()[0]
    rows = conn.execute(
        f"""SELECT msg_id, doc_no, kind, cashier, client, sold_at, day,
                   payable, paid_debt, client_debt
              FROM receipts WHERE client IN ({q})
             ORDER BY sold_at DESC, msg_id DESC LIMIT ? OFFSET ?""",
        (*names, limit, offset),
    ).fetchall()
    ids = [r["msg_id"] for r in rows]
    counts = {}
    if ids:
        qi = ",".join("?" * len(ids))
        counts = dict(conn.execute(
            f"SELECT msg_id, COUNT(*) FROM receipt_items "
            f"WHERE msg_id IN ({qi}) GROUP BY msg_id", ids).fetchall())
    return rows, counts, total


# ─── Mijoz kabineti ─────────────────────────────────────────────────────────

@DEBT_ROUTES.get("/api/my/summary")
async def api_my_summary(request: web.Request):
    """«Hisobim» ning boshi: qarz, xaridlar soni, oxirgi xarid."""
    auth, names, err = await _identity(request)
    if err is not None:
        return err

    D = _debt()
    with _mdb() as mdb:
        total = sum(D.debt(mdb.conn, n) for n in names)
        info = []
        for n in names:
            row = mdb.conn.execute(
                "SELECT * FROM debt_clients WHERE name = ?", (n,)).fetchone()
            cnt, last, summa = mdb.conn.execute(
                """SELECT COUNT(*), MAX(sold_at), COALESCE(SUM(payable),0)
                     FROM receipts WHERE client = ?""", (n,)).fetchone()
            info.append({
                "name": n,
                "debt": round(D.debt(mdb.conn, n)),
                "limit": round(float(row["credit_limit"]) if row else 0),
                "receipts": int(cnt or 0),
                "last": last or "",
                "spent": round(summa or 0),
            })

    return web.json_response({
        "ok": True,
        "linked": bool(names),
        "debt": round(total),
        "accounts": info,
        "name": (auth.get("user") or {}).get("shop_name") or "",
    })


@DEBT_ROUTES.get("/api/my/receipts")
async def api_my_receipts(request: web.Request):
    """«Xaridlarim» — mijozning o'z cheklari, sahifalab."""
    auth, names, err = await _identity(request)
    if err is not None:
        return err
    if not names:
        return web.json_response({"ok": True, "linked": False,
                                  "items": [], "total": 0})

    try:
        per = max(1, min(100, int(request.query.get("per") or 20)))
        page = max(0, int(request.query.get("page") or 0))
    except ValueError:
        per, page = 20, 0

    with _mdb() as mdb:
        rows, counts, total = _receipt_rows(mdb.conn, names, per, page * per)

    return web.json_response({
        "ok": True, "linked": True, "page": page, "per": per,
        "total": total, "items": _rows_to_receipts(rows, counts),
    })


@DEBT_ROUTES.get("/api/my/receipt")
async def api_my_receipt(request: web.Request):
    """Bitta chekning tovarlari — faqat o'z chekini ochish mumkin."""
    auth, names, err = await _identity(request)
    if err is not None:
        return err
    try:
        msg_id = int(request.query.get("id") or 0)
    except ValueError:
        msg_id = 0
    if not msg_id:
        from bot.web_api import _err
        return _err("Chek tanlanmadi")

    with _mdb() as mdb:
        head = mdb.conn.execute(
            """SELECT msg_id, doc_no, kind, cashier, client, sold_at, day,
                      total, discount, payable, paid_cash, paid_card,
                      paid_click, paid_debt, client_debt
                 FROM receipts WHERE msg_id = ?""", (msg_id,)).fetchone()
        if head is None or (head["client"] or "").strip() not in names:
            from bot.web_api import _err
            return _err("Chek topilmadi", 404)
        items = mdb.conn.execute(
            """SELECT line_no, name, qty, price, amount FROM receipt_items
                WHERE msg_id = ? ORDER BY line_no""", (msg_id,)).fetchall()

    return web.json_response({"ok": True, "receipt": _receipt_full(head, items)})


def _receipt_full(head, items):
    return {
        "id": head["msg_id"],
        "doc_no": head["doc_no"] or "",
        "kind": _KIND.get(head["kind"], head["kind"]),
        "at": head["sold_at"],
        "cashier": head["cashier"] or "",
        "client": (head["client"] or "").strip(),
        "total": round(head["total"] or 0),
        "discount": round(head["discount"] or 0),
        "payable": round(head["payable"] or 0),
        "paid": {
            "cash": round(head["paid_cash"] or 0),
            "card": round(head["paid_card"] or 0),
            "click": round(head["paid_click"] or 0),
            "debt": round(head["paid_debt"] or 0),
        },
        "client_debt": (None if head["client_debt"] is None
                        else round(head["client_debt"])),
        "items": [{
            "no": i["line_no"], "name": i["name"],
            "qty": i["qty"], "price": round(i["price"] or 0),
            "amount": round(i["amount"] or 0),
        } for i in items],
    }


@DEBT_ROUTES.get("/api/my/ledger")
async def api_my_ledger(request: web.Request):
    """«Hisobim» daftari — har qator qoldiqni qanday o'zgartirgani."""
    auth, names, err = await _identity(request)
    if err is not None:
        return err
    if not names:
        return web.json_response({"ok": True, "linked": False, "rows": []})

    try:
        limit = max(0, min(500, int(request.query.get("limit") or 100)))
    except ValueError:
        limit = 100

    D = _debt()
    out = []
    with _mdb() as mdb:
        for n in names:
            for row in D.ledger(mdb.conn, n, limit=limit):
                row = dict(row)
                row["account"] = n
                out.append(row)
        total = sum(D.debt(mdb.conn, n) for n in names)
    out.sort(key=lambda r: r["at"] or "", reverse=True)

    return web.json_response({"ok": True, "linked": True,
                              "debt": round(total), "rows": out})


# ─── Admin: qarzdorlar ──────────────────────────────────────────────────────

@DEBT_ROUTES.get("/api/debt/clients")
async def api_debt_clients(request: web.Request):
    """Qarzdorlar ro'yxati — kattasidan kichigiga. Ruxsat: 'clients'."""
    from database.channel_db import db as _db_pos
    from bot.web_api import _need_admin

    auth, err = await _need_admin(request, _db_pos, "clients_view")
    if err is not None:
        return err

    try:
        min_amount = float(request.query.get("min") or 1)
    except ValueError:
        min_amount = 1.0

    D = _debt()
    with _mdb() as mdb:
        D.ensure_schema(mdb.conn)
        rows = D.clients_with_debt(mdb.conn, min_amount)

    return web.json_response({
        "ok": True,
        "count": len(rows),
        "total": sum(r["debt"] for r in rows),
        "outside": sum(r["outside"] for r in rows),
        "items": rows,
    })


@DEBT_ROUTES.get("/api/debt/client")
async def api_debt_client(request: web.Request):
    """Bitta mijoz: qarzi, sozlamalari va hisob-kitob daftari."""
    from database.channel_db import db as _db_pos
    from bot.web_api import _need_admin, _err

    auth, err = await _need_admin(request, _db_pos, "clients_view")
    if err is not None:
        return err

    name = (request.query.get("name") or "").strip()
    if not name:
        return _err("Mijoz tanlanmadi")
    try:
        limit = max(0, min(500, int(request.query.get("limit") or 200)))
    except ValueError:
        limit = 200

    D = _debt()
    with _mdb() as mdb:
        D.ensure_schema(mdb.conn)
        row = mdb.conn.execute(
            "SELECT * FROM debt_clients WHERE name = ?", (name,)).fetchone()
        snap = D.snapshot(mdb.conn, name)
        data = {
            "name": name,
            "debt": round(D.debt(mdb.conn, name)),
            "qpos": round(D.qpos_debt(mdb.conn, name)),
            "phone": (row["phone"] if row else "") or "",
            "phone2": (row["phone2"] if row else "") or "",
            "chat_id": (row["chat_id"] if row else "") or "",
            "note": (row["note"] if row else "") or "",
            "limit": round(float(row["credit_limit"]) if row else 0),
            "outside": round(float(row["outside_debt"]) if row else 0),
            "outside_note": (row["outside_note"] if row else "") or "",
            "sync_open": int(row["sync_open"]) if row else 1,
            "remind_open": int(row["remind_open"]) if row else 1,
            "snapshot": ({"at": snap.at, "value": round(snap.value),
                          "source": snap.source} if snap else None),
        }
        data["ledger"] = D.ledger(mdb.conn, name, limit=limit)

    return web.json_response({"ok": True, "client": data})


@DEBT_ROUTES.get("/api/debt/receipts")
async def api_debt_receipts(request: web.Request):
    """Mijozning cheklari — birlashtirib yuborish uchun tanlanadi."""
    from database.channel_db import db as _db_pos
    from bot.web_api import _need_admin, _err

    auth, err = await _need_admin(request, _db_pos, "clients_view")
    if err is not None:
        return err

    name = (request.query.get("name") or "").strip()
    if not name:
        return _err("Mijoz tanlanmadi")
    try:
        per = max(1, min(200, int(request.query.get("per") or 30)))
        page = max(0, int(request.query.get("page") or 0))
    except ValueError:
        per, page = 30, 0

    with _mdb() as mdb:
        rows, counts, total = _receipt_rows(mdb.conn, [name], per, page * per)

    return web.json_response({
        "ok": True, "page": page, "per": per, "total": total,
        "items": _rows_to_receipts(rows, counts),
    })


@DEBT_ROUTES.post("/api/debt/client/save")
async def api_debt_client_save(request: web.Request):
    """Mijoz sozlamalari: telefon, chegara, tashqi qarz, qulflar."""
    from database.channel_db import db as _db_pos
    from bot.web_api import _need_admin, _err, _read_json

    auth, err = await _need_admin(request, _db_pos, "clients_add")
    if err is not None:
        return err

    body = await _read_json(request)
    name = (body.get("name") or "").strip()
    if not name:
        return _err("Mijoz tanlanmadi")

    fields = {}
    for k, cast in (("phone", str), ("phone2", str), ("note", str),
                    ("outside_note", str), ("credit_limit", float),
                    ("outside_debt", float), ("sync_open", int),
                    ("remind_open", int)):
        if k in body:
            try:
                fields[k] = cast(body[k] if body[k] is not None else
                                 ("" if cast is str else 0))
            except (TypeError, ValueError):
                return _err(f"«{k}» qiymati noto'g'ri")
    if not fields:
        return _err("O'zgarish yo'q")

    D = _debt()
    with _mdb() as mdb:
        D.ensure_schema(mdb.conn)
        D.upsert_client(mdb.conn, name, **fields)
        mdb.conn.commit()
        debt_now = round(D.debt(mdb.conn, name))

    return web.json_response({"ok": True, "debt": debt_now})


@DEBT_ROUTES.post("/api/debt/move")
async def api_debt_move(request: web.Request):
    """Qo'lda harakat: to'lov / qarz / tuzatish.

    Kassadagi to'lovlar o'zi keladi — bu faqat kassadan TASHQARIDA bo'lgani
    uchun (daftardagi pul, kelishuv). Shuning uchun izoh talab qilinadi:
    keyin «bu qayerdan chiqdi?» degan savol qolmasin.
    """
    from database.channel_db import db as _db_pos
    from bot.web_api import _need_admin, _err, _read_json

    auth, err = await _need_admin(request, _db_pos, "clients_pay")
    if err is not None:
        return err

    body = await _read_json(request)
    name = (body.get("name") or "").strip()
    kind = (body.get("kind") or "").strip()
    note = (body.get("note") or "").strip()
    try:
        amount = float(body.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0.0

    if not name:
        return _err("Mijoz tanlanmadi")
    if kind not in ("payment", "charge", "adjust"):
        return _err("Harakat turi noto'g'ri")
    if amount == 0:
        return _err("Summa nol bo'lmasin")
    if not note:
        return _err("Izoh yozing — keyin nima uchun ekani esda qolsin")

    D = _debt()
    with _mdb() as mdb:
        D.ensure_schema(mdb.conn)
        mid = D.add_move(mdb.conn, name, kind, amount, note=note,
                         author=str(auth["tg_id"]))
        mdb.conn.commit()
        debt_now = round(D.debt(mdb.conn, name))

    return web.json_response({"ok": True, "id": mid, "debt": debt_now})


# ─── Excel ──────────────────────────────────────────────────────────────────

def _collect(conn, names, ids=None, limit=500):
    """Cheklarni tovarlari bilan birga yig'adi (Excel uchun)."""
    if ids:
        q = ",".join("?" * len(ids))
        nq = ",".join("?" * len(names))
        # Nom bo'yicha ham cheklaymiz: begona chek tanlab yuborilmasin.
        rows = conn.execute(
            f"""SELECT msg_id, doc_no, kind, cashier, client, sold_at, day,
                       payable, paid_debt, client_debt
                  FROM receipts WHERE msg_id IN ({q}) AND client IN ({nq})
                 ORDER BY sold_at, msg_id""", (*ids, *names)).fetchall()
    else:
        nq = ",".join("?" * len(names))
        rows = conn.execute(
            f"""SELECT msg_id, doc_no, kind, cashier, client, sold_at, day,
                       payable, paid_debt, client_debt
                  FROM receipts WHERE client IN ({nq})
                 ORDER BY sold_at DESC, msg_id DESC LIMIT ?""",
            (*names, limit)).fetchall()
        rows = list(reversed(rows))

    out = []
    for r in rows:
        items = conn.execute(
            """SELECT line_no, name, qty, price, amount FROM receipt_items
                WHERE msg_id = ? ORDER BY line_no""", (r["msg_id"],)).fetchall()
        out.append({
            "at": r["sold_at"], "doc_no": r["doc_no"] or "",
            "kind": _KIND.get(r["kind"], r["kind"]),
            "client": (r["client"] or "").strip(),
            "payable": r["payable"] or 0, "debt": r["paid_debt"] or 0,
            "client_debt": r["client_debt"],
            "items": [{"name": i["name"], "qty": i["qty"],
                       "price": i["price"], "amount": i["amount"]}
                      for i in items],
        })
    return out


def _file(path: str):
    import os as _os

    if not path or not _os.path.exists(path):
        from bot.web_api import _err
        return _err("Fayl tayyorlanmadi")
    return web.FileResponse(path, headers={
        "Content-Disposition":
            f'attachment; filename="{_os.path.basename(path)}"',
    })


@DEBT_ROUTES.get("/api/my/excel")
async def api_my_excel(request: web.Request):
    """Mijozning o'z xaridlari yoki hisob-kitobi — Excel."""
    from bot import debt_export
    from bot.web_api import _err

    auth, names, err = await _identity(request)
    if err is not None:
        return err
    if not names:
        return _err("Hisobingiz hali bog'lanmagan", 404)

    kind = (request.query.get("type") or "receipts").strip().lower()
    try:
        # Nechta chek / harakat kerak. 0 yoki yo'q — hammasi.
        count = max(0, min(60, int(request.query.get("count") or 0)))
    except ValueError:
        count = 0
    D = _debt()
    with _mdb() as mdb:
        if kind in ("cheklar_png", "receipts_png"):
            # Oxirgi `count` ta chek (sukut 10) — tovarlari bilan bitta rasmda.
            recs = _collect(mdb.conn, names, limit=count or 10)
            if not recs:
                return _err("Chek topilmadi", 404)
            path = debt_export.receipts_png(names[0], recs)
        elif kind == "png":
            # Android'da .xlsx ko'pincha ochilmaydi — rasm har qanday
            # telefonda darrov ochiladi.
            rows, total = [], 0.0
            for n in names:
                rows += D.ledger(mdb.conn, n)
                total += D.debt(mdb.conn, n)
            rows.sort(key=lambda r: r["at"] or "")
            path = debt_export.ledger_png(names[0], rows, total,
                                          limit=count or 40)
        elif kind == "ledger":
            rows, total = [], 0.0
            for n in names:
                rows += D.ledger(mdb.conn, n)
                total += D.debt(mdb.conn, n)
            rows.sort(key=lambda r: r["at"] or "")
            path = debt_export.ledger_xlsx(names[0], rows, total)
        else:
            path = debt_export.receipts_xlsx(
                _collect(mdb.conn, names), names[0])
    return _file(path)


@DEBT_ROUTES.get("/api/debt/excel")
async def api_debt_excel(request: web.Request):
    """Admin: tanlangan cheklarni BITTA Excel faylga birlashtiradi.

    GET — chunki fayl brauzerda havola orqali ochiladi va `<a download>` ga
    maxsus sarlavha qo'shib bo'lmaydi (shu sababli `initData` ham query'da
    keladi — Mini App'dagi eksport shunday ishlaydi).

    `ids` berilmasa — mijozning oxirgi cheklari olinadi. `type=ledger` esa
    hisob-kitob daftarini chiqaradi.
    """
    from database.channel_db import db as _db_pos
    from bot import debt_export
    from bot.web_api import _need_admin, _err

    auth, err = await _need_admin(request, _db_pos, "export")
    if err is not None:
        return err

    name = (request.query.get("name") or "").strip()
    if not name:
        return _err("Mijoz tanlanmadi")
    ids = [int(x) for x in (request.query.get("ids") or "").split(",")
           if x.strip().lstrip("-").isdigit()]
    kind = (request.query.get("type") or "receipts").strip().lower()

    D = _debt()
    with _mdb() as mdb:
        D.ensure_schema(mdb.conn)
        if kind == "ledger":
            path = debt_export.ledger_xlsx(
                name, D.ledger(mdb.conn, name), D.debt(mdb.conn, name))
        elif kind == "ledger_png":
            path = debt_export.ledger_png(
                name, D.ledger(mdb.conn, name), D.debt(mdb.conn, name))
        elif kind == "receipts_png":
            recs = _collect(mdb.conn, [name], ids)
            if not recs:
                return _err("Chek topilmadi")
            path = debt_export.receipts_png(name, recs)
        else:
            recs = _collect(mdb.conn, [name], ids)
            if not recs:
                return _err("Chek topilmadi")
            path = debt_export.receipts_xlsx(recs, name)
    return _file(path)


# ─── Admin: mijozga yuborish ────────────────────────────────────────────────

# Oxirgi yuborish xatosi — adminga sabab ko'rsatish uchun.
_LAST_ERROR = {"why": ""}


async def _tg_send(chat_id: str, text: str) -> bool:
    """Botdan mijozga xabar. Xato bo'lsa `False` — chaqiruvchi sanab boradi.

    Yuborish `bot/tg_out.py` orqali ketadi: mijoz asosiy botda ham,
    «Cheklar boti»da (@cheklars_bot) ham bo'lishi mumkin, shuning uchun
    ikkala token sinaladi. Ilgari faqat asosiy token ishlatilardi va
    kassadan kelgan mijozlarga xabar JIM yo'qolardi.

    XATO JIM QOLMAYDI (2026-10-28): sabab jurnalga ham, `_LAST_ERROR` ga
    ham yoziladi — «mijozga xabar bormayapti» degan shikoyatda nima
    bo'lganini (bloklagan, chat topilmadi) ko'rsatib berish uchun.
    """
    from bot import tg_out

    ok = await tg_out.send_text(chat_id, text)
    if not ok:
        _LAST_ERROR["why"] = tg_out.last_error()
    return ok


def _receipt_text(rec: dict) -> str:
    """Bitta chekning Telegram uchun matni."""
    lines = [f"🧾 <b>{rec['kind'].capitalize()}</b>"
             + (f" №{rec['doc_no']}" if rec["doc_no"] else ""),
             f"🗓 {rec['at']}", ""]
    for i, it in enumerate(rec["items"], 1):
        qty = it["qty"]
        qty_s = f"{qty:g}"
        lines.append(f"{i}) {it['name']}\n     {qty_s} × "
                     f"{round(it['price']):,} = {round(it['amount']):,}"
                     .replace(",", " "))
    lines.append("")
    lines.append(f"💵 Jami: <b>{round(rec['payable']):,}</b> so'm"
                 .replace(",", " "))
    if rec.get("debt"):
        lines.append(f"📕 Qarzga: {round(rec['debt']):,}".replace(",", " "))
    if rec.get("client_debt") is not None:
        lines.append(f"📊 Qarz qoldig'i: {round(rec['client_debt']):,}"
                     .replace(",", " "))
    return "\n".join(lines)


@DEBT_ROUTES.post("/api/debt/send")
async def api_debt_send(request: web.Request):
    """Admin: tanlangan cheklarni mijozning Telegramiga yuboradi.

    Mijoz botda ro'yxatdan o'tgan bo'lishi SHART (`chat_id`) — bot /start
    bosmagan odamga yoza olmaydi. O'tmagan bo'lsa shunday deb aytiladi,
    jim qolmaydi.
    """
    from database.channel_db import db as _db_pos
    from bot.web_api import _need_admin, _err, _read_json

    auth, err = await _need_admin(request, _db_pos, "clients_view")
    if err is not None:
        return err

    body = await _read_json(request)
    name = (body.get("name") or "").strip()
    ids = [int(x) for x in (body.get("ids") or []) if str(x).lstrip("-").isdigit()]
    if not name:
        return _err("Mijoz tanlanmadi")
    if not ids:
        return _err("Chek tanlanmadi")
    if len(ids) > 20:
        return _err("Bir martada 20 tagacha chek yuborish mumkin")

    D = _debt()
    with _mdb() as mdb:
        D.ensure_schema(mdb.conn)
        row = mdb.conn.execute(
            "SELECT chat_id FROM debt_clients WHERE name = ?", (name,)).fetchone()
        chat_id = (row["chat_id"] if row else "") or ""
        recs = _collect(mdb.conn, [name], ids)
        debt_now = round(D.debt(mdb.conn, name))

    if not chat_id:
        return _err("Mijoz botda ro'yxatdan o'tmagan — unga yozib bo'lmaydi")
    if not recs:
        return _err("Chek topilmadi")

    _LAST_ERROR["why"] = ""
    sent = 0
    for rec in recs:
        ok = await _tg_send(chat_id, _receipt_text(rec))
        sent += 1 if ok else 0
    if len(recs) > 1:
        jami = sum(float(r["payable"] or 0) for r in recs)
        await _tg_send(
            chat_id,
            f"📦 Yuborildi: {len(recs)} ta chek\n"
            f"💵 Jami: <b>{round(jami):,}</b> so'm\n"
            f"📕 Joriy qarz: <b>{debt_now:,}</b> so'm".replace(",", " "))

    if sent == 0:
        # Jim «ok» qaytarmaymiz: admin yuborildi deb o'ylab, mijoz esa
        # hech narsa olmay qolardi.
        return _err("Telegram xabarni qabul qilmadi: "
                    + (_LAST_ERROR["why"] or "noma'lum sabab"))
    return web.json_response({"ok": True, "sent": sent, "of": len(recs)})


@DEBT_ROUTES.post("/api/debt/remind")
async def api_debt_remind(request: web.Request):
    """Admin: mijozga qarz eslatmasini yuboradi.

    QAROR ADMINNIKI. Bot o'zi hech kimga eslatma yubormaydi — Mini App'da
    tugma bosiladi, matn shu yerda tuziladi. Eslatmasi o'chirilgan mijozga
    (`remind_open = 0`) yuborilmaydi: egasi ataylab shunday qo'ygan.
    """
    from database.channel_db import db as _db_pos
    from bot.web_api import _need_admin, _err, _read_json

    auth, err = await _need_admin(request, _db_pos, "clients_pay")
    if err is not None:
        return err

    body = await _read_json(request)
    name = (body.get("name") or "").strip()
    extra = (body.get("text") or "").strip()
    # via: bot — faqat botdan; userbot — shaxsiy akkauntdan (navbatga);
    # auto — botda bo'lsa bot, bo'lmasa userbot.
    via = (body.get("via") or "auto").strip().lower()
    if not name:
        return _err("Mijoz tanlanmadi")

    D = _debt()
    with _mdb() as mdb:
        D.ensure_schema(mdb.conn)
        row = mdb.conn.execute(
            "SELECT chat_id, phone, phone2, remind_open FROM debt_clients "
            "WHERE name = ?", (name,)).fetchone()
        chat_id = (row["chat_id"] if row else "") or ""
        phone = ((row["phone"] if row else "") or
                 (row["phone2"] if row else "") or "")
        quiet = row is not None and int(row["remind_open"]) == 0
        debt_now = round(D.debt(mdb.conn, name))

    if quiet:
        return _err("Bu mijozga eslatma o'chirilgan")
    if debt_now <= 0:
        return _err("Mijozning qarzi yo'q")

    text = _remind_text(debt_now, extra)

    # ── Bot orqali (xavfsiz, cheklovsiz) ────────────────────────────────
    if chat_id and via in ("auto", "bot"):
        ok = await _tg_send(chat_id, text)
        return web.json_response({"ok": bool(ok), "via": "bot",
                                  "debt": debt_now})
    if via == "bot":
        return _err("Mijoz botda ro'yxatdan o'tmagan — bot yoza olmaydi")

    # ── Userbot orqali (navbatga) ───────────────────────────────────────
    if not phone:
        return _err("Telefon raqami yo'q — yuborib bo'lmaydi")

    import re as _re

    plain = _re.sub(r"<[^>]+>", "", text)      # userbot HTML ishlatmaydi
    with _mdb() as mdb:
        from marketplace import tg_notify as N

        qid = N.enqueue(mdb, name, phone, plain, kind="debt",
                        author=str(auth["tg_id"]))
        st = N.status(mdb)
    if not qid:
        return _err("Bu mijoz allaqachon navbatda turibdi")
    return web.json_response({
        "ok": True, "via": "userbot", "debt": debt_now, "queued": qid,
        "pending": st.get("pending", 0), "today": st.get("today", 0),
        "limit": st.get("limit", 0),
    })


def _remind_text(debt_now: int, extra: str = "") -> str:
    from bot.config import CONTACT_PHONE_1

    text = (f"Assalomu alaykum! 👋\n\n"
            f"Chinor do'konidan eslatma: hisobingizda "
            f"<b>{debt_now:,}</b> so'm qarz turibdi.".replace(",", " "))
    if extra:
        text += f"\n\n{extra}"
    text += (f"\n\nTo'lov yoki savol bo'lsa: {CONTACT_PHONE_1}\n"
             f"Xaridlaringizni Mini App'dan ko'rishingiz mumkin.")
    return text


@DEBT_ROUTES.post("/api/debt/remind/bulk")
async def api_debt_remind_bulk(request: web.Request):
    """Bir nechta qarzdorga eslatma: botdagilarga bot, qolganiga userbot.

    Shaxsiy akkauntdan ketma-ket yozish Telegram spam-qo'riqchisini
    uyg'otadi, shuning uchun userbot qismi NAVBATGA tushadi va fonda
    bittalab, tanaffus bilan ketadi (kunlik chegara bilan). Bu yerda
    faqat navbatga qo'yiladi — hech narsa darhol yuborilmaydi.
    """
    from database.channel_db import db as _db_pos
    from bot.web_api import _need_admin, _err, _read_json

    auth, err = await _need_admin(request, _db_pos, "clients_pay")
    if err is not None:
        return err

    body = await _read_json(request)
    names = [str(n).strip() for n in (body.get("names") or []) if str(n).strip()]
    try:
        min_debt = float(body.get("min") or 0)
    except (TypeError, ValueError):
        min_debt = 0.0
    extra = (body.get("text") or "").strip()

    D = _debt()
    import re as _re

    yuborildi = navbat = otkazildi = 0
    sabablar = {}
    with _mdb() as mdb:
        from marketplace import tg_notify as N

        D.ensure_schema(mdb.conn)
        N.ensure_schema(mdb)
        if not names:
            names = [r["name"] for r in D.clients_with_debt(mdb.conn, max(1.0, min_debt))]

        tasks = []
        for name in names:
            row = mdb.conn.execute(
                "SELECT chat_id, phone, phone2, remind_open FROM debt_clients "
                "WHERE name = ?", (name,)).fetchone()
            qarz = round(D.debt(mdb.conn, name))
            if qarz < max(1, min_debt):
                otkazildi += 1
                sabablar[name] = "qarzi yo'q"
                continue
            if row is not None and int(row["remind_open"]) == 0:
                otkazildi += 1
                sabablar[name] = "eslatma o'chiq"
                continue
            chat_id = (row["chat_id"] if row else "") or ""
            phone = ((row["phone"] if row else "") or
                     (row["phone2"] if row else "") or "")
            text = _remind_text(qarz, extra)
            if chat_id:
                tasks.append((chat_id, text))
                continue
            if not phone:
                otkazildi += 1
                sabablar[name] = "telefon yo'q"
                continue
            if N.enqueue(mdb, name, phone, _re.sub(r"<[^>]+>", "", text),
                         kind="debt", author=str(auth["tg_id"])):
                navbat += 1
            else:
                otkazildi += 1
                sabablar[name] = "allaqachon navbatda"
        st = N.status(mdb)

    # Botdagilarga DARHOL — bot uchun cheklov yo'q.
    for chat_id, text in tasks:
        if await _tg_send(chat_id, text):
            yuborildi += 1

    return web.json_response({
        "ok": True, "sent": yuborildi, "queued": navbat,
        "skipped": otkazildi, "reasons": sabablar,
        "pending": st.get("pending", 0), "today": st.get("today", 0),
        "limit": st.get("limit", 0),
    })


@DEBT_ROUTES.get("/api/debt/notify/status")
async def api_debt_notify_status(request: web.Request):
    """Userbot navbati holati — Mini App shu raqamlarni ko'rsatadi."""
    from database.channel_db import db as _db_pos
    from bot.web_api import _need_admin

    auth, err = await _need_admin(request, _db_pos, "clients_view")
    if err is not None:
        return err

    with _mdb() as mdb:
        from marketplace import tg_notify as N

        st = N.status(mdb)
        rows = mdb.conn.execute(
            """SELECT client, phone, status, error, created_at, sent_at
                 FROM notify_queue ORDER BY id DESC LIMIT 30""").fetchall()
    return web.json_response({
        "ok": True, "stats": st,
        "items": [{"client": r["client"], "phone": r["phone"],
                   "status": r["status"], "error": r["error"] or "",
                   "at": r["sent_at"] or r["created_at"]} for r in rows],
    })


# ─── Fayl yetkazish: Telegramga, brauzerdan chiqmasdan ──────────────────────
# ILGARI QANDAY EDI. Mini App'dagi «Excel» va «Rasm» tugmalari havolani
# BRAUZERDA ochardi: Telegram ilovasidan chiqib ketiladi, fayl «Downloads»
# ga tushadi va uni mijozga yuborish uchun yana qidirish kerak bo'lardi.
# ENDI: fayl shu yerda yasaladi va to'g'ridan-to'g'ri Telegramga yuboriladi —
# o'ziga (`to=me`) yoki mijozga (`to=client`).

_FMT = ("pdf", "png", "xlsx")


def _build_file(conn, name: str, ids, kind: str, fmt: str):
    """(yo'l, as_photo) — so'ralgan shakldagi fayl."""
    from bot import debt_export

    D = _debt()
    if kind == "ledger":
        rows = D.ledger(conn, name)
        qarz = D.debt(conn, name)
        if fmt == "pdf":
            return debt_export.ledger_pdf(name, rows, qarz), False
        if fmt == "png":
            return debt_export.ledger_png(name, rows, qarz), True
        return debt_export.ledger_xlsx(name, rows, qarz), False

    recs = _collect(conn, [name], ids)
    if not recs:
        return "", False
    if fmt == "pdf":
        return debt_export.receipts_pdf(name, recs), False
    if fmt == "png":
        return debt_export.receipts_png(name, recs), True
    return debt_export.receipts_xlsx(recs, name), False


@DEBT_ROUTES.post("/api/debt/deliver")
async def api_debt_deliver(request: web.Request):
    """Admin: cheklarni/daftarni PDF, rasm yoki Excel qilib Telegramga yuboradi.

    Body: {name, ids[], kind: receipts|ledger, fmt: pdf|png|xlsx, to: me|client}
    """
    from database.channel_db import db as _db_pos
    from bot import tg_out
    from bot.web_api import _need_admin, _err, _read_json

    auth, err = await _need_admin(request, _db_pos, "clients_view")
    if err is not None:
        return err

    body = await _read_json(request)
    name = (body.get("name") or "").strip()
    kind = (body.get("kind") or "receipts").strip().lower()
    fmt = (body.get("fmt") or "pdf").strip().lower()
    to = (body.get("to") or "me").strip().lower()
    ids = [int(x) for x in (body.get("ids") or [])
           if str(x).lstrip("-").isdigit()]
    if not name:
        return _err("Mijoz tanlanmadi")
    if fmt not in _FMT:
        return _err("Fayl turi noto'g'ri")
    if kind not in ("receipts", "ledger"):
        return _err("Hisobot turi noto'g'ri")
    if len(ids) > 60:
        return _err("Bir martada 60 tagacha chek")

    D = _debt()
    with _mdb() as mdb:
        D.ensure_schema(mdb.conn)
        row = mdb.conn.execute(
            "SELECT chat_id FROM debt_clients WHERE name = ?", (name,)).fetchone()
        chat_id = (row["chat_id"] if row else "") or ""
        qarz = round(D.debt(mdb.conn, name))
        path, as_photo = _build_file(mdb.conn, name, ids, kind, fmt)

    if not path:
        return _err("Chek topilmadi")

    if to == "client":
        if not chat_id:
            return _err("Mijoz botda ro'yxatdan o'tmagan — unga yozib bo'lmaydi")
        nishon = chat_id
        caption = ("🧾 <b>Chinor</b> — xaridlaringiz" if kind == "receipts"
                   else "💰 <b>Chinor</b> — hisob-kitob daftaringiz")
        if ids and kind == "receipts":
            caption += f"\n{len(ids)} ta chek"
        if qarz > 0:
            caption += f"\n📕 Joriy qarz: <b>{qarz:,}</b> so'm".replace(",", " ")
    else:
        nishon = auth["tg_id"]
        caption = f"📎 <b>{name}</b> · " + ("cheklar" if kind == "receipts"
                                            else "hisob-kitob")

    ok = await tg_out.send_file(nishon, path, caption=caption,
                                as_photo=as_photo)
    if not ok:
        return _err("Telegramga yuborilmadi: "
                    + (tg_out.last_error()
                       or "botga /start bosilganini tekshiring"))
    return web.json_response({"ok": True, "to": to, "fmt": fmt,
                              "chat_id": str(nishon)})


@DEBT_ROUTES.post("/api/my/deliver")
async def api_my_deliver(request: web.Request):
    """Mijoz: o'z chekini/daftarini Telegramga fayl qilib oladi.

    Brauzerga chiqish shart emas — fayl shu suhbatga keladi.
    Body: {kind: receipts|ledger, fmt: pdf|png|xlsx, count?}
    """
    from bot import debt_export, tg_out
    from bot.web_api import _err, _read_json

    auth, names, err = await _identity(request)
    if err is not None:
        return err
    if not names:
        return _err("Hisobingiz hali bog'lanmagan", 404)

    body = await _read_json(request)
    kind = (body.get("kind") or "receipts").strip().lower()
    fmt = (body.get("fmt") or "pdf").strip().lower()
    try:
        count = max(0, min(60, int(body.get("count") or 0)))
    except (TypeError, ValueError):
        count = 0
    if fmt not in _FMT:
        return _err("Fayl turi noto'g'ri")

    D = _debt()
    as_photo = False
    with _mdb() as mdb:
        if kind == "ledger":
            rows, jami = [], 0.0
            for n in names:
                rows += D.ledger(mdb.conn, n)
                jami += D.debt(mdb.conn, n)
            rows.sort(key=lambda r: r["at"] or "")
            if fmt == "pdf":
                path = debt_export.ledger_pdf(names[0], rows, jami)
            elif fmt == "png":
                path, as_photo = debt_export.ledger_png(names[0], rows, jami), True
            else:
                path = debt_export.ledger_xlsx(names[0], rows, jami)
            caption = "💰 Hisob-kitob daftaringiz"
        else:
            recs = _collect(mdb.conn, names, limit=count or 10)
            if not recs:
                return _err("Chek topilmadi", 404)
            if fmt == "pdf":
                path = debt_export.receipts_pdf(names[0], recs)
            elif fmt == "png":
                path, as_photo = debt_export.receipts_png(names[0], recs), True
            else:
                path = debt_export.receipts_xlsx(recs, names[0])
            caption = f"🧾 Oxirgi {len(recs)} ta xaridingiz"

    ok = await tg_out.send_file(auth["tg_id"], path, caption=caption,
                                as_photo=as_photo)
    if not ok:
        return _err("Telegramga yuborilmadi: "
                    + (tg_out.last_error() or "botga /start bosing"))
    return web.json_response({"ok": True, "fmt": fmt, "kind": kind})


def setup(app: web.Application) -> None:
    app.add_routes(DEBT_ROUTES)
