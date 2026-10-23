"""Kassa kompyuteridagi ChekHisobot bazasini (`hisobot.db`) ko'chirish.

NIMA KO'CHADI VA NIMA KO'CHMAYDI. ChekHisobot cheklarni aynan bizdagi manbadan
oladi — @yespos_notify_bot xabarlaridan. Shuning uchun uning `cheklar` /
`tovarlar` / `qarz_tolovlari` jadvallari bizdagi `receipts` / `receipt_items` /
`debt_payments` bilan BIR XIL ma'lumot: ularni ko'chirish takror yozuv
bo'lardi. Ularni biz Telegramdan qayta o'qiymiz:

    python -m marketplace.tg_receipts sync --all --reparse

Ko'chirish kerak bo'lgani — faqat QO'LDA kiritilgani, ya'ni chekda yo'q va
qaytadan hosil qilib bo'lmaydigani:

  * `mijozlar`   → `debt_clients`  (telefon, Telegram chat_id, tashqi qarz,
                                    chegara, qulflar)
  * `harakatlar` dagi chek_id si YO'Q qatorlar → `debt_moves`
    (2026-10-23 holatiga ko'ra bunday qator yo'q, lekin keyin paydo bo'lsa)

Ishlatish:
    python -m marketplace.migrate_chekhisobot --db /yo'l/hisobot.db          # sinov
    python -m marketplace.migrate_chekhisobot --db /yo'l/hisobot.db --apply  # yozish

Sukut bo'yicha HECH NARSA YOZILMAYDI — avval nima o'zgarishini ko'rsatadi.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from typing import Dict, List

from marketplace import debt as D
from marketplace.db import MarketplaceDB

# hisobot.db ustuni → debt_clients ustuni
_CLIENT_MAP = {
    "telefon": "phone",
    "telefon2": "phone2",
    "chat_id": "chat_id",
    "izoh": "note",
    "chegara": "credit_limit",
    "boshlangich": "opening",
    "tashqi_qarz": "outside_debt",
    "tashqi_izoh": "outside_note",
    "sinx_ochiq": "sync_open",
    "eslatma_ochiq": "remind_open",
    "royxatdan_otgan": "registered_at",
}

_MOVE_KIND = {"tolov": "payment", "qarz": "charge", "tuzatish": "adjust",
              "boshlangich": "adjust"}


def _open_source(path: str) -> sqlite3.Connection:
    """Manba bazani FAQAT O'QISH uchun ochadi — tegib ketish ehtimoli yo'q."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _cols(con: sqlite3.Connection, table: str) -> set:
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def read_clients(src: sqlite3.Connection) -> List[Dict]:
    have = _cols(src, "mijozlar")
    out = []
    for r in src.execute("SELECT * FROM mijozlar ORDER BY nom"):
        name = (r["nom"] or "").strip()
        if not name:
            continue
        row = {"name": name}
        for a, b in _CLIENT_MAP.items():
            if a not in have:
                continue
            v = r[a]
            if v is None:
                continue
            if b in ("credit_limit", "opening", "outside_debt"):
                row[b] = float(v)
            elif b in ("sync_open", "remind_open"):
                row[b] = int(v)
            else:
                v = str(v).strip()
                if v:
                    row[b] = v
        out.append(row)
    return out


def read_moves(src: sqlite3.Connection) -> List[Dict]:
    """QO'LDA kiritilgan harakatlar (chekdan hosil bo'lmaganlari).

    `tolov` turidagilarning KO'PCHILIGI `qarz_tolovlari` dan (ya'ni
    «ОПЛАТА» xabaridan) hosil bo'lgan — ular bizda Telegramdan qayta
    o'qiladi, shuning uchun olinmaydi. Lekin ularning orasida botning
    `/tolov` buyrug'i bilan QO'LDA kiritilgani ham bor va uni hech qayerdan
    tiklab bo'lmaydi. Shuning uchun har bir `tolov` harakati
    `qarz_tolovlari` bilan solishtiriladi (mijoz + daqiqa + summa); mos
    kelmagani qo'lda kiritilgan deb olinadi.

    Shu tekshiruvsiz to'lab bo'lgan mijoz qarzdor bo'lib qolardi.
    """
    pays = {
        ((p["mijoz"] or "").strip(), (p["vaqt_toliq"] or "")[:16],
         round(float(p["tolangan"] or 0), 2))
        for p in src.execute("SELECT mijoz, vaqt_toliq, tolangan FROM qarz_tolovlari")
    }

    out = []
    for r in src.execute(
            "SELECT * FROM harakatlar WHERE chek_id IS NULL ORDER BY vaqt, id"):
        turi = (r["turi"] or "").strip()
        if turi == "tolov":
            key = ((r["mijoz"] or "").strip(), (r["vaqt"] or "")[:16],
                   round(float(r["summa"] or 0), 2))
            if key in pays:
                continue            # «ОПЛАТА» dan kelgan — takror olinmaydi
        out.append({
            "client": (r["mijoz"] or "").strip(),
            "at": (r["vaqt"] or "").strip(),
            "kind": _MOVE_KIND.get(turi, "adjust"),
            "amount": float(r["summa"] or 0),
            "note": (r["izoh"] or "").strip() or f"ChekHisobot: {turi}",
        })
    return [m for m in out if m["client"]]


