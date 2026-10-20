"""Ketish tezligi — tovar kuniga nechtadan sotilishini qoldiq tarixidan
hisoblaydi. Buyurtma miqdori shu raqamdan chiqadi.

Nega kerak: gateway savdo tarixini BERMAYDI. Sotuv faqat qoldiqning
kamayishidan bilinadi. Ikkita manba bor va aynan shu tartibda ishonchli:

  1. `stock_flow` — kun ICHIDA har 20 daqiqalik suratdan yig'ilgan kamayish.
     Kirim sotuvni yashira olmaydi (10 sotilib 50 kirim qilinsa ham 10 ko'rinadi).
     Faqat shu jadval to'ldirilgan kunlar uchun bor (2026-08-20 dan keyin).
  2. `stock_daily` — kunlik oxirgi surat farqi. Eski kunlar uchun yagona
     imkoniyat, lekin kirim bo'lgan kunda sotuvni pasaytirib ko'rsatadi.

MUHIM: o'rtacha SOTUV BO'LGAN kunlarga bo'linadi, tugab turgan kunlarga emas.
Coca-Cola tugab yotgan bo'lsa, o'sha kunlar 0 sotuv — ularni bo'linmaga qo'shsak
tezlik sun'iy ravishda tushib ketadi va yana kam buyurtma qilinadi. Aynan shu
xato pulni noto'g'ri yo'naltiradi.

Cheklov ochiq aytiladi: inventarizatsiya, buzilgan tovarni chiqarish yoki
qaytarish ham qoldiqni kamaytiradi — bu «sotuv» bo'lib ko'rinadi. Bir kunda
katta sakrash bo'lsa `spike` bayrog'i qo'yiladi.

Ishlatish:
    python -m marketplace.velocity --days 30 --top 20
"""

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional

from marketplace import config
from marketplace.db import MarketplaceDB, now_local


# Cheklardan hisoblash uchun kamida shuncha kunlik chek tarixi kerak. Kamrog'i
# bo'lsa qoldiq usuli ishlatiladi — yarim kunlik chek butun tezlikni buzardi.
MIN_RECEIPT_DAYS = 5

# Tezlik «sotuvda turgan kunlar»ga bo'linadi — tugab yotgan kunlar talabni
# yashirmasin uchun. Lekin tovar deyarli DOIM tugagan bo'lsa, bo'linma juda
# kichik bo'lib qoladi va bitta tasodifiy savdo «kuniga bitta» ga aylanadi
# (real misol: 30 kunda 1 dona sotilgan tovarga 17 dona buyurtma chiqqan edi).
# Shuning uchun bo'linma quyidan cheklanadi: kamida shuncha kun kuzatilgan
# deb hisoblanadi. Uzoq kuzatilgan tovarlarga bu ta'sir qilmaydi.
MIN_OBSERVED_DAYS = 7


def _date(s: str) -> date:
    return date.fromisoformat(s)


