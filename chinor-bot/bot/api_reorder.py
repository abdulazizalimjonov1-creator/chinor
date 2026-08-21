"""Buyurtma ro'yxati — HTTP API (Mini App «📋 Buyurtma» bo'limi uchun).

Ish bo'linishi: HISOB-KITOB bu yerda EMAS. Tezlik va miqdor `marketplace`
paketida hisoblanadi (`velocity.py` + `reorder.py`) — bir xil raqam terminalda
ham (`python -m marketplace.reorder`), Mini App'da ham chiqishi kerak, shuning
uchun formula BITTA joyda turadi. Bu modul faqat: ruxsatni tekshiradi,
natijani JSON qiladi va Excel'ga yozadi.

`marketplace` alohida paket (repo ildizida, `chinor-bot` yonida) va botga
bog'liq emas — shuning uchun import qilishdan oldin repo ildizi `sys.path` ga
qo'shiladi. Paketning o'zi faqat stdlib + requests/dotenv ishlatadi.

Endpointlar:
  GET /api/reorder/suppliers          — kompaniyalar + qisqa xulosa
  GET /api/reorder?supplier_id=N      — bitta kompaniyaning to'liq ro'yxati
  GET /api/reorder/export?supplier_id=N — Excel fayl (to'g'ridan yuklanadi)
"""

import os
import sys

from aiohttp import web

REORDER_ROUTES = web.RouteTableDef()

# Repo ildizi (chinor-bot ning ota papkasi) — `marketplace` shu yerda.
_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


def _marketplace():
    """`marketplace` modullarini kech import qiladi.

    Kech, chunki: (1) bot ishga tushishi bu bo'limga bog'liq bo'lmasin —
    marketplace o'rnatilmagan bo'lsa ham bot ishlayversin; (2) `sys.path` ga
    qo'shish faqat kerak bo'lganda bajarilsin.
    """
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)
    from marketplace import reorder as _reorder
    from marketplace.db import MarketplaceDB

    return _reorder, MarketplaceDB


def _period_label(win: dict) -> str:
    """«2026-07-01 … 2026-07-31» yoki «oxirgi 30 kun» — hisobotda ko'rinadi."""
    if win.get("since") or win.get("until"):
        _reorder, _ = _marketplace()
        from marketplace.velocity import window as _w
        a, b = _w(win.get("days", 30), win.get("since", ""), win.get("until", ""))
        return f"{a} … {b}"
    return f"oxirgi {win.get('days', 30)} kun"


def _line_json(l) -> dict:
    r = l.rate
    return {
        "sku": r.sku,
        "name": r.name,
        "status": l.status,
        "need": l.need,
        "rate": round(r.rate, 2),
        "stock": round(r.stock, 1),
        "days_left": None if r.days_left == float("inf") else round(r.days_left, 1),
        "amount": round(l.amount),
        "cost": round(l.cost),
        "confidence": r.confidence,
        "thin": r.thin,
        "source": r.source,
        "rate_all": round(r.rate_all, 2),
        "bulk_count": r.bulk_count,
        "bulk_qty": round(r.bulk_qty),
        "bulk_note": r.bulk_note,
        "spike": r.spike,
        "lost_per_day": round(l.lost_per_day),
    }


def _window(request) -> dict:
    """So'rovdan davrni oladi: ?days=30 yoki ?from=YYYY-MM-DD&to=YYYY-MM-DD."""
    q = request.query
    try:
        days = max(1, min(365, int(q.get("days") or 30)))
    except ValueError:
        days = 30
    ok = lambda v: v if (v or "").count("-") == 2 and len(v) == 10 else ""
    return {"days": days, "since": ok(q.get("from")), "until": ok(q.get("to"))}