def run(src_path: str, apply: bool) -> int:
    src = _open_source(src_path)
    clients = read_clients(src)
    moves = read_moves(src)
    src.close()

    with MarketplaceDB() as db:
        # Cheklar jadvali hali yaratilmagan bo'lishi mumkin (toza baza) —
        # quyida nomlarni o'shandan tekshiramiz.
        from marketplace import receipts as R

        R.ensure_schema(db)
        D.ensure_schema(db.conn)
        have = {r[0] for r in db.conn.execute("SELECT name FROM debt_clients")}
        known = {r[0] for r in db.conn.execute(
            "SELECT DISTINCT client FROM receipts WHERE client <> ''")}

        yangi = [c for c in clients if c["name"] not in have]
        bor = [c for c in clients if c["name"] in have]
        cheksiz = [c["name"] for c in clients if c["name"] not in known]

        print(f"Mijozlar: {len(clients)} ta "
              f"(yangi {len(yangi)}, bor edi {len(bor)})")
        print(f"  telefoni bor:        {sum(1 for c in clients if c.get('phone'))}")
        print(f"  Telegram chat_id:    {sum(1 for c in clients if c.get('chat_id'))}")
        print(f"  tashqi qarzi bor:    {sum(1 for c in clients if c.get('outside_debt'))}")
        print(f"  sinx qulflangan:     {sum(1 for c in clients if c.get('sync_open') == 0)}")
        print(f"  eslatmasi o'chiq:    {sum(1 for c in clients if c.get('remind_open') == 0)}")
        if cheksiz:
            # Nomi cheklarda uchramasa — bog'lanmay qoladi. Yashirmaymiz:
            # odatda bu nom kassada o'zgartirilganini bildiradi.
            print(f"\n  DIQQAT: {len(cheksiz)} mijozning nomi cheklarda uchramadi:")
            for n in cheksiz[:20]:
                print(f"    ? {n}")
            if len(cheksiz) > 20:
                print(f"    … yana {len(cheksiz) - 20} ta")

        print(f"\nQo'lda kiritilgan harakatlar: {len(moves)}")

        if not apply:
            print("\nSINOV — hech narsa yozilmadi. Yozish uchun: --apply")
            return 0

        for c in clients:
            name = c.pop("name")
            D.upsert_client(db.conn, name, **c)

        # Migratsiyani ikki marta ishga tushirish xavfsiz bo'lsin: aynan shu
        # harakat allaqachon yozilgan bo'lsa takror qo'shilmaydi (aks holda
        # qarz ikki barobar kamayib ketardi).
        qoshildi = 0
        for m in moves:
            bor = db.conn.execute(
                """SELECT 1 FROM debt_moves
                    WHERE client = ? AND at = ? AND kind = ? AND amount = ?""",
                (m["client"], m["at"], m["kind"], m["amount"])).fetchone()
            if bor:
                continue
            D.add_move(db.conn, m["client"], m["kind"], m["amount"],
                       at=m["at"], note=m["note"], author="migratsiya")
            qoshildi += 1
        print(f"Harakatlar: +{qoshildi} yangi "
              f"({len(moves) - qoshildi} tasi allaqachon bor edi)")
        db.conn.commit()

        n = db.conn.execute("SELECT COUNT(*) FROM debt_clients").fetchone()[0]
        print(f"\nYozildi. Bazada endi {n} mijoz.")
    return 0


def main(argv=None) -> int:
    # Windows konsoli sukut bo'yicha cp1251 — mijoz nomidagi bitta g'alati
    # belgi butun migratsiyani yiqitardi (UnicodeEncodeError). Chiqishni
    # UTF-8 ga o'tkazamiz, o'girib bo'lmaydigani «?» bo'lib ketaveradi.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(
        description="ChekHisobot (hisobot.db) → marketplace.db migratsiyasi")
    ap.add_argument("--db", required=True, help="hisobot.db yo'li")
    ap.add_argument("--apply", action="store_true",
                    help="haqiqatda yozadi (sukut: faqat ko'rsatadi)")
    args = ap.parse_args(argv)
    return run(args.db, args.apply)


if __name__ == "__main__":
    sys.exit(main())
