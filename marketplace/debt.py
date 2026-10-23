"""Mijozlarning qarz daftari — YesPOS cheklari va to'lovlari asosida.

QARZ QAYERDAN OLINADI. Biz qarzni noldan QAYTA HISOBLAMAYMIZ. QPOS ikki joyda
o'z rasmiy qoldig'ini yozib yuboradi:

  * nasiya chekidagi «Задолженность»  → `receipts.client_debt`
  * «ОПЛАТА» xabaridagi «Остаток»     → `debt_payments.balance_after`

Shu ikkisidan ENG KEYINGISI «surat» (snapshot) qilib olinadi, ustiga suratdan
keyingi harakatlar qo'shiladi. Nega shunday: bitta chek o'qilmay qolsa yoki
kassada qo'lda tuzatish bo'lsa, noldan yig'ilgan hisob abadiy adashib qolardi —
surat esa har yangi chekda o'zini o'ngladi.

    qarz = surat + (suratdan keyingi nasiya) − (suratdan keyingi to'lovlar)
                 + qo'lda kiritilgan harakatlar + tashqi qarz

TASHQI QARZ — QPOS dan tashqaridagi qarz (daftarda, og'zaki kelishuv). U
chekdagi «Задолженность» ga kirmaydi, shuning uchun alohida saqlanadi va har
doim ustiga qo'shiladi; yangi chek kelganda yo'qolmaydi.

Mijoz NOM bo'yicha aniqlanadi — chekda id yo'q, faqat «Клиент : ...» qatori
bor. Shuning uchun nom bu yerda kalit.
"""

from __future__ import annotations

from typing import Dict, List, Optional

SCHEMA = """
-- Qarz daftaridagi mijoz. Nomi chekdagi «Клиент» bilan AYNAN bir xil
-- bo'lishi shart — bog'lanish shu orqali.
CREATE TABLE IF NOT EXISTS debt_clients (
    name          TEXT PRIMARY KEY,
    phone         TEXT DEFAULT '',
    phone2        TEXT DEFAULT '',
    chat_id       TEXT DEFAULT '',      -- Telegram: botda ro'yxatdan o'tgan bo'lsa
    note          TEXT DEFAULT '',
    credit_limit  REAL NOT NULL DEFAULT 0,   -- ruxsat etilgan qarz chegarasi
    opening       REAL NOT NULL DEFAULT 0,   -- boshlang'ich qarz (qo'lda)
    outside_debt  REAL NOT NULL DEFAULT 0,   -- QPOS bilmaydigan qarz
    outside_note  TEXT DEFAULT '',
    sync_open     INTEGER NOT NULL DEFAULT 1,  -- 0: QPOS sinxroni telefonga tegmaydi
    remind_open   INTEGER NOT NULL DEFAULT 1,  -- 0: avto-eslatma yuborilmaydi
    registered_at TEXT DEFAULT '',
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dclients_chat ON debt_clients(chat_id);

-- QO'LDA kiritilgan harakatlar. Cheklar va «ОПЛАТА» lar bu yerga YOZILMAYDI —
-- ular o'z jadvallaridan o'qiladi, aks holda bir to'lov ikki marta sanalardi.
--   kind: payment (to'lov) | charge (qarz qo'shildi) | adjust (tuzatish)
CREATE TABLE IF NOT EXISTS debt_moves (
    id         INTEGER PRIMARY KEY,
    client     TEXT NOT NULL,
    at         TEXT NOT NULL,          -- YYYY-MM-DD HH:MM:SS
    kind       TEXT NOT NULL,
    amount     REAL NOT NULL DEFAULT 0,
    note       TEXT DEFAULT '',
    author     TEXT DEFAULT '',        -- kim kiritdi (tg id yoki ism)
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dmoves_client ON debt_moves(client, at);
"""

# Qo'lda kiritilgan harakatning qoldiqqa ta'siri.
_MOVE_SIGN = {"payment": -1.0, "charge": 1.0, "adjust": 1.0}


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


# ─── Surat ───────────────────────────────────────────────────────────────────

class Snapshot:
    """QPOS ning o'z qoldig'i: qachon va qancha."""

    __slots__ = ("at", "value", "source", "ref")

    def __init__(self, at: str, value: float, source: str, ref: int):
        self.at = at            # YYYY-MM-DD HH:MM:SS
        self.value = value
        self.source = source    # 'receipt' | 'payment'
        self.ref = ref          # msg_id

    def __repr__(self):
        return f"<Snapshot {self.at} {self.value} {self.source}#{self.ref}>"