@dataclass
class Rate:
    """Bitta tovarning ketish tezligi va uni qanchalik ishonch bilan aytish
    mumkinligi."""

    product_id: int
    sku: str
    name: str
    stock: float = 0.0
    price: float = 0.0
    rate: float = 0.0            # kuniga o'rtacha ketish (sotuv bo'lgan kunlar bo'yicha)
    sold_total: float = 0.0
    received_total: float = 0.0
    avail_days: int = 0          # tovar sotuvda TURGAN kunlar (bo'linma)
    span_days: int = 0           # kuzatuv oynasi
    events: int = 0              # nechta kunda harakat bo'lgan
    stockout_days: int = 0       # oxiridan boshlab necha kun 0 turibdi
    last_move_day: str = ""
    biggest_day: float = 0.0     # eng katta bir kunlik kamayish
    flow_days: int = 0           # nechta kun aniq (kun ichi) manbadan olindi
    source: str = "qoldiq"       # "chek" (aniq savdo) | "qoldiq" (taxmin)
    rate_all: float = 0.0        # yirik savdolar BILAN o'rtacha (solishtirish uchun)
    bulk_qty: float = 0.0        # yirik savdolarda ketgan jami dona
    bulk_count: int = 0          # nechta yirik savdo bo'lgan
    bulk_max: float = 0.0        # eng kattasi
    daily: Dict[str, float] = field(default_factory=dict)

    @property
    def spike(self) -> bool:
        """O'rtachaga to'liq ishonib bo'lmasligini bildiradi.

        Chek manbasida: yirik (bir martalik) savdo aniqlangan — miqdor allaqachon
        undan tozalangan, lekin foydalanuvchi buni bilib turishi kerak.
        Qoldiq manbasida: bir kunlik sakrash umumiy ketishning yarmidan ko'pi
        (odatda inventarizatsiya yoki ulgurji savdo)."""
        if self.source == "chek":
            return self.bulk_count > 0
        return self.events >= 2 and self.sold_total > 0 and (
            self.biggest_day / self.sold_total > 0.6
        )

    @property
    def bulk_note(self) -> str:
        """«2× yirik savdo, 210 dona» — hisobotda ko'rsatish uchun."""
        if not self.bulk_count:
            return ""
        return f"{self.bulk_count}× yirik savdo, {self.bulk_qty:.0f} dona"

    @property
    def days_left(self) -> float:
        """Qoldiq necha kunga yetadi. Tezlik 0 bo'lsa — cheksiz.

        Manfiy qoldiq (ombor teshigi) 0 deb olinadi: «-5 kunga yetadi» degan
        raqamning ma'nosi yo'q. Manfiy sonning o'zi «qoldiq» ustunida ko'rinadi.
        """
        if self.rate <= 0:
            return float("inf")
        return max(0.0, self.stock) / self.rate

    @property
    def thin(self) -> bool:
        """Tovar juda kam kunda sotuvda turgan — tezlik ishonchsiz."""
        return 0 < self.avail_days < MIN_OBSERVED_DAYS

    @property
    def confidence(self) -> str:
        # Chek manbasi — haqiqiy savdo, taxmin emas: kamroq kun ham yetarli.
        if self.source == "chek":
            if self.avail_days >= 10 and self.events >= 3:
                return "yuqori"
            if self.avail_days >= 4 and self.events >= 1:
                return "o'rta"
            return "past"
        if self.avail_days >= 14 and self.events >= 4:
            return "yuqori"
        if self.avail_days >= 7 and self.events >= 2:
            return "o'rta"
        return "past"


def _series(db: MarketplaceDB, since: str, until: str, branch: str) -> Dict[int, List[dict]]:
    rows = db.conn.execute(
        """SELECT product_id, day, stock, price
             FROM stock_daily
            WHERE branch = ? AND day >= ? AND day <= ?
            ORDER BY product_id, day""",
        (branch, since, until),
    ).fetchall()
    out: Dict[int, List[dict]] = defaultdict(list)
    for r in rows:
        out[int(r["product_id"])].append(
            {"day": r["day"], "stock": float(r["stock"]), "price": float(r["price"])}
        )
    return out


def _flow(db: MarketplaceDB, since: str, until: str, branch: str) -> Dict[int, Dict[str, dict]]:
    rows = db.conn.execute(
        """SELECT product_id, day, sold, received
             FROM stock_flow
            WHERE branch = ? AND day >= ? AND day <= ?""",
        (branch, since, until),
    ).fetchall()
    out: Dict[int, Dict[str, dict]] = defaultdict(dict)
    for r in rows:
        out[int(r["product_id"])][r["day"]] = {
            "sold": float(r["sold"]),
            "received": float(r["received"]),
        }
    return out


