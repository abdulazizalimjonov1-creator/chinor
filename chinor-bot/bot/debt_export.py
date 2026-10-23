"""Chek va qarz hisobotini Excel qilish.

ChekHisobot'dagi asosiy qulaylik ko'chirildi: chek qatori yonidagi «+» ni
bosganda o'sha chekning TOVARLARI ochiladi (qatorlarni guruhlash / outline).
Shunda bir ekranda o'nlab chek ko'rinadi, kerakligini ochib ko'rasiz.

DIQQAT (ChekHisobot izohidan): telefondagi Excel va Telegram ko'ruvchisi
guruhlash tugmalarini ko'rsatmaydi — shuning uchun tovarlar sukut bo'yicha
OCHIQ turadi. Kompyuterda chap chetdagi «1» bilan yig'ib qo'ysa bo'ladi.
"""

import os
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from database.channel_db import DB_PATH

_OUT_DIR = os.path.join(os.path.dirname(DB_PATH), "exports")
os.makedirs(_OUT_DIR, exist_ok=True)

_HEAD_FILL = PatternFill("solid", fgColor="1F6F43")
_HEAD_FONT = Font(bold=True, color="FFFFFF")
_CHEK_FILL = PatternFill("solid", fgColor="EAF3EC")
_MONEY = "#,##0"

_COLS = ["Sana", "Vaqt", "Chek", "Tovar / izoh", "Miqdor", "Narx",
         "Summa", "Qarzga", "Qoldiq"]


def _safe(name: str) -> str:
    """Fayl nomiga yaroqli ko'rinish."""
    keep = [c if (c.isalnum() or c in " -_") else "_" for c in (name or "")]
    return "".join(keep).strip().replace(" ", "_")[:40] or "mijoz"


def _sheet(wb: Workbook, title: str):
    ws = wb.active
    ws.title = (title or "Cheklar")[:31]
    # Chek qatori guruhning TEPASIDA turadi, tovarlar ostida — shuning uchun
    # «summary below» o'chiriladi, aks holda Excel «+» ni oxirgi tovardan
    # keyin chizadi va qaysi chekniki ekani ko'rinmaydi.
    ws.sheet_properties.outlinePr.summaryBelow = False
    for i, h in enumerate(_COLS, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.fill, c.font = _HEAD_FILL, _HEAD_FONT
        c.alignment = Alignment(horizontal="center", vertical="center")
    ws.freeze_panes = "A2"
    for i, w in enumerate([11, 7, 9, 44, 9, 12, 13, 12, 13], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    return ws


def _money_cell(ws, row, col, value):
    c = ws.cell(row=row, column=col, value=round(float(value or 0)))
    c.number_format = _MONEY
    return c


def receipts_xlsx(rows, title: str, path: str = "") -> str:
    """`rows` — har biri {chek, tovarlar} lug'ati. Faylning yo'lini qaytaradi.

    Kutilgan shakl:
        {"at", "doc_no", "kind", "payable", "debt", "client_debt",
         "client", "items": [{"name","qty","price","amount"}]}
    """
    wb = Workbook()
    ws = _sheet(wb, title or "Cheklar")

    r = 2
    jami = qarzga = 0.0
    for rec in rows:
        at = (rec.get("at") or "")
        sana, vaqt = (at.split(" ") + [""])[:2]
        ws.cell(row=r, column=1, value=sana)
        ws.cell(row=r, column=2, value=vaqt[:5])
        ws.cell(row=r, column=3, value=rec.get("doc_no") or "")
        izoh = rec.get("kind") or ""
        if rec.get("client"):
            izoh = f"{izoh} · {rec['client']}"
        ws.cell(row=r, column=4, value=izoh)
        _money_cell(ws, r, 7, rec.get("payable"))
        _money_cell(ws, r, 8, rec.get("debt"))
        if rec.get("client_debt") is not None:
            _money_cell(ws, r, 9, rec["client_debt"])
        for col in range(1, 10):
            ws.cell(row=r, column=col).fill = _CHEK_FILL
            ws.cell(row=r, column=col).font = Font(bold=True)
        jami += float(rec.get("payable") or 0)
        qarzga += float(rec.get("debt") or 0)
        r += 1

        for it in rec.get("items") or []:
            ws.cell(row=r, column=4, value=it.get("name") or "")
            ws.cell(row=r, column=5, value=it.get("qty") or 0)
            _money_cell(ws, r, 6, it.get("price"))
            _money_cell(ws, r, 7, it.get("amount"))
            # Guruhlash: chek qatori ostidagi tovarlar yig'iladi/ochiladi.
            ws.row_dimensions[r].outline_level = 1
            ws.row_dimensions[r].hidden = False
            r += 1

    ws.cell(row=r + 1, column=4, value="JAMI").font = Font(bold=True)
    _money_cell(ws, r + 1, 7, jami).font = Font(bold=True)
    _money_cell(ws, r + 1, 8, qarzga).font = Font(bold=True)

    if not path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(_OUT_DIR, f"cheklar-{_safe(title)}-{stamp}.xlsx")
    wb.save(path)
    return path


def ledger_xlsx(name: str, rows, debt_now: float, path: str = "") -> str:
    """Mijozning hisob-kitob daftari — bitta varaq."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Hisob-kitob"
    heads = ["Sana", "Turi", "Hujjat", "Izoh", "O'zgarish", "Qoldiq"]
    for i, h in enumerate(heads, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.fill, c.font = _HEAD_FILL, _HEAD_FONT
    ws.freeze_panes = "A2"
    for i, w in enumerate([19, 12, 10, 44, 14, 14], 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    _TUR = {"receipt": "Xarid", "payment": "To'lov", "manual": "Qo'lda",
            "fix": "Tuzatish", "opening": "Boshlang'ich"}
    r = 2
    for row in rows:
        ws.cell(row=r, column=1, value=row.get("at") or "")
        ws.cell(row=r, column=2, value=_TUR.get(row.get("type"), row.get("type")))
        ws.cell(row=r, column=3, value=row.get("doc_no") or "")
        ws.cell(row=r, column=4, value=row.get("note") or "")
        _money_cell(ws, r, 5, row.get("change"))
        _money_cell(ws, r, 6, row.get("balance"))
        r += 1

    ws.cell(row=r + 1, column=4, value="JORIY QARZ").font = Font(bold=True)
    _money_cell(ws, r + 1, 6, debt_now).font = Font(bold=True)

    if not path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(_OUT_DIR, f"hisob-{_safe(name)}-{stamp}.xlsx")
    wb.save(path)
    return path