def snapshot(conn, client: str) -> Optional[Snapshot]:
    """Mijozning eng oxirgi rasmiy qarz qoldig'i. Yo'q bo'lsa `None`."""
    rec = conn.execute(
        """SELECT msg_id, client_debt, sold_at FROM receipts
            WHERE client = ? AND client_debt IS NOT NULL
            ORDER BY sold_at DESC, msg_id DESC LIMIT 1""",
        (client,),
    ).fetchone()
    pay = conn.execute(
        """SELECT msg_id, balance_after, paid_at FROM debt_payments
            WHERE client = ? AND balance_after IS NOT NULL
            ORDER BY paid_at DESC, msg_id DESC LIMIT 1""",
        (client,),
    ).fetchone()

    if rec is None and pay is None:
        return None
    if pay is None:
        return Snapshot(rec[2], float(rec[1]), "receipt", int(rec[0]))
    if rec is None:
        return Snapshot(pay[2], float(pay[1]), "payment", int(pay[0]))
    # Ikkalasi ham bor — keyingisi. Teng bo'lsa to'lov ustun: u chekdan
    # keyin bo'ladi (avval tovar beriladi, keyin pul to'lanadi).
    if pay[2] >= rec[2]:
        return Snapshot(pay[2], float(pay[1]), "payment", int(pay[0]))
    return Snapshot(rec[2], float(rec[1]), "receipt", int(rec[0]))


def _sum(conn, sql: str, args) -> float:
    row = conn.execute(sql, args).fetchone()
    return float(row[0] or 0.0) if row else 0.0


def _client_row(conn, client: str):
    return conn.execute(
        "SELECT * FROM debt_clients WHERE name = ?", (client,)
    ).fetchone()


def manual_total(conn, client: str, after: Optional[str] = None) -> float:
    """Qo'lda kiritilgan harakatlar yig'indisi."""
    sql = ("SELECT SUM(CASE kind WHEN 'payment' THEN -amount ELSE amount END) "
           "FROM debt_moves WHERE client = ?")
    args = [client]
    if after:
        sql += " AND at > ?"
        args.append(after)
    return _sum(conn, sql, args)


def qpos_debt(conn, client: str) -> float:
    """QPOS bo'yicha qarz (tashqi qarzsiz)."""
    snap = snapshot(conn, client)
    row = _client_row(conn, client)
    opening = float(row["opening"]) if row is not None else 0.0

    if snap is None:
        # Surat yo'q — noldan yig'amiz. Bu kamdan-kam: mijoz hech qachon
        # nasiyaga olmagan yoki cheklari hali o'qilmagan.
        charged = _sum(conn, "SELECT SUM(paid_debt) FROM receipts WHERE client = ?",
                       (client,))
        paid = _sum(conn, "SELECT SUM(amount) FROM debt_payments WHERE client = ?",
                    (client,))
        return opening + charged - paid + manual_total(conn, client)

    # Suratdan KEYIN bo'lgan harakatlar.
    #
    # Surat bilan BIR SONIYADAGI yozuvlar: suratning o'zi sanalmasligi kerak
    # (uning ta'siri qoldiqqa allaqachon kirgan), lekin ayni soniyadagi
    # BOSHQA yozuv tushib qolmasligi kerak. Shuning uchun surat qaysi
    # jadvaldan olingan bo'lsa, o'sha jadvalda «>=» va id bo'yicha chiqarib
    # tashlash, ikkinchisida oddiy «>» ishlatiladi.
    if snap.source == "receipt":
        charged = _sum(
            conn,
            """SELECT SUM(paid_debt) FROM receipts
                WHERE client = ? AND sold_at >= ? AND msg_id <> ?""",
            (client, snap.at, snap.ref))
        paid = _sum(
            conn,
            "SELECT SUM(amount) FROM debt_payments WHERE client = ? AND paid_at > ?",
            (client, snap.at))
    else:
        charged = _sum(
            conn,
            "SELECT SUM(paid_debt) FROM receipts WHERE client = ? AND sold_at > ?",
            (client, snap.at))
        paid = _sum(
            conn,
            """SELECT SUM(amount) FROM debt_payments
                WHERE client = ? AND paid_at >= ? AND msg_id <> ?""",
            (client, snap.at, snap.ref))
    return snap.value + charged - paid + manual_total(conn, client, snap.at)