def _receipt_sales(db: MarketplaceDB, since: str, until: str) -> tuple:
    """Cheklardan kunlik savdo: ({product_id: {kun: dona}}, birinchi_kun, oxirgi_kun).

    Qaytarish (`ВОЗВРАТ`) manfiy yozilgani uchun SUM o'zi sof savdoni beradi.
    Jadval yo'q bo'lsa — bo'sh qaytadi va chaqiruvchi qoldiq usuliga o'tadi.
    """
    try:
        # Kunlik yig'indi EMAS, har bir chek qatori alohida — yirik bitta
        # savdoni oddiy kundalik savdodan ajratish uchun (pastda `_split_bulk`).
        rows = db.conn.execute(
            """SELECT i.product_id, r.day, i.qty
                 FROM receipt_items i JOIN receipts r ON r.msg_id = i.msg_id
                WHERE i.product_id > 0 AND r.day >= ? AND r.day <= ?""",
            (since, until),
        ).fetchall()
        span = db.conn.execute(
            "SELECT MIN(day), MAX(day), COUNT(DISTINCT day) FROM receipts "
            "WHERE day >= ? AND day <= ?",
            (since, until),
        ).fetchone()
    except Exception:
        return {}, None, None          # cheklar hali yig'ilmagan
    if not rows or not span or (span[2] or 0) < MIN_RECEIPT_DAYS:
        return {}, None, None

    # {product_id: {"daily": {kun: dona}, "lines": [dona, ...]}}
    out: Dict[int, dict] = defaultdict(lambda: {"daily": {}, "lines": []})
    for pid, day, q in rows:
        q = float(q or 0)
        rec = out[int(pid)]
        rec["daily"][day] = rec["daily"].get(day, 0.0) + q
        if q > 0:
            rec["lines"].append(q)
    return out, span[0], span[1]


# Bitta chekda ketgan miqdor oyning umumiy ketishidan shuncha ulushni tashkil
# qilsa — bu YIRIK savdo (pudratchi/ustaning bir martalik xaridi), kundalik
# talab emas. O'rtachaga qo'shilsa, keyingi oyga ham shuncha zaxira
# buyurtma qilinardi — pul javonda qotardi.
BULK_SHARE = 0.30
# Kichik hajmli tovarda bitta savdo tabiiy ravishda katta ulush bo'ladi
# (oyiga 3 ta sotilsa, bittasi 33%). Shuning uchun quyi chegara ham bor.
BULK_MIN_QTY = 5.0
# Odatdagi savdodan shuncha barobar katta bo'lsa — chetdagi (outlier) savdo.
BULK_FACTOR = 4.0
# Epizodik talab: oynadagi savdoning shuncha ulushi shuncha KUNGA to'plangan
# bo'lsa — bu obyekt buyurtmasi, kundalik talab emas.
EPISODE_DAYS = 2
EPISODE_SHARE = 0.8



def _median(vals: List[float]) -> float:
    v = sorted(vals)
    n = len(v)
    if not n:
        return 0.0
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def _split_bulk(lines: List[float]) -> tuple:
    """(yirik_savdolar, oddiy_jami) — chek qatorlarini ikkiga ajratadi.

    Ikki shart birga: savdo (1) oyning umumiy ketishidan katta ulush bo'lsa VA
    (2) shu tovarning ODATDAGI savdosidan bir necha barobar katta bo'lsa.

    Ikkinchi shart nega kerak: tovar doim 10 tadan paketlab sotilsa, oyiga 3
    marta sotilganda har biri 33% bo'ladi — ulush bo'yicha «yirik» ko'rinadi.
    Aslida bu ODATDAGI savdo va uni chiqarib tashlasak, tezlik 0 ga tushib,
    tovar buyurtmaga umuman chiqmay qolardi.

    Savdo 1-2 tagina bo'lsa mediana ishonchsiz — u holda faqat ulush ishlaydi
    (oyiga bitta 80 donalik xarid — bu aniq bir martalik buyurtma).
    """
    total = sum(lines)
    if total <= 0:
        return [], 0.0
    typical = _median(lines)
    bulk = []
    for q in lines:
        if q < BULK_MIN_QTY or q < BULK_SHARE * total:
            continue
        if len(lines) >= 3 and q < BULK_FACTOR * typical:
            continue          # odatdagi hajm — chetdagi savdo emas
        bulk.append(q)
    return bulk, total - sum(bulk)