def _collect(supplier_id: int = None, days: int = 30,
             since: str = "", until: str = ""):
    """(guruhlar, ogohlantirish) — hisobni bir marta bajaradi."""
    _reorder, MarketplaceDB = _marketplace()
    from marketplace import velocity as _v

    with MarketplaceDB() as mdb:
        history_days = mdb.snapshot_days()
        a, b = _v.window(days, since, until)
        have = _v.data_days(mdb, a, b)
        lines = _reorder.build(mdb, days=days, since=since, until=until)
    groups = _reorder.group_by_supplier(lines)

    warn = ""
    if have < 3:
        # Tanlangan oynada ma'lumot deyarli yo'q — bo'sh ro'yxatning sababi
        # shu, «ishlamayapti» emas. Aniq aytamiz.
        warn = (f"«{a} … {b}» oralig'ida atigi {have} kunlik savdo bor — "
                "ro'yxat shuning uchun bo'sh/kam chiqadi. Boshqa davr tanlang.")
    elif have < 7:
        warn = (f"«{a} … {b}»: {have} kunlik ma'lumot — raqamlar taxminiy.")
    elif history_days < 7:
        warn = (f"Atigi {history_days} kunlik qoldiq tarixi bor — "
                "raqamlar taxminiy.")
    if supplier_id is not None:
        groups = {supplier_id: groups.get(supplier_id, [])}
    return groups, warn


@REORDER_ROUTES.get("/api/reorder/suppliers")
async def api_reorder_suppliers(request: web.Request):
    """Kompaniyalar ro'yxati + har biriga qisqa xulosa (nechta pozitsiya,
    qancha summa, kuniga qancha savdo yo'qolmoqda). Ruxsat: 'suppliers'."""
    from database.channel_db import db as _db
    from bot.web_api import _need_admin, _err

    auth, err = await _need_admin(request, _db, "suppliers")
    if err is not None:
        return err

    try:
        groups, warn = _collect(**_window(request))
    except Exception as e:
        return _err(f"Hisob bajarilmadi: {e}")

    _reorder, _ = _marketplace()
    out = []
    for sid, group in groups.items():
        if not sid:
            continue
        need = [l for l in group if l.need > 0]
        out.append({
            "id": sid,
            "name": group[0].supplier or f"#{sid}",
            "lead": group[0].lead,
            "cycle": group[0].cycle,
            "products": len(group),
            "urgent": sum(1 for l in need if l.status == _reorder.URGENT),
            "to_order": len(need),
            # Qoldig'i 0 ga tushgan, lekin buyurtma hisoblanmaganlar — Mini App
            # ularni ko'k belgi bilan alohida ko'rsatadi.
            "zero_stock": sum(1 for l in group if l.need <= 0 and l.rate.stock <= 0),
            "amount": round(sum(l.amount for l in need)),
            "lost_per_day": round(sum(l.lost_per_day for l in group)),
        })
    out.sort(key=lambda s: (-s["urgent"], -s["amount"]))

    unassigned = groups.get(0) or []
    return web.json_response({
        "ok": True,
        "suppliers": out,
        "unassigned": len([l for l in unassigned if l.need > 0]),
        "warning": warn,
    })


@REORDER_ROUTES.get("/api/reorder")
async def api_reorder_list(request: web.Request):
    """Bitta kompaniyaning to'liq ro'yxati. Ruxsat: 'suppliers'."""
    from database.channel_db import db as _db
    from bot.web_api import _need_admin, _err

    auth, err = await _need_admin(request, _db, "suppliers")
    if err is not None:
        return err

    sid = int(request.query.get("supplier_id") or 0)
    win = _window(request)
    try:
        groups, warn = _collect(supplier_id=sid, **win)
    except Exception as e:
        return _err(f"Hisob bajarilmadi: {e}")

    group = groups.get(sid) or []
    if not group:
        return web.json_response({"ok": True, "items": [], "warning": warn,
                                  "name": "", "total": 0})

    need = [l for l in group if l.need > 0]
    return web.json_response({
        "ok": True,
        "name": group[0].supplier or f"#{sid}",
        "lead": group[0].lead,
        "cycle": group[0].cycle,
        "warning": warn,
        "period": _period_label(win),
        "total": round(sum(l.amount for l in need)),
        "lost_per_day": round(sum(l.lost_per_day for l in group)),
        # Buyurtma kerak bo'lganlar tepada — qolganlari «nega kerak emas»ni
        # ko'rsatish uchun quyida turadi.
        "items": [_line_json(l) for l in group],
    })