def debt(conn, client: str) -> float:
    """Joriy to'liq qarz: QPOS + tashqi."""
    row = _client_row(conn, client)
    outside = float(row["outside_debt"]) if row is not None else 0.0
    return qpos_debt(conn, client) + outside


# ─── Daftar (hisob-kitob) ────────────────────────────────────────────────────

def ledger(conn, client: str, limit: int = 0) -> List[dict]:
    """Mijozning hisob-kitob daftari — har qator qoldiqni qanday o'zgartirgani.

    Qatorlar vaqt bo'yicha tartiblanadi va qoldiq yugurib boradi. Chekdagi
    «Задолженность» uchrasa — qoldiq O'SHANGA tenglashtiriladi va farq
    «kassada tuzatish» qatori bo'lib ko'rinadi: yashirilmaydi, chunki farq
    odatda bizda yo'q to'lovni bildiradi.
    """
    row = _client_row(conn, client)
    opening = float(row["opening"]) if row is not None else 0.0

    events = []
    for r in conn.execute(
        """SELECT msg_id, doc_no, kind, sold_at, payable, paid_debt, client_debt
             FROM receipts WHERE client = ? ORDER BY sold_at, msg_id""",
        (client,),
    ):
        events.append({
            "at": r["sold_at"], "type": "receipt", "id": r["msg_id"],
            "doc_no": r["doc_no"] or "", "kind": r["kind"],
            "total": float(r["payable"] or 0),
            "change": float(r["paid_debt"] or 0),
            "official": None if r["client_debt"] is None else float(r["client_debt"]),
            "note": "",
        })
    for r in conn.execute(
        """SELECT msg_id, paid_at, amount, balance_after, kinds, note
             FROM debt_payments WHERE client = ? ORDER BY paid_at, msg_id""",
        (client,),
    ):
        events.append({
            "at": r["paid_at"], "type": "payment", "id": r["msg_id"],
            "doc_no": "", "kind": "payment", "total": float(r["amount"] or 0),
            "change": -float(r["amount"] or 0),
            "official": None if r["balance_after"] is None else float(r["balance_after"]),
            "note": (r["kinds"] or "") + ((" · " + r["note"]) if r["note"] else ""),
        })
    for r in conn.execute(
        """SELECT id, at, kind, amount, note FROM debt_moves
            WHERE client = ? ORDER BY at, id""",
        (client,),
    ):
        amount = float(r["amount"] or 0)
        events.append({
            "at": r["at"], "type": "manual", "id": r["id"], "doc_no": "",
            "kind": r["kind"], "total": amount,
            "change": amount * _MOVE_SIGN.get(r["kind"], 1.0),
            "official": None, "note": r["note"] or "",
        })

    events.sort(key=lambda e: (e["at"], 0 if e["type"] == "receipt" else 1))

    out: List[dict] = []
    balance = opening
    if abs(opening) > 0.5:
        out.append({"at": "", "type": "opening", "id": 0, "doc_no": "",
                    "kind": "opening", "total": opening, "change": opening,
                    "balance": balance, "note": "Boshlang'ich qarz (qo'lda)"})

    for e in events:
        balance += e["change"]
        if e["official"] is not None and abs(e["official"] - balance) > 0.5:
            # QPOS boshqa qoldiq ko'rsatyapti — oraliqda bizda yo'q harakat
            # bo'lgan. Farqni alohida qator qilib ko'rsatamiz va qoldiqni
            # QPOS niki bilan tenglaymiz (u rasmiy).
            fark = e["official"] - balance
            out.append({"at": e["at"], "type": "fix", "id": 0, "doc_no": "",
                        "kind": "fix", "total": fark, "change": fark,
                        "balance": e["official"],
                        "note": "Kassada tuzatish (QPOS hisobi bilan farq)"})
            balance = e["official"]
        e = dict(e)
        e["balance"] = balance
        out.append(e)

    if limit and len(out) > limit:
        out = out[-limit:]
    return out


# ─── Qarzdorlar ──────────────────────────────────────────────────────────────