def _latest_stock(db: MarketplaceDB, branch: str) -> Dict[int, dict]:
    """Eng oxirgi suratdagi qoldiq/narx.

    Nega alohida: davr tanlanganda (masalan «iyul oyi») ketish tezligi O'SHA
    oyники bo'lishi kerak, lekin qoldiq HOZIRGI bo'lishi shart — buyurtma
    bugungi javonga qarab beriladi, o'tgan oyникига emas.
    """
    day = db.conn.execute(
        "SELECT MAX(day) d FROM stock_daily WHERE branch = ?", (branch,)
    ).fetchone()["d"]
    if not day:
        return {}
    return {
        int(r["product_id"]): {"stock": float(r["stock"]), "price": float(r["price"])}
        for r in db.conn.execute(
            "SELECT product_id, stock, price FROM stock_daily WHERE branch=? AND day=?",
            (branch, day),
        )
    }


def _meta(db: MarketplaceDB) -> Dict[int, dict]:
    return {
        int(r["id"]): {"name": r["name"] or "", "sku": str(r["sku"] or "")}
        for r in db.conn.execute("SELECT id, name, sku FROM products")
    }


def _from_receipts(r: Rate, rows: List[dict], rec: dict,
                   rec_from: str, rec_to: str) -> None:
    """Tezlikni CHEKLARDAN hisoblaydi (aniq savdo), qoldiqni esa mavjudlikni
    aniqlash uchun ishlatadi.

    Oyna chek tarixi bilan kesishgan qismga qisqartiriladi: cheklar boshlanmagan
    kunlarni «0 sotildi» deb hisoblash tezlikni sun'iy pasaytirardi.
    """
    sold_by_day = rec.get("daily") or {}
    # Yirik (bir martalik) savdolarni ajratamiz — ular kundalik talab emas.
    bulk, _regular = _split_bulk(rec.get("lines") or [])
    r.bulk_count = len(bulk)
    r.bulk_qty = sum(bulk)
    r.bulk_max = max(bulk) if bulk else 0.0

    stock_by_day = {row["day"]: row["stock"] for row in rows}
    # Davr — CHEKLAR oralig'i. Qoldiq tarixi faqat «o'sha kuni sotuvda
    # bormidi» degan savolga javob beradi (surat yo'q kun = bor deb olinadi).
    # Ilgari davr qoldiq tarixi bilan kesishtirilardi: server ko'chib qoldiq
    # tarixi qayta boshlanganda (2026-10-20, 1 kunlik) 30 kunlik chekdan faqat
    # bugungisi sanalar, 6854 tovardan 122 tasida tezlik chiqardi.
    start, end = rec_from, rec_to
    if start > end:
        return

    d, last_d = _date(start), _date(end)
    while d <= last_d:
        day = d.isoformat()
        sold = sold_by_day.get(day, 0.0)
        stock = stock_by_day.get(day)

        # Kun hisobga olinadimi: tovar sotuvda turgan bo'lsa. Sotilgan bo'lsa
        # — demak turgan. Surat yo'q kunni ham turgan deb olamiz (aks holda
        # sinxron uzilgan kun tezlikni ko'taradi).
        available = sold > 0 or stock is None or stock > 0
        if available:
            r.avail_days += 1
        if sold > 0:
            r.sold_total += sold
            r.events += 1
            r.last_move_day = day
            r.daily[day] = sold
            r.biggest_day = max(r.biggest_day, sold)
        d += timedelta(days=1)

    r.span_days = (last_d - _date(start)).days + 1
    r.source = "chek"

    # EPIZODIK TALAB: butun oynadagi savdo 1-2 kunga to'plangan bo'lsa, bu
    # kundalik talab emas — bitta obyekt/pudratchi uchun olingan.
    #
    # Nega alohida qoida kerak: `_split_bulk` har SAVDONI alohida o'lchaydi.
    # Obyekt buyurtmasi esa ko'p marta bo'lib olinadi (300 donadan 7 marta,
    # 2 kun ichida) — har biri alohida katta emas, lekin birgalikda butun
    # oyning savdosi. Shu holat «kuniga 185 dona» degan xulosaga va 3096
    # donalik buyurtmaga olib kelgan edi.
    if r.daily and r.avail_days >= MIN_OBSERVED_DAYS:
        tops = sorted(r.daily.values(), reverse=True)[:EPISODE_DAYS]
        if sum(tops) >= EPISODE_SHARE * r.sold_total and sum(tops) >= BULK_MIN_QTY:
            r.bulk_count = max(r.bulk_count, len(tops))
            r.bulk_qty = max(r.bulk_qty, sum(tops))
            r.bulk_max = max(r.bulk_max, tops[0])

    if r.avail_days > 0:
        # `rate_all` — hamma savdo bilan (solishtirish uchun ko'rsatiladi).
        # `rate` — yirik savdolarsiz: buyurtma AYNAN shundan hisoblanadi,
        # aks holda bitta pudratchining xaridi keyingi oyga ham zaxira
        # bo'lib buyurtma qilinardi.
        div = max(r.avail_days, min(r.span_days, MIN_OBSERVED_DAYS))
        r.rate_all = r.sold_total / div
        r.rate = max(0.0, r.sold_total - r.bulk_qty) / div