@REORDER_ROUTES.get("/api/reorder/export")
async def api_reorder_export(request: web.Request):
    """Excel fayl — `/api/export` bilan bir xil naqsh (to'g'ridan yuklanadi).
    Ruxsat: 'suppliers'."""
    from database.channel_db import db as _db
    from bot.web_api import _need_admin, _err

    auth, err = await _need_admin(request, _db, "suppliers")
    if err is not None:
        return err

    sid = int(request.query.get("supplier_id") or 0)
    win = _window(request)
    try:
        groups, _warn = _collect(supplier_id=sid, **win)
        group = groups.get(sid) or []
        if not group:
            return _err("Bu kompaniyada tovar yo'q")
        path = _build_excel(group, win)
    except Exception as e:
        return _err(f"Fayl tayyorlanmadi: {e}")

    return web.FileResponse(path, headers={
        "Content-Disposition": f'attachment; filename="{os.path.basename(path)}"',
    })


@REORDER_ROUTES.post("/api/reorder/send")
async def api_reorder_send(request: web.Request):
    """Excel'ni SO'RAGAN adminning Telegramiga yuboradi.

    Nega yuklab olish emas: Mini App Telegram ichida ochiladi va u yerda
    yuklangan fayl ko'pincha yo'qoladi (qaysi papkaga tushgani bilinmaydi).
    Bot yuborgan hujjat esa suhbatda turadi — istalgan payt ochiladi va
    yetkazib beruvchiga o'sha yerdan uzatiladi.
    """
    from database.channel_db import db as _db
    from bot.web_api import _need_admin, _err, _read_json

    auth, err = await _need_admin(request, _db, "suppliers")
    if err is not None:
        return err

    body = await _read_json(request)
    sid = int(body.get("supplier_id") or 0)
    win = {
        "days": max(1, min(365, int(body.get("days") or 30))),
        "since": (body.get("from") or "").strip(),
        "until": (body.get("to") or "").strip(),
    }
    try:
        groups, _warn = _collect(supplier_id=sid, **win)
        group = groups.get(sid) or []
        if not group:
            return _err("Bu kompaniyada tovar yo'q")
        path = _build_excel(group, win)
    except Exception as e:
        return _err(f"Fayl tayyorlanmadi: {e}")

    bot = getattr(_db, "_bot", None)
    if not bot:
        return _err("Bot ulanmagan — fayl yuborilmadi")

    from aiogram.types import FSInputFile

    need = [l for l in group if l.need > 0]
    total = sum(l.amount for l in need)
    urgent = sum(1 for l in need if l.status == "SHOSHILINCH")
    caption = (
        f"📋 <b>{group[0].supplier}</b> — buyurtma ro'yxati\n"
        f"📦 {len(need)} pozitsiya · 💰 ~{total:,.0f} so'm\n"
        f"📅 savdo davri: {_period_label(win)}\n"
        f"⏱ muddat: {group[0].lead}+{group[0].cycle} kun"
        + (f"\n🔴 {urgent} ta tovar TUGAGAN" if urgent else "")
    ).replace(",", " ")
    try:
        await bot.send_document(
            auth["tg_id"], FSInputFile(path), caption=caption, parse_mode="HTML"
        )
    except Exception as e:
        return _err(f"Telegramga yuborilmadi: {e}")
    return web.json_response({"ok": True, "sent": True, "positions": len(need)})