def clients_with_debt(conn, min_amount: float = 1.0) -> List[dict]:
    """Qarzi bor mijozlar — kattasidan kichigiga.

    Ro'yxat chekdagi nomlardan ham, `debt_clients` dan ham yig'iladi: kassada
    yangi mijoz ochilgan, lekin bizda hali yozuvi yo'q bo'lsa ham ko'rinsin.
    """
    names = {r[0] for r in conn.execute(
        "SELECT DISTINCT client FROM receipts WHERE client <> ''")}
    names |= {r[0] for r in conn.execute(
        "SELECT DISTINCT client FROM debt_payments WHERE client <> ''")}
    names |= {r[0] for r in conn.execute("SELECT name FROM debt_clients")}

    rows = []
    for name in names:
        total = debt(conn, name)
        if total < min_amount:
            continue
        snap = snapshot(conn, name)
        info = _client_row(conn, name)
        rows.append({
            "name": name,
            "debt": round(total),
            "qpos": round(qpos_debt(conn, name)),
            "outside": round(float(info["outside_debt"]) if info is not None else 0),
            "phone": (info["phone"] if info is not None else "") or "",
            "chat_id": (info["chat_id"] if info is not None else "") or "",
            "limit": round(float(info["credit_limit"]) if info is not None else 0),
            "remind_open": int(info["remind_open"]) if info is not None else 1,
            "last_at": snap.at if snap else "",
            "source": snap.source if snap else "",
        })
    rows.sort(key=lambda r: -r["debt"])
    return rows


def upsert_client(conn, name: str, **fields) -> None:
    """Mijozni yaratadi yoki berilgan maydonlarini yangilaydi."""
    from marketplace.db import now_local

    allowed = {"phone", "phone2", "chat_id", "note", "credit_limit", "opening",
               "outside_debt", "outside_note", "sync_open", "remind_open",
               "registered_at"}
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"Noma'lum maydon: {', '.join(sorted(bad))}")

    ts = now_local().isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO debt_clients (name, created_at) VALUES (?,?) "
        "ON CONFLICT(name) DO NOTHING", (name, ts))
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE debt_clients SET {sets} WHERE name = ?",
                     (*fields.values(), name))


def add_move(conn, client: str, kind: str, amount: float, at: str = "",
             note: str = "", author: str = "") -> int:
    """Qo'lda harakat qo'shadi (to'lov / qarz / tuzatish)."""
    from marketplace.db import now_local

    if kind not in _MOVE_SIGN:
        raise ValueError(f"Noma'lum tur: {kind}")
    ts = now_local()
    upsert_client(conn, client)
    cur = conn.execute(
        """INSERT INTO debt_moves (client, at, kind, amount, note, author, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (client, at or ts.strftime("%Y-%m-%d %H:%M:%S"), kind, float(amount),
         note, author, ts.isoformat(timespec="seconds")),
    )
    return int(cur.lastrowid)


def client_by_chat(conn, chat_id: str) -> List[str]:
    """Shu Telegram chatga bog'langan mijoz nomlari (bittadan ko'p bo'lishi mumkin)."""
    return [r[0] for r in conn.execute(
        "SELECT name FROM debt_clients WHERE chat_id = ? AND chat_id <> ''",
        (str(chat_id),))]


def client_by_phone(conn, phone9: str) -> List[str]:
    """Telefonning oxirgi 9 raqami bo'yicha mijoz nomlari."""
    out = []
    for r in conn.execute(
            "SELECT name, phone, phone2 FROM debt_clients"):
        for p in (r[1], r[2]):
            if p and _digits9(p) == phone9:
                out.append(r[0])
                break
    return out


def bind_phone(conn, phone: str, chat_id: str) -> List[str]:
    """Telefon bo'yicha kassadagi hisobni Telegram akkauntiga bog'laydi.

    Mijoz botda raqamini tasdiqlaganda chaqiriladi. Nomlar RO'YXAT bo'lib
    qaytadi: bitta odamning kassada bir nechta nomi bo'lishi mumkin.
    Hech narsa topilmasa — bo'sh ro'yxat, bu xato emas (kassada hali
    xaridi yo'q mijoz).
    """
    names = client_by_phone(conn, _digits9(phone))
    for n in names:
        upsert_client(conn, n, chat_id=str(chat_id))
    return names


def _digits9(phone: str) -> str:
    """«+998 94 686 77 33» → «946867733». Taqqoslash shu ko'rinishda."""
    d = "".join(ch for ch in (phone or "") if ch.isdigit())
    return d[-9:] if len(d) >= 9 else d


def stats(conn) -> Dict[str, float]:
    """Umumiy qarz holati — admin paneli uchun."""
    rows = clients_with_debt(conn)
    return {
        "debtors": len(rows),
        "total": sum(r["debt"] for r in rows),
        "outside": sum(r["outside"] for r in rows),
        "top": rows[:5],
    }