def window(days: int = 30, since: str = "", until: str = "") -> tuple:
    """(boshlanish, tugash) — YYYY-MM-DD. Sana berilmasa oxirgi `days` kun.

    Tugash sanasi BUGUNDAN nariga o'tmaydi: kelajak sana tanlansa oynada
    savdo bo'lmaydi va ro'yxat sababsiz bo'sh chiqardi (foydalanuvchi buni
    «ishlamayapti» deb tushunadi). Boshlanish tugashdan keyin bo'lsa ham
    tuzatiladi.
    """
    today = now_local().strftime("%Y-%m-%d")
    until = (until or "").strip() or today
    if until > today:
        until = today
    since = (since or "").strip()
    if not since:
        since = (_date(until) - timedelta(days=max(1, days) - 1)).isoformat()
    if since > until:
        since = (_date(until) - timedelta(days=max(1, days) - 1)).isoformat()
    return since, until


def data_days(db: MarketplaceDB, since: str, until: str) -> int:
    """Oynada nechta kunda savdo ma'lumoti bor — ogohlantirish uchun."""
    try:
        n = db.conn.execute(
            "SELECT COUNT(DISTINCT day) FROM receipts WHERE day >= ? AND day <= ?",
            (since, until),
        ).fetchone()[0]
        if n:
            return int(n)
    except Exception:
        pass
    row = db.conn.execute(
        "SELECT COUNT(DISTINCT day) FROM stock_daily WHERE day >= ? AND day <= ?",
        (since, until),
    ).fetchone()
    return int(row[0] or 0)