def _build_excel(group: list, win: dict = None) -> str:
    """Buyurtma ro'yxatini Excel qilib yozadi. Qaytaradi: fayl yo'li.

    Ustunlar ataylab shu tartibda: agentga ko'rsatiladigan qism (nom, miqdor)
    chapda, asoslash (tezlik, qoldiq, necha kunga yetadi) o'ngda — chop etilsa
    ham birinchi ustunlar yetarli bo'ladi.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from database.channel_db import DB_PATH, now_local

    _reorder, _ = _marketplace()

    supplier = group[0].supplier or "nomalum"
    need = [l for l in group if l.need > 0]

    wb = Workbook()
    ws = wb.active
    ws.title = "Buyurtma"

    ws["A1"] = f"Buyurtma: {supplier}"
    ws["A1"].font = Font(bold=True, size=14, color="1F4E79")
    ws["A2"] = (f"{now_local().strftime('%Y-%m-%d %H:%M')} · "
                f"muddat {group[0].lead}+{group[0].cycle} kun · "
                f"{len(need)} pozitsiya · "
                f"savdo davri: {_period_label(win or {})}")
    ws["A2"].font = Font(size=10, color="666666")

    headers = ["SKU", "Nom", "BUYURTMA", "Holat", "Kunlik ketish", "Qoldiq",
               "Necha kunga yetadi", "Tannarx", "Summa", "Ishonch", "Manba",
               "Yirik savdo (hisobga olinmagan)"]
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=4, column=i, value=h)
        c.fill = PatternFill("solid", fgColor="305496")
        c.font = Font(bold=True, color="FFFFFF", size=11)
        c.alignment = Alignment(horizontal="center", vertical="center")

    red = PatternFill("solid", fgColor="FFC7CE")     # shoshilinch
    yellow = PatternFill("solid", fgColor="FFEB9C")  # buyurtma
    grey = Font(color="999999")

    row = 5
    for l in group:
        r = l.rate
        ws.cell(row=row, column=1, value=r.sku)
        ws.cell(row=row, column=2, value=r.name)
        ws.cell(row=row, column=3, value=l.need or None)
        ws.cell(row=row, column=4, value=l.status)
        ws.cell(row=row, column=5, value=round(r.rate, 2))
        ws.cell(row=row, column=6, value=round(r.stock, 1))
        ws.cell(row=row, column=7,
                value=None if r.days_left == float("inf") else round(r.days_left))
        ws.cell(row=row, column=8, value=round(l.cost))
        ws.cell(row=row, column=9, value=round(l.amount) or None)
        # «kam kuzatilgan» — tovar oynaning ozgina qismida sotuvda turgan,
        # shuning uchun tezlik bir-ikkita savdoga tayanadi.
        ws.cell(row=row, column=10,
                value=r.confidence + (" · kam kuzatilgan" if r.thin else ""))
        # Manba: «chek» = haqiqiy savdo, «qoldiq» = qoldiq kamayishidan taxmin.
        ws.cell(row=row, column=11, value=r.source)
        # Yirik (bir martalik) savdo miqdordan CHIQARILGAN — lekin ko'rinsin:
        # yana shunday xarid kutilsa, odam o'zi qo'shib qo'yadi.
        ws.cell(row=row, column=12, value=r.bulk_note or None)

        if l.status == _reorder.URGENT:
            for col in range(1, 13):
                ws.cell(row=row, column=col).fill = red
        elif l.status == _reorder.ORDER:
            for col in range(1, 13):
                ws.cell(row=row, column=col).fill = yellow
        elif l.need == 0:
            for col in range(1, 13):
                ws.cell(row=row, column=col).font = grey
        row += 1

    ws.cell(row=row + 1, column=2, value="JAMI").font = Font(bold=True)
    ws.cell(row=row + 1, column=3, value=sum(l.need for l in need)).font = Font(bold=True)
    ws.cell(row=row + 1, column=9,
            value=round(sum(l.amount for l in need))).font = Font(bold=True)

    widths = [9, 40, 11, 13, 14, 9, 18, 11, 13, 10, 9, 30]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A5"

    out_dir = os.path.join(os.path.dirname(DB_PATH), "exports")
    os.makedirs(out_dir, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in supplier).strip()
    path = os.path.join(
        out_dir, f"buyurtma-{safe}-{now_local().strftime('%Y%m%d-%H%M')}.xlsx"
    )
    wb.save(path)
    _rotate_exports(out_dir)
    return path


def _rotate_exports(out_dir: str, keep: int = 20) -> None:
    """Eski buyurtma fayllarini o'chiradi.

    Har tugma bosilganda yangi fayl yoziladi. Bittasi ~6 KB, lekin kunda
    o'nlab marta bosilsa yillar davomida yig'ilib qoladi — oxirgi `keep`
    tasidan boshqasi kerak emas (fayl darrov yuklab olinadi).
    """
    import glob

    files = sorted(glob.glob(os.path.join(out_dir, "buyurtma-*.xlsx")))
    for old in files[:-keep] if keep > 0 else files:
        try:
            os.remove(old)
        except OSError:
            pass


def setup(app: web.Application):
    """web_api.create_app() shu funksiyani chaqirib route'larni ulaydi."""
    app.add_routes(REORDER_ROUTES)
