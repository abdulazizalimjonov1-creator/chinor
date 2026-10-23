"""YesPOS chek xabarlarini ajratish va saqlash.

Bu modul TELEGRAM'ni bilmaydi — unga tayyor matn beriladi. Shu sababli
ajratgichni xabarlarsiz ham sinash mumkin, va transport (`tg_receipts.py`)
o'zgarsa bu yer tegilmaydi.

Chek ko'rinishi (YesPOS @yespos_notify_bot):

    ПРОДАЖА №: 44527
    Дата : 18:55:17 20-08-2026
    Филиал : CHINOR
    Касса : Kassa-1
    Сотрудник : Mo'minjonov Abduqodir
    -----------------------------
    1) Oshiq moshiq kapalak
        3.00 x 21,666.67 = 65,000.00      ← HAQIQATDA to'langan
          3.00 x 22,000.00 = 66,000.00    ← chegirmagacha (bo'lishi shart emas)
    -----------------------------
    Общий: 66,000.00
    Скидки: 1,000.00
    К оплате: 65,000.00

To'rt xil xabar keladi:
  • ПРОДАЖА             — savdo
  • ПРОДАЖА (ОТЛОЖЕН)   — nasiyaga savdo. Tovar baribir do'kondan chiqadi,
                          shuning uchun ketish tezligiga QO'SHILADI.
  • ВОЗВРАТ             — qaytarish. Miqdori MANFIY bo'lib yoziladi.
  • ОПЛАТА              — qarz to'lovi. Tovar yo'q, shuning uchun `parse()`
                          uni olmaydi — `parse_payment()` o'qiydi va
                          `debt_payments` jadvaliga tushadi. Mijozning qarz
                          daftari aynan shu ikkisidan yig'iladi.

Tovar nomi bo'yicha katalogga bog'lanadi (chekda artikul yo'q). Nomlar YesPOS
katalogidan kelgani uchun aksariyati AYNAN mos tushadi; mos tushmagani
`product_id = 0` bo'lib qoladi va `unmatched` buyrug'ida ko'rinadi — jim
tashlab yuborilmaydi.
"""

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from marketplace.db import MarketplaceDB, now_local

SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts (
    msg_id      INTEGER PRIMARY KEY,      -- Telegram xabar id — takror yozilmaydi
    doc_no      TEXT    DEFAULT '',
    kind        TEXT    NOT NULL,         -- sale | sale_deferred | return
    branch      TEXT    DEFAULT '',
    cashier     TEXT    DEFAULT '',
    client      TEXT    DEFAULT '',
    sold_at     TEXT    NOT NULL,         -- YYYY-MM-DD HH:MM:SS (Toshkent)
    day         TEXT    NOT NULL,         -- YYYY-MM-DD
    total       REAL    DEFAULT 0,
    discount    REAL    DEFAULT 0,
    payable     REAL    DEFAULT 0,
    paid_cash   REAL    DEFAULT 0,    -- Наличные
    paid_card   REAL    DEFAULT 0,    -- Пласт. карта / Корп.карта / Humo
    paid_click  REAL    DEFAULT 0,    -- Click
    paid_debt   REAL    DEFAULT 0,    -- Долг (qarzga yozilgan qism)
    client_debt REAL,                 -- Задолженность: chekdagi QARZ QOLDIG'I
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_receipts_day ON receipts(day);
CREATE INDEX IF NOT EXISTS idx_receipts_client ON receipts(client, sold_at);

-- «ОПЛАТА» — mijoz kassada qarzini to'lagani. Tovar harakati yo'q, shuning
-- uchun `receipts` ga qo'shilmaydi: aks holda aylanma ikki marta sanalardi.
--
-- `balance_after` («Остаток») — to'lovdan keyingi qarz. Bu QPOS ning O'Z
-- hisobi, ya'ni chekdagi «Задолженность» bilan bir xil ishonchli: qarzni
-- biz qayta hisoblamaymiz, eng oxirgi rasmiy qoldiqni asos qilamiz.
CREATE TABLE IF NOT EXISTS debt_payments (
    msg_id        INTEGER PRIMARY KEY,
    client        TEXT    NOT NULL,
    paid_at       TEXT    NOT NULL,     -- YYYY-MM-DD HH:MM:SS
    day           TEXT    NOT NULL,
    debt_before   REAL    DEFAULT 0,    -- Общий долг
    amount        REAL    DEFAULT 0,    -- to'lovlar yig'indisi
    balance_after REAL,                 -- Остаток (bo'lmasligi mumkin)
    kinds         TEXT    DEFAULT '',   -- «Click, Наличные»
    note          TEXT    DEFAULT '',   -- Комментарий
    branch        TEXT    DEFAULT '',
    cashier       TEXT    DEFAULT '',
    created_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dpay_client ON debt_payments(client, paid_at);
CREATE INDEX IF NOT EXISTS idx_dpay_day ON debt_payments(day);

CREATE TABLE IF NOT EXISTS receipt_items (
    msg_id      INTEGER NOT NULL,
    line_no     INTEGER NOT NULL,
    name        TEXT    NOT NULL,
    qty         REAL    NOT NULL,         -- qaytarishda MANFIY
    price       REAL    DEFAULT 0,
    amount      REAL    DEFAULT 0,
    product_id  INTEGER DEFAULT 0,        -- katalog id (0 = topilmadi)
    PRIMARY KEY (msg_id, line_no)
);
CREATE INDEX IF NOT EXISTS idx_ritems_product ON receipt_items(product_id);
CREATE INDEX IF NOT EXISTS idx_ritems_name    ON receipt_items(name);

-- Qo'lda belgilangan moslik. Kassadagi nom xato yozilgan bo'lsa (masalan
-- «PROFIL J 75/40» aslida «Demir profil J 75/50»), avtomatik moslashtirish
-- uni topa olmaydi — va TOPMAGANI TO'G'RI: taxmin qilib bog'lasa, bitta
-- tovarning savdosi boshqasiga yozilardi. Qaror odamniki, bu yerda saqlanadi
-- va har qayta bog'lashda birinchi bo'lib qo'llanadi.
CREATE TABLE IF NOT EXISTS receipt_aliases (
    name        TEXT PRIMARY KEY,     -- chekdagi nom (aynan)
    product_id  INTEGER NOT NULL,     -- katalog id
    note        TEXT DEFAULT '',
    created_at  TEXT NOT NULL
);
"""

# «ПРОДАЖА №: 44527» / «ВОЗВРАТ №: 44410» / «ПРОДАЖА (ОТЛОЖЕН) №: 44092»
_HEAD = re.compile(r"^(ПРОДАЖА|ВОЗВРАТ|ОПЛАТА)(\s*\(ОТЛОЖЕН\))?\s*(?:№\s*:?\s*(\S+))?")
_FIELD = re.compile(r"^\s*([А-Яа-яЁё]+)\s*:\s*(.*)$")
# «1) Tovar nomi»
_ITEM = re.compile(r"^\s*(\d+)\)\s*(.+?)\s*$")
# «3.00 x 21,666.67 = 65,000.00»
_PRICE = re.compile(
    r"^\s*([\d,]+(?:\.\d+)?)\s*[xх*]\s*([\d,]+(?:\.\d+)?)\s*=\s*([\d,]+(?:\.\d+)?)\s*$"
)
_DATE = re.compile(r"(\d{2}):(\d{2}):(\d{2})\s+(\d{2})-(\d{2})-(\d{4})")

# To'lov qatorlari: «    Наличные : 65,000.00». Bitta chekda bir nechtasi
# bo'lishi mumkin (aralash to'lov) — shuning uchun har biri alohida yig'iladi.
# Nomlar YesPOS'dan qanday kelsa shundayligicha; noma'lumi `paid_click` ga
# emas, e'tiborsiz qoldiriladi (jim noto'g'ri joyga qo'shishdan ko'ra yaxshi).
_PAY_KINDS = {
    "наличные": "cash",
    "пласт. карта": "card",
    "пласт.карта": "card",
    "корп.карта": "card",
    "корп. карта": "card",
    "humo": "card",
    "uzcard": "card",
    "терминал": "card",
    "click": "click",
    "payme": "click",
    "долг": "debt",
}
_PAY = re.compile(r"^\s*([А-Яа-яЁёA-Za-z\.\s]+?)\s*:\s*([\d,]+(?:\.\d+)?)\s*$")

KIND_SALE, KIND_DEFERRED, KIND_RETURN = "sale", "sale_deferred", "return"


def _num(s: str) -> float:
    """«21,666.67» → 21666.67. Ajratgich vergul, kasr nuqta."""
    try:
        return float((s or "").replace(",", "").replace(" ", ""))
    except ValueError:
        return 0.0


@dataclass
class Receipt:
    kind: str
    doc_no: str = ""
    branch: str = ""
    cashier: str = ""
    client: str = ""
    sold_at: Optional[datetime] = None
    total: float = 0.0
    discount: float = 0.0
    payable: float = 0.0
    paid: Dict[str, float] = field(default_factory=dict)
    items: List[dict] = field(default_factory=list)
    # «Задолженность» — chek chiqqan ondagi mijoz qarzi. Nasiya chekida
    # bo'ladi. Bu YUGURIB BORADIGAN qoldiq, shuning uchun qarz hisobida
    # asos qilib olinadi (qarang: `debt.py`).
    client_debt: Optional[float] = None

    @property
    def day(self) -> str:
        return self.sold_at.strftime("%Y-%m-%d") if self.sold_at else ""


@dataclass
class DebtPayment:
    """«ОПЛАТА» xabari — mijoz kassada qarzini to'lagani."""

    client: str = ""
    paid_at: Optional[datetime] = None
    debt_before: float = 0.0
    amount: float = 0.0
    balance_after: Optional[float] = None
    kinds: str = ""
    note: str = ""
    branch: str = ""
    cashier: str = ""

    @property
    def day(self) -> str:
        return self.paid_at.strftime("%Y-%m-%d") if self.paid_at else ""


def parse(text: str) -> Optional[Receipt]:
    """Chek matnini `Receipt` ga aylantiradi.

    `None` qaytaradi: chek emas (ОПЛАТА, tizim xabari) yoki tanib bo'lmadi.
    Tanib bo'lmagani JIM tashlanmaydi — chaqiruvchi uni sanab boradi.
    """
    if not text:
        return None
    lines = text.splitlines()
    head = _HEAD.match(lines[0].strip())
    if not head:
        return None

    word, deferred, doc_no = head.group(1), head.group(2), head.group(3) or ""
    if word == "ОПЛАТА":
        return None                     # qarz to'lovi — tovar harakati yo'q
    kind = (KIND_RETURN if word == "ВОЗВРАТ"
            else KIND_DEFERRED if deferred else KIND_SALE)
    sign = -1.0 if kind == KIND_RETURN else 1.0

    r = Receipt(kind=kind, doc_no=doc_no)
    pending: Optional[dict] = None      # nomi o'qilgan, narxi kutilayotgan qator

    def flush():
        nonlocal pending
        if pending and pending["qty"]:
            r.items.append(pending)
        pending = None

    for raw in lines[1:]:
        line = raw.rstrip()
        if not line.strip() or set(line.strip()) == {"-"}:
            continue

        m = _PRICE.match(line)
        if m and pending is not None:
            # Nomdan keyingi BIRINCHI narx qatori — haqiqatda to'langani.
            # Ikkinchisi (chegirmagacha) e'tiborsiz: bizga haqiqiy pul kerak.
            if not pending["qty"]:
                pending["qty"] = _num(m.group(1)) * sign
                pending["price"] = _num(m.group(2))
                pending["amount"] = _num(m.group(3)) * sign
            continue

        m = _ITEM.match(line)
        if m and not _FIELD.match(line):
            flush()
            pending = {"line_no": int(m.group(1)), "name": m.group(2).strip(),
                       "qty": 0.0, "price": 0.0, "amount": 0.0}
            continue

        # To'lov turi: «Наличные : 65,000.00», «Click : 269,500.00» …
        # DIQQAT: bu tekshiruv `_FIELD` dan OLDIN turishi shart — kirillcha
        # to'lov nomlari (Наличные, Долг) `_FIELD` naqshiga ham to'g'ri keladi
        # va u yerda e'tiborsiz qolib ketardi.
        m = _PAY.match(line)
        if m:
            # DIQQAT: `kind` NOMINI ishlatmaymiz — u chek TURINI saqlaydi
            # (savdo/nasiya/qaytarish) va pastda kerak bo'ladi. Ustidan
            # yozilsa nasiya qarzga yozilmay, qaytarish manfiy bo'lmay qolardi.
            pay_kind = _PAY_KINDS.get(m.group(1).strip().lower())
            if pay_kind:
                r.paid[pay_kind] = r.paid.get(pay_kind, 0.0) + _num(m.group(2))
                continue

        m = _FIELD.match(line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if key == "Дата":
                d = _DATE.search(val)
                if d:
                    hh, mm, ss, dd, mo, yy = (int(x) for x in d.groups())
                    r.sold_at = datetime(yy, mo, dd, hh, mm, ss)
            elif key == "Филиал":
                r.branch = val
            elif key == "Касса":
                r.cashier = r.cashier or val
            elif key == "Сотрудник":
                r.cashier = val
            elif key == "Клиент":
                r.client = val
            elif key == "Общий":
                r.total = _num(val)
            elif key == "Скидки":
                r.discount = _num(val)
            elif key.startswith("К оплате") or key == "оплате":
                r.payable = _num(val)
            elif key == "Задолженность":
                r.client_debt = _num(val)
            continue

        # «К оплате: 65,000.00» — kirilcha bo'shliqli kalit, _FIELD tutmaydi.
        if line.strip().startswith("К оплате"):
            r.payable = _num(line.split(":", 1)[-1])

    flush()
    if not r.sold_at or not r.items:
        return None

    # Nasiya chekida «К оплате» qatori YO'Q — o'rniga «Задолженность» turadi,
    # shuning uchun summa 0 bo'lib qolardi va aylanmadan tushib ketardi
    # (avgustda 75 mln so'm). Tovar do'kondan chiqqan, demak bu ham savdo:
    # summani qatorlardan yig'amiz.
    items_sum = sum(i["amount"] for i in r.items)
    if not r.payable:
        r.payable = items_sum
    if not r.total:
        r.total = items_sum

    # Nasiyada to'lov qatori yo'q — butun summa qarzga yozilgan.
    if kind == KIND_DEFERRED and not r.paid:
        r.paid["debt"] = r.payable

    # Qaytarishda summa MANFIY bo'lishi kerak — aylanmaga qo'shilmasin, balki
    # ayrilsin. Matnda u musbat yozilgan (miqdorlar esa manfiy).
    if kind == KIND_RETURN:
        r.payable = -abs(r.payable)
        r.total = -abs(r.total)
        # To'lov ham manfiy: qaytarishda pul kassadan CHIQADI, shuning uchun
        # naqd/karta yig'indisiga qo'shilmasligi, balki ayrilishi kerak.
        r.paid = {k: -abs(v) for k, v in r.paid.items()}
    return r


# «ОПЛАТА» xabari
#     ОПЛАТА
#     Дата : 15:24:22 10-08-2026
#     Клиент : Daler aka metro
#     Общий долг: 6,732,402.22
#     Оплаченный:
#         Click : 3,000,000.00
#         Комментарий : daler
#     Остаток: 3,732,402.22
_PAY_HEAD = re.compile(r"^\s*ОПЛАТА\s*$")
_DEBT_BEFORE = re.compile(r"^\s*(?:Общий долг|Umumiy qarz)\s*:?\s*([\d,\.\-]+)\s*$")
_DEBT_AFTER = re.compile(r"^\s*(?:Остаток|Qoldiq)\s*:?\s*([\d,\.\-]+)\s*$")
_PAID_HEAD = re.compile(r"^\s*(?:Оплаченный|To'langan)\s*:?\s*$")
_NOTE = re.compile(r"^\s*(?:Комментарий|Izoh)\s*:?\s*(.*)$")
_PAY_LINE = re.compile(r"^\s*([A-Za-zА-Яа-яЁё'\. ]+?)\s*:\s*([\d,\.]+)\s*$")


def is_payment(text: str) -> bool:
    """Matn «ОПЛАТА» xabarimi."""
    return bool(text) and bool(_PAY_HEAD.match(text.splitlines()[0]))


def parse_payment(text: str) -> Optional[DebtPayment]:
    """«ОПЛАТА» xabarini `DebtPayment` ga aylantiradi.

    `None`: bu ОПЛАТА emas, yoki sana/mijoz yo'q — ikkalasisiz yozuvning
    qarz daftarida ma'nosi qolmaydi.
    """
    if not is_payment(text):
        return None

    p = DebtPayment()
    in_paid = False
    for raw in text.splitlines()[1:]:
        line = raw.strip()
        if not line or set(line) == {"-"}:
            continue

        m = _DEBT_BEFORE.match(line)
        if m:
            p.debt_before = _num(m.group(1))
            continue
        m = _DEBT_AFTER.match(line)
        if m:
            p.balance_after = _num(m.group(1))
            continue
        if _PAID_HEAD.match(line):
            in_paid = True
            continue
        m = _NOTE.match(line)
        if m:
            p.note = m.group(1).strip()
            continue

        m = _FIELD.match(line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if key == "Дата":
                d = _DATE.search(val)
                if d:
                    hh, mm, ss, dd, mo, yy = (int(x) for x in d.groups())
                    p.paid_at = datetime(yy, mo, dd, hh, mm, ss)
                continue
            if key == "Филиал":
                p.branch = val
                continue
            if key in ("Касса", "Сотрудник"):
                p.cashier = val if key == "Сотрудник" else (p.cashier or val)
                continue
            if key in ("Клиент", "Mijoz"):
                p.client = re.sub(r"\s+", " ", val).strip()
                continue

        # To'lov qatori — faqat «Оплаченный» dan keyin. Undan oldin ham
        # «Nom : son» ko'rinishidagi qator bo'lishi mumkin (Касса : Kassa-1),
        # shuning uchun bo'lim boshlanmaguncha olinmaydi.
        m = _PAY_LINE.match(line)
        if m and in_paid:
            p.amount += _num(m.group(2))
            nom = m.group(1).strip()
            p.kinds = nom if not p.kinds else p.kinds + ", " + nom
            continue

    if not p.paid_at or not p.client:
        return None
    # Summa yozilmagan bo'lsa — qarz farqidan chiqaramiz.
    if p.amount <= 0 and p.balance_after is not None:
        p.amount = p.debt_before - p.balance_after
    return p


# ─── Katalogga bog'lash ──────────────────────────────────────────────────────

def _fold(s: str) -> str:
    """Nomni solishtirish ko'rinishiga keltiradi (registr, bo'shliq, apostrof)."""
    s = (s or "").lower().strip()
    for ch in ("'", "‘", "’", "`", "ʻ", "ʼ"):
        s = s.replace(ch, "")
    return re.sub(r"\s+", " ", s)


def _key(s: str) -> str:
    """Solishtirish kaliti — faqat harf/raqam, kichik registr."""
    return re.sub(r"[^a-z0-9]+", "", _fold(s))


def _tokens(s: str) -> frozenset:
    """So'zlar to'plami — SO'Z TARTIBIDAN qat'i nazar solishtirish uchun.

    Chekdagi nom katalogdagidan so'z tartibi bilan farq qiladi:
    «PROFil J 100/50 [0.4 mm]» ↔ «Demir profil J 100/50 J [0.4 mm]»,
    «Tavriko reka 3x4» ↔ «Reka Tavriko 3x4». Aynan moslik izlaganda bular
    «katalogda yo'q» bo'lib chiqardi va savdo tahlildan tushib qolardi.
    """
    return frozenset(t for t in re.split(r"[^a-z0-9]+", _fold(s)) if t)


def _letters(s: str) -> frozenset:
    """Yakka harfli so'zlar: «PROFIL J 75/40» → {"j"}.

    Qurilish mollarida yakka harf TURNI bildiradi: J-profil, U-profil,
    C-profil. Nomlar qolgan hamma joyda bir xil bo'lgani uchun o'xshashlik
    0.97 chiqadi va noto'g'ri moslik qabul qilinardi — «PROFIL J 75/40»
    «Demir profil U 75/40» ga bog'lanib, bitta tovarning savdosi
    ikkinchisiga yozilardi.
    """
    return frozenset(t for t in re.split(r"[^a-z0-9]+", _fold(s)) if len(t) == 1)


def _digits(s: str) -> frozenset:
    """Nomdagi sonlar — o'lchamni ajratadi. «RTR 20 turba» ≠ «RTR 25 turba»,
    shuning uchun sonlar mos kelmasa moslik QABUL QILINMAYDI."""
    return frozenset(re.findall(r"\d+", _fold(s)))


class CatalogIndex:
    """Nomdan katalog id'sini topish — uch bosqichda, noaniqni rad etib.

    Bosqichlar: aynan nom → so'zlar to'plami (tartibsiz) → o'xshashlik.
    Har bosqichda NOMZOD YAGONA bo'lishi shart; ikkitasi da'vogar bo'lsa
    bog'lanmaydi, chunki noto'g'ri bog'lash savdoni boshqa tovarga yozadi.
    """

    def __init__(self, rows, aliases: Dict[str, int] = None):
        self.aliases = {_fold(k): v for k, v in (aliases or {}).items()}
        self.by_key: Dict[str, List[int]] = {}
        self.by_tok: Dict[frozenset, List[int]] = {}
        self.meta: Dict[int, dict] = {}
        for pid, name in rows:
            pid = int(pid)
            self.meta[pid] = {"key": _key(name), "digits": _digits(name),
                              "letters": _letters(name)}
            self.by_key.setdefault(_key(name), []).append(pid)
            self.by_tok.setdefault(_tokens(name), []).append(pid)
        self._keys = list(self.by_key)
        self._cache: Dict[str, int] = {}

    def find(self, name: str) -> int:
        """product_id yoki 0 (topilmadi/noaniq). Nom bo'yicha keshlanadi."""
        if name in self._cache:
            return self._cache[name]
        pid = self._find(name)
        self._cache[name] = pid
        return pid

    def _find(self, name: str) -> int:
        # Qo'lda belgilangan moslik hammasidan ustun.
        hit = self.aliases.get(_fold(name))
        if hit:
            return hit
        key = _key(name)
        if not key:
            return 0
        hit = self.by_key.get(key)
        if hit and len(hit) == 1:
            return hit[0]

        hit = self.by_tok.get(_tokens(name))
        if hit and len(hit) == 1:
            return hit[0]

        import difflib
        digits = _digits(name)
        letters = _letters(name)
        close = difflib.get_close_matches(key, self._keys, n=4, cutoff=0.80)
        cands = []
        for k in close:
            ids = self.by_key[k]
            if len(ids) != 1:
                continue
            pid = ids[0]
            # O'lcham mos kelmasa — boshqa tovar (20 turba ↔ 25 turba).
            if digits and self.meta[pid]["digits"] and digits != self.meta[pid]["digits"]:
                continue
            # Tur belgisi (J/U/C profil) mos kelmasa ham — boshqa tovar.
            if letters and self.meta[pid]["letters"] and letters != self.meta[pid]["letters"]:
                continue
            cands.append((difflib.SequenceMatcher(None, key, k).ratio(), pid))
        if not cands:
            return 0
        cands.sort(reverse=True)
        if len(cands) > 1 and cands[0][0] - cands[1][0] < 0.05:
            return 0            # ikkita nomzod deyarli teng — tanlamaymiz
        return cands[0][1]


def catalog_index(db: MarketplaceDB) -> CatalogIndex:
    """Katalog nomlaridan qidirgich quradi (qo'lda mosliklar bilan)."""
    ensure_schema(db)
    aliases = {}
    try:
        aliases = {n: int(p) for n, p in
                   db.conn.execute("SELECT name, product_id FROM receipt_aliases")}
    except Exception:
        pass
    return CatalogIndex(db.conn.execute("SELECT id, name FROM products"), aliases)


# ─── Saqlash ─────────────────────────────────────────────────────────────────

def ensure_schema(db: MarketplaceDB) -> None:
    db.conn.executescript(SCHEMA)
    # `CREATE TABLE IF NOT EXISTS` mavjud jadvalga ustun qo'shmaydi.
    have = {r[1] for r in db.conn.execute("PRAGMA table_info(receipts)")}
    for col in ("paid_cash", "paid_card", "paid_click", "paid_debt"):
        if col not in have:
            db.conn.execute(f"ALTER TABLE receipts ADD COLUMN {col} REAL DEFAULT 0")
    # Sukut bo'yicha 0 EMAS, NULL: «qarzi yo'q» bilan «chekda yozilmagan»
    # bir xil bo'lib qolsa, qarz qoldig'i noldan hisoblanib ketardi.
    if "client_debt" not in have:
        db.conn.execute("ALTER TABLE receipts ADD COLUMN client_debt REAL")
    db.conn.commit()


def store(db: MarketplaceDB, msg_id: int, r: Receipt, index: "CatalogIndex") -> int:
    """Bitta chekni yozadi. Qaytaradi: bog'lanmagan qatorlar soni.

    `msg_id` birlamchi kalit — bir xabar ikki marta yozilmaydi, shuning uchun
    sinxronni xohlagancha qayta ishga tushirsa bo'ladi.
    """
    ts = now_local().isoformat(timespec="seconds")
    db.conn.execute(
        """INSERT INTO receipts (msg_id, doc_no, kind, branch, cashier, client,
                                 sold_at, day, total, discount, payable,
                                 paid_cash, paid_card, paid_click, paid_debt,
                                 client_debt, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(msg_id) DO UPDATE SET
               total=excluded.total, discount=excluded.discount,
               payable=excluded.payable, paid_cash=excluded.paid_cash,
               paid_card=excluded.paid_card, paid_click=excluded.paid_click,
               paid_debt=excluded.paid_debt,
               client_debt=COALESCE(excluded.client_debt, receipts.client_debt)""",
        (msg_id, r.doc_no, r.kind, r.branch, r.cashier, r.client,
         r.sold_at.strftime("%Y-%m-%d %H:%M:%S"), r.day,
         r.total, r.discount, r.payable,
         r.paid.get("cash", 0.0), r.paid.get("card", 0.0),
         r.paid.get("click", 0.0), r.paid.get("debt", 0.0), r.client_debt, ts),
    )
    misses = 0
    rows = []
    for it in r.items:
        pid = index.find(it["name"])
        if not pid:
            misses += 1
        rows.append((msg_id, it["line_no"], it["name"], it["qty"],
                     it["price"], it["amount"], pid))
    db.conn.executemany(
        """INSERT INTO receipt_items (msg_id, line_no, name, qty, price, amount, product_id)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(msg_id, line_no) DO NOTHING""",
        rows,
    )
    return misses


def store_payment(db: MarketplaceDB, msg_id: int, p: DebtPayment) -> None:
    """Bitta «ОПЛАТА» ni yozadi. `msg_id` kalit — takror yozilmaydi."""
    db.conn.execute(
        """INSERT INTO debt_payments (msg_id, client, paid_at, day, debt_before,
                                      amount, balance_after, kinds, note,
                                      branch, cashier, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(msg_id) DO UPDATE SET
               amount=excluded.amount, debt_before=excluded.debt_before,
               balance_after=excluded.balance_after""",
        (msg_id, p.client, p.paid_at.strftime("%Y-%m-%d %H:%M:%S"), p.day,
         p.debt_before, p.amount, p.balance_after, p.kinds, p.note,
         p.branch, p.cashier, now_local().isoformat(timespec="seconds")),
    )


# ─── CLI (ajratgichni sinash uchun) ──────────────────────────────────────────

def relink(db: MarketplaceDB, only_missing: bool = True) -> dict:
    """Saqlangan chek qatorlarini katalogga QAYTA bog'laydi.

    Kerak bo'lgan payt: katalog yangilanganda (yangi tovar qo'shildi, nom
    o'zgardi) yoki bog'lagich yaxshilanganda. Aks holda eski qatorlar abadiy
    «bog'lanmagan» bo'lib qolardi va o'sha savdo tahlilga tushmasdi.
    """
    ensure_schema(db)
    index = catalog_index(db)
    where = "WHERE product_id = 0" if only_missing else ""
    rows = db.conn.execute(
        f"SELECT DISTINCT name FROM receipt_items {where}"
    ).fetchall()

    fixed = cleared = 0
    for (name,) in rows:
        pid = index.find(name)
        if not pid:
            if only_missing:
                continue
            # To'liq qayta bog'lashda: qoida qattiqlashtirilgan bo'lsa, ESKI
            # NOTO'G'RI bog'lanish o'chirilishi kerak. Aks holda «PROFIL J
            # 75/40» abadiy U-profilga yozilib qolardi — qayta bog'lash
            # faqat qo'shar, hech narsani tuzatmasdi.
            cur = db.conn.execute(
                "UPDATE receipt_items SET product_id = 0 "
                "WHERE name = ? AND product_id <> 0", (name,)
            )
            cleared += cur.rowcount
            continue
        cur = db.conn.execute(
            "UPDATE receipt_items SET product_id = ? WHERE name = ?"
            + (" AND product_id = 0" if only_missing else " AND product_id <> ?"),
            (pid, name) if only_missing else (pid, name, pid),
        )
        fixed += cur.rowcount
    db.conn.commit()
    return {"nomlar": len(rows), "bog'landi": fixed, "tozalandi": cleared}


def unmatched(db: MarketplaceDB, days: int = 30, limit: int = 20) -> list:
    """Hali ham bog'lanmagan nomlar — pul bo'yicha tartiblangan."""
    return db.conn.execute(
        """SELECT i.name, COUNT(DISTINCT i.msg_id), SUM(i.qty), SUM(i.amount)
             FROM receipt_items i JOIN receipts r ON r.msg_id = i.msg_id
            WHERE i.product_id = 0 AND r.day >= date('now', ?)
            GROUP BY i.name ORDER BY 4 DESC LIMIT ?""",
        (f"-{days} day", limit),
    ).fetchall()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Chek ajratgichi va bog'lagichi")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("relink", help="chek qatorlarini katalogga qayta bog'lash")
    p.add_argument("--all", action="store_true",
                   help="bog'langanlarini ham qayta tekshirish")
    p.set_defaults(cmd="relink")

    p = sub.add_parser("alias", help="chekdagi nomni tovarga QO'LDA bog'lash")
    p.add_argument("--name", required=True, help="chekdagi nom (aynan)")
    p.add_argument("--sku", required=True, help="katalog sku (artikul)")
    p.add_argument("--note", default="", help="izoh (nega shunday)")
    p.set_defaults(cmd="alias")

    p = sub.add_parser("fix-totals", help="eski cheklarning summasini tuzatish")
    p.set_defaults(cmd="fix-totals")

    p = sub.add_parser("unmatched", help="bog'lanmagan nomlar ro'yxati")
    p.add_argument("--days", type=int, default=30)
    p.set_defaults(cmd="unmatched")

    p = sub.add_parser("test", help="matndan chekni ajratib ko'rsatish")
    p.add_argument("--file", default="-", help="fayl ('-' = stdin)")
    p.set_defaults(cmd="test")

    args = ap.parse_args(argv)

    if args.cmd == "relink":
        with MarketplaceDB() as db:
            res = relink(db, only_missing=not args.all)
        print(f"Tekshirilgan nom: {res['nomlar']}   "
              f"bog'langan: {res["bog'landi"]}   "
              f"noto'g'ri bog'lanish tozalandi: {res.get('tozalandi', 0)}")
        return 0

    if args.cmd == "alias":
        with MarketplaceDB() as db:
            ensure_schema(db)
            row = db.conn.execute(
                "SELECT id, name FROM products WHERE sku = ?", (str(args.sku),)
            ).fetchone()
            if not row:
                print(f"sku {args.sku} katalogda topilmadi.")
                return 1
            db.conn.execute(
                "INSERT INTO receipt_aliases(name, product_id, note, created_at) "
                "VALUES (?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
                "product_id=excluded.product_id, note=excluded.note",
                (args.name, int(row[0]), args.note,
                 now_local().strftime("%Y-%m-%d %H:%M:%S")),
            )
            db.conn.commit()
            print(f"«{args.name}»  →  {args.sku} {row[1]}")
            res = relink(db, only_missing=False)
            print(f"Qayta bog'landi: {res["bog'landi"]} qator")
        return 0

    if args.cmd == "fix-totals":
        # Ajratgich tuzatilgunga qadar yozilgan cheklar uchun: nasiyaning
        # summasi 0 qolgan, qaytarishniki musbat. Qatorlardan qayta hisoblaymiz.
        with MarketplaceDB() as db:
            ensure_schema(db)
            zero = db.conn.execute("""
                UPDATE receipts SET
                    payable = (SELECT COALESCE(SUM(amount), 0) FROM receipt_items i
                                WHERE i.msg_id = receipts.msg_id),
                    total = (SELECT COALESCE(SUM(amount), 0) FROM receipt_items i
                              WHERE i.msg_id = receipts.msg_id)
                 WHERE COALESCE(payable, 0) = 0""").rowcount
            ret = db.conn.execute("""
                UPDATE receipts SET payable = -ABS(payable), total = -ABS(total)
                 WHERE kind = 'return' AND payable > 0""").rowcount
            db.conn.commit()
        print(f"Summasi tiklandi: {zero} chek   qaytarish belgisi: {ret} chek")
        return 0

    if args.cmd == "unmatched":
        with MarketplaceDB() as db:
            rows = unmatched(db, args.days)
        print(f"{'Nom':<46}{'chek':>6}{'dona':>8}{'pul':>13}")
        for name, n, q, amount in rows:
            print(f"{name[:46]:<46}{n:>6}{q:>8.0f}{amount:>13,.0f}")
        return 0

    text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
    r = parse(text)
    if not r:
        print("Tanilmadi (chek emas yoki ko'rinishi boshqacha).")
        return 1
    print(f"{r.kind}  №{r.doc_no}  {r.sold_at}  {r.branch}/{r.cashier}")
    for it in r.items:
        print(f"  {it['line_no']:>2}) {it['name'][:44]:<44} {it['qty']:>8.2f} "
              f"x {it['price']:>12,.2f} = {it['amount']:>13,.2f}")
    print(f"  Общий {r.total:,.2f} · Скидки {r.discount:,.2f} · К оплате {r.payable:,.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