def compute(
    db: MarketplaceDB, days: int = 30, branch: Optional[str] = None,
    since: str = "", until: str = "",
) -> Dict[int, Rate]:
    """Har bir tovar uchun `Rate`. Kalit — YesPOS product_id.

    Oyna: `since`/`until` berilsa o'sha sanalar, aks holda oxirgi `days` kun.
    Qoldiq esa DOIM hozirgi — oyna faqat ketish tezligiga taalluqli.
    """
    branch = str(branch if branch is not None else config.BRANCH)
    since, until = window(days, since, until)
    series = _series(db, since, until, branch)
    flows = _flow(db, since, until, branch)
    meta = _meta(db)
    stock_now = _latest_stock(db, branch)
    # Cheklar bor bo'lsa — SAVDO o'sha yerdan olinadi (aniq), qoldiq esa
    # baribir `stock_daily` dan (chek qoldiqni bilmaydi).
    receipts, rec_from, rec_to = _receipt_sales(db, since, until)

    out: Dict[int, Rate] = {}
    for pid, rows in series.items():
        info = meta.get(pid, {})
        last = rows[-1]
        cur = stock_now.get(pid) or {"stock": last["stock"], "price": last["price"]}
        r = Rate(
            product_id=pid,
            sku=info.get("sku", ""),
            name=info.get("name", "?"),
            stock=cur["stock"],
            price=cur["price"] or last["price"],
        )
        pflow = flows.get(pid, {})

        if receipts:
            _from_receipts(r, rows, receipts.get(pid) or {}, rec_from, rec_to)
        elif len(rows) >= 2:
            r.span_days = (_date(last["day"]) - _date(rows[0]["day"])).days + 1
            for i in range(1, len(rows)):
                prev, cur = rows[i - 1], rows[i]
                gap = max(1, (_date(cur["day"]) - _date(prev["day"])).days)

                f = pflow.get(cur["day"])
                if f is not None:
                    sold, recv = f["sold"], f["received"]
                    r.flow_days += 1
                else:
                    # Zaxira: kunlik farq. Kirim bo'lgan kunda sotuv ko'rinmaydi.
                    sold = max(0.0, prev["stock"] - cur["stock"])
                    recv = max(0.0, cur["stock"] - prev["stock"])

                r.sold_total += sold
                r.received_total += recv
                if sold > 0:
                    r.events += 1
                    r.last_move_day = cur["day"]
                    r.daily[cur["day"]] = sold
                    r.biggest_day = max(r.biggest_day, sold)

                # Bo'linma: tovar sotuvda turgan kunlar. Boshlanishida qoldiq
                # bo'lgan yoki sotuv ko'rilgan kun — «turgan» hisoblanadi.
                if prev["stock"] > 0 or sold > 0:
                    r.avail_days += gap

        # Tugab yotgan kunlar — oxiridan orqaga.
        for row in reversed(rows):
            if row["stock"] > 0:
                break
            r.stockout_days += 1

        if r.avail_days > 0 and r.source != "chek":
            div = max(r.avail_days, min(r.span_days, MIN_OBSERVED_DAYS))
            r.rate = r.sold_total / div
            r.rate_all = r.rate
        out[pid] = r

    return out


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Tovarlarning ketish tezligi")
    ap.add_argument("--days", type=int, default=30, help="kuzatuv oynasi (kun)")
    ap.add_argument("--since", default="", help="boshlanish sanasi YYYY-MM-DD")
    ap.add_argument("--until", default="", help="tugash sanasi YYYY-MM-DD")
    ap.add_argument("--top", type=int, default=25, help="nechtasini ko'rsatish")
    args = ap.parse_args(argv)

    with MarketplaceDB() as db:
        rates = compute(db, days=args.days, since=args.since, until=args.until)

    moving = sorted(
        (r for r in rates.values() if r.rate > 0), key=lambda r: r.rate, reverse=True
    )
    print(f"Tovar: {len(rates)}   harakatdagi: {len(moving)}   oyna: {args.days} kun")
    print(f"{'SKU':<8} {'Nom':<34} {'kun/dona':>9} {'qoldiq':>8} {'yetadi':>8}  ishonch")
    print("-" * 78)
    for r in moving[: args.top]:
        left = "∞" if r.days_left == float("inf") else f"{r.days_left:.0f} kun"
        mark = " ⚠" if r.spike else ""
        print(
            f"{r.sku:<8} {r.name[:34]:<34} {r.rate:>9.2f} {r.stock:>8.0f} {left:>8}"
            f"  {r.confidence}{mark}"
        )
    print("\n⚠ = bitta kunlik sakrash o'rtachani buzayotgan bo'lishi mumkin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
