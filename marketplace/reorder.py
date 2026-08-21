"""Buyurtma ro'yxati — YETKAZIB BERUVCHI bo'yicha, ketish tezligiga qarab.

Muammo: «ko'z bilan» buyurtma berilganda tugab yotgan tovar unutiladi, ko'p
turgani esa yana keltiriladi. Pul javonda qotadi, sotuv esa yo'qoladi.

Hisob oddiy va tekshirsa bo'ladigan:

    maqsad  = kunlik_ketish × (yetkazish_kuni + zaxira_kuni)
    kerak   = maqsad − hozirgi_qoldiq          (manfiy bo'lsa — 0)

Misol (foydalanuvchi bergan): Coca-Cola kuniga 1 dona, qoldiq 0 → yetkazish 3
kun + zaxira 14 kun = 17 dona kerak. Fanta kuniga 8 dona, qoldiq 10 → maqsad
136, kerak 126. Ya'ni «Fanta bor ekan» degan xulosa noto'g'ri: u 1.2 kunda
tugaydi.

Holatlar:
  SHOSHILINCH — qoldiq 0, lekin sotilardi. Har kun savdo yo'qotilmoqda.
  BUYURTMA    — yetkazish muddatidan kam qoldi.
  YETADI      — hozircha kerak emas.
  KO'P        — juda uzoqqa yetadi, pul qotgan. Buyurtma qilinmaydi.
  MUZLAGAN    — qoldiq bor, harakat yo'q. Buyurtma qilinmaydi.
  NOANIQ      — ma'lumot yetarli emas (yangi tovar yoki qisqa tarix).

Ishlatish:
    python -m marketplace.reorder                      # barcha yetkazib beruvchilar
    python -m marketplace.reorder --supplier "Coca-Cola"
    python -m marketplace.reorder --lead 5 --cover 21  # muddatlarni o'zgartirish
    python -m marketplace.reorder --csv                # data/exports/ ga yozadi
    python -m marketplace.reorder --unassigned         # biriktirilmagan harakatdagi tovarlar
"""

import argparse
import csv
import math
import os
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional

from marketplace import config
from marketplace.db import MarketplaceDB, now_local
from marketplace.velocity import Rate, compute

# Standart muddatlar — buyurtma berilgandan tovar javonga chiqquncha ketadigan
# kun, va undan keyin qancha kunga zaxira ushlanadi.
LEAD_DAYS = int(os.getenv("REORDER_LEAD_DAYS", "3"))
COVER_DAYS = int(os.getenv("REORDER_COVER_DAYS", "14"))
# Shuncha kundan uzoqqa yetadigan qoldiq — ortiqcha (pul qotgan).
OVERSTOCK_DAYS = int(os.getenv("REORDER_OVERSTOCK_DAYS", "60"))

URGENT, ORDER, ENOUGH, EXCESS, FROZEN, UNKNOWN, PROJECT = (
    "SHOSHILINCH", "BUYURTMA", "YETADI", "KO'P", "MUZLAGAN", "NOANIQ", "YIRIK SAVDO"
)
_ORDER_RANK = {URGENT: 0, ORDER: 1, UNKNOWN: 2, ENOUGH: 3,
               PROJECT: 4, EXCESS: 5, FROZEN: 6}


@dataclass
class Line:
    """Buyurtma ro'yxatining bitta qatori."""

    rate: Rate
    supplier_id: int
    supplier: str
    status: str
    need: int              # buyurtma qilinadigan miqdor (butunga yaxlitlangan)
    cost: float            # tannarx (bo'lmasa — sotuv narxi)
    lost_per_day: float    # tugab turgani uchun kuniga yo'qotilayotgan savdo
    lead: int = 0          # shu tovar uchun ishlatilgan yetkazish muddati
    cycle: int = 0         # shu tovar uchun ishlatilgan kelish oralig'i

    @property
    def amount(self) -> float:
        return self.need * self.cost


def _pos_suppliers(pos_db: str) -> tuple:
    """pos.db dan biriktiruvni o'qiydi.

    Ulanish kaliti — Chinor `products.id` == YesPOS `sku` (pos_bridge.py bilan
    bir xil). Qaytaradi: (sku → {supplier_id, supplier, cost}, id → nom).
    """
    if not os.path.exists(pos_db):
        return {}, {}
    conn = sqlite3.connect(f"file:{pos_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        sups = {}
        for r in conn.execute("SELECT * FROM suppliers"):
            try:
                lead, cycle = int(r["lead_days"] or 0), int(r["cycle_days"] or 0)
            except (IndexError, KeyError):
                lead = cycle = 0  # ustunlar hali qo'shilmagan eski baza
            sups[int(r["id"])] = {"name": r["name"], "lead": lead, "cycle": cycle}
        rows = conn.execute(
            "SELECT id, supplier_id, cost_price FROM products"
        ).fetchall()
    except sqlite3.Error:
        conn.close()
        return {}, {}

    # Bitta tovar bir nechta firmada bo'lishi mumkin — bog'lanishlar alohida
    # jadvalda (`product_suppliers`). Jadval bo'lmasa (eski baza) faqat
    # `products.supplier_id` ishlatiladi.
    links: Dict[str, list] = {}
    try:
        for pid, sid, price in conn.execute(
            "SELECT product_id, supplier_id, COALESCE(price,0) FROM product_suppliers"
        ):
            links.setdefault(str(pid), []).append((int(sid), float(price or 0)))
    except sqlite3.Error:
        pass
    finally:
        conn.close()

    by_sku = {}
    for r in rows:
        sku = str(r["id"])
        cost = float(r["cost_price"] or 0)
        pairs = list(links.get(sku) or [])
        primary = int(r["supplier_id"] or 0)
        if primary and primary not in [p[0] for p in pairs]:
            pairs.append((primary, 0.0))
        if not pairs:
            pairs = [(0, 0.0)]
        # Summa pos.db tannarxidan hisoblanadi. Nakladnoydagi firma narxi
        # ATAYLAB ishlatilmaydi: u tez eskiradi (narx tez-tez o'zgaradi),
        # shuning uchun unga qarab «qaysi firma arzon» deyish adashtiradi.
        by_sku[sku] = [
            {
                "supplier_id": sid,
                "supplier": sups.get(sid, {}).get("name", ""),
                "lead": sups.get(sid, {}).get("lead", 0),
                "cycle": sups.get(sid, {}).get("cycle", 0),
                "cost": cost,
            }
            for sid, _sup_price in pairs
        ]
    return by_sku, sups


def classify(r: Rate, lead: int, cover: int) -> tuple:
    """(holat, kerakli miqdor) — bitta tovar uchun qaror.

    Manfiy qoldiq — ombor hisobidagi TESHIK (kirim qilinmay sotilgan), haqiqiy
    ehtiyoj emas. Uni shundayligicha formulaga qo'shsak, «-49» qoldiq 49 dona
    QO'SHIMCHA buyurtmaga aylanardi — ya'ni hisob xatosi uchun pul to'langan
    bo'lardi. Shuning uchun hisobda 0 deb olinadi; haqiqiy manfiy son esa
    hisobotda ko'rinib turadi va inventarizatsiya kerakligini bildiradi.
    """
    target_days = lead + cover
    stock = max(0.0, r.stock)

    # Ma'lumot yetarli emas: harakat ko'rilmagan va tarix qisqa.
    if r.rate <= 0:
        # Tovar sotilgan, lekin FAQAT yirik (bir martalik) buyurtmalarda.
        # «MUZLAGAN» deb belgilash noto'g'ri bo'lardi — u qimirlamagani yo'q,
        # shunchaki kundalik talabi yo'q. Buyurtma ham qilinmaydi: keyingi
        # shunday xarid qachon bo'lishini faqat do'kon biladi.
        if r.bulk_count:
            return PROJECT, 0
        if r.stock <= 0:
            # Qoldiq ham yo'q, harakat ham yo'q — tovar umuman ishlamayapti.
            return (UNKNOWN if r.avail_days < 7 else FROZEN), 0
        if r.avail_days < 7:
            return UNKNOWN, 0
        return FROZEN, 0

    need_raw = r.rate * target_days - stock

    # DALIL KUCHSIZ BO'LSA — miqdor kuzatilgan savdodan oshmaydi.
    #
    # Tezlik «sotuvda turgan kunlar»ga bo'lingani uchun kam kuzatilgan tovarda
    # u yuqori chiqadi: 30 kunda 1 dona sotilgan 1.85 mln so'mlik asbobga
    # 3 dona (5.5 mln) buyurtma yozilardi. Bitta savdo bunday xarajatni
    # oqlamaydi. Ko'p marta sotilgan tovarga bu cheklov TEGMAYDI.
    weak = r.thin or r.events <= 2
    cap = max(1.0, r.sold_total - r.bulk_qty) if weak else None

    def _qty(v: float) -> int:
        v = math.ceil(v)
        return int(min(v, cap)) if cap is not None else int(v)

    if stock <= 0:
        # Tugagan-u sotilardi — eng qimmat holat: har kun savdo yo'qoladi.
        return URGENT, max(1, _qty(need_raw))
    if r.days_left > OVERSTOCK_DAYS:
        return EXCESS, 0
    if r.days_left <= target_days:
        return ORDER, max(1, _qty(need_raw)) if need_raw > 0 else 0
    return ENOUGH, 0


def build(
    db: MarketplaceDB,
    days: int = 30,
    lead: int = LEAD_DAYS,
    cover: int = COVER_DAYS,
    pos_db: Optional[str] = None,
    since: str = "",
    until: str = "",
) -> List[Line]:
    """Barcha tovarlar uchun qatorlarni tuzadi (filtrlanmagan).

    `since`/`until` — ketish tezligi qaysi davrdan hisoblanishi. Berilmasa
    oxirgi `days` kun.
    """
    rates = compute(db, days=days, since=since, until=until)
    by_sku, _ = _pos_suppliers(pos_db or config.POS_DB)

    lines: List[Line] = []
    for r in rates.values():
        # Tovar bir nechta firmada bo'lsa — HAR BIRI uchun alohida qator.
        # Miqdor ham har biriga alohida hisoblanadi, chunki muddat har xil:
        # haftada keladigan firmadan kamroq, oyda keladiganidan ko'proq olinadi.
        for link in by_sku.get(r.sku) or [{}]:
            s_lead = link.get("lead") or lead
            s_cover = link.get("cycle") or cover
            status, need = classify(r, s_lead, s_cover)
            cost = link.get("cost") or 0.0
            if cost <= 0:
                # Tannarxni API bermaydi; pos.db da ham bo'lmasa sotuv narxi
                # bilan taxmin qilamiz — summa haqiqiydan yuqoriroq chiqadi.
                cost = r.price
            lines.append(
                Line(
                    rate=r,
                    supplier_id=int(link.get("supplier_id") or 0),
                    supplier=link.get("supplier") or "",
                    status=status,
                    need=need,
                    cost=cost,
                    lost_per_day=(r.rate * r.price) if r.stock <= 0 and r.rate > 0 else 0.0,
                    lead=s_lead,
                    cycle=s_cover,
                )
            )
    return lines


def group_by_supplier(lines: List[Line]) -> Dict[int, List[Line]]:
    out: Dict[int, List[Line]] = defaultdict(list)
    for ln in lines:
        out[ln.supplier_id].append(ln)
    for group in out.values():
        group.sort(
            key=lambda l: (_ORDER_RANK[l.status], -l.amount, -l.rate.rate)
        )
    return out


# ─── Chiqarish ───────────────────────────────────────────────────────────────

def _money(v: float) -> str:
    return f"{v:,.0f}".replace(",", " ")


def _print_group(title: str, group: List[Line], show_all: bool) -> dict:
    orderable = [l for l in group if l.need > 0]
    total = sum(l.amount for l in orderable)
    lost = sum(l.lost_per_day for l in group)

    when = f"{group[0].lead}+{group[0].cycle} kun" if group else ""
    print(f"\n{'═' * 86}")
    print(f"{title}   ({len(group)} tovar, buyurtmaga {len(orderable)} ta"
          + (f", muddat {when}" if when else "") + ")")
    print("═" * 86)
    if not orderable and not show_all:
        print("  Buyurtma kerak emas.")
        return {"total": 0.0, "count": 0, "lost": lost}

    print(
        f"  {'Holat':<11} {'SKU':<7} {'Nom':<30} {'kun/dona':>8} {'qoldiq':>7}"
        f" {'yetadi':>7} {'BUYURT':>7}  yirik savdo"
    )
    print("  " + "-" * 84)
    for l in group if show_all else orderable:
        r = l.rate
        left = "—" if r.days_left == float("inf") else f"{r.days_left:.0f}k"
        mark = "⚠" if r.spike else (" " if r.confidence == "yuqori" else "·")
        note = ""
        if r.bulk_count:
            # Yirik savdo miqdordan CHIQARILGAN — lekin ko'rinib tursin:
            # yana shunday xarid kutilsa, odam o'zi qo'shib qo'yadi.
            note = (f"{r.bulk_count}× {r.bulk_qty:.0f} dona "
                    f"(o'rtacha {r.rate_all:.2f} edi)")
        print(
            f"  {l.status:<11} {r.sku:<7} {r.name[:30]:<30} {r.rate:>8.2f}"
            f" {r.stock:>7.0f} {left:>7} {l.need:>7}{mark}  {note}"
        )
    print("  " + "-" * 84)
    print(f"  Buyurtma summasi (taxminiy): {_money(total)} so'm")
    holes = [l for l in group if l.rate.stock < 0]
    if holes:
        # Manfiy qoldiq buyurtmaga qo'shilmaydi (0 deb olinadi), lekin uni
        # aytmaslik kerak emas: ombor hisobi bilan javon mos kelmayapti.
        print(f"  ⛔ {len(holes)} tovarda MANFIY qoldiq — ombor hisobida teshik, "
              f"inventarizatsiya kerak")
    if lost > 0:
        print(f"  Tugagani uchun kunlik yo'qotilayotgan savdo: {_money(lost)} so'm")
    return {"total": total, "count": len(orderable), "lost": lost}


def _write_csv(title: str, group: List[Line], out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in title).strip()
    path = os.path.join(
        out_dir, f"buyurtma-{safe or 'nomalum'}-{now_local():%Y%m%d}.csv"
    )
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow([
            "Holat", "SKU", "Nom", "Kunlik ketish", "Qoldiq", "Necha kunga yetadi",
            "BUYURTMA", "Tannarx", "Summa", "Ishonch", "Kuzatuv kuni",
        ])
        for l in group:
            if l.need <= 0:
                continue
            r = l.rate
            w.writerow([
                l.status, r.sku, r.name, f"{r.rate:.2f}", f"{r.stock:.0f}",
                "" if r.days_left == float("inf") else f"{r.days_left:.0f}",
                l.need, f"{l.cost:.0f}", f"{l.amount:.0f}",
                r.confidence, r.avail_days,
            ])
    return path


def telegram_text(groups: Dict[int, List[Line]], lead: int, cover: int,
                  per_supplier: int = 10) -> str:
    """Telegram uchun qisqartirilgan matn — faqat buyurtma kerak bo'lganlar.

    To'liq ro'yxat CSV da qoladi: xabarda 4096 belgi cheklovi bor va uzun
    ro'yxat baribir o'qilmaydi. Bu yerda diqqat talab qiladigan qismi.
    """
    L = [f"<b>📦 Buyurtma ro'yxati</b> (standart {lead}+{cover} kun)"]
    grand = lost = 0.0
    count = 0
    for _sid, group in sorted(
        groups.items(), key=lambda kv: (kv[0] == 0, (kv[1][0].supplier or "").lower())
    ):
        need = [l for l in group if l.need > 0]
        if not need:
            continue
        title = group[0].supplier or "Biriktirilmagan"
        total = sum(l.amount for l in need)
        grand += total
        count += len(need)
        L.append(f"\n<b>{title}</b> ({need[0].lead}+{need[0].cycle} kun) — "
                 f"{len(need)} pozitsiya, ~{_money(total)} so'm")
        for l in need[:per_supplier]:
            flag = "🔴" if l.status == URGENT else "🟡"
            L.append(f"{flag} {l.rate.name[:34]} — <b>{l.need}</b> "
                     f"(kuniga {l.rate.rate:.1f}, qoldiq {l.rate.stock:.0f})")
        if len(need) > per_supplier:
            L.append(f"… yana {len(need) - per_supplier} ta")
        lost += sum(l.lost_per_day for l in group)

    if count == 0:
        return "📦 Buyurtma ro'yxati: hozircha hech narsa kerak emas."
    L.append(f"\n<b>Jami:</b> {count} pozitsiya, ~{_money(grand)} so'm")
    if lost > 0:
        L.append(f"⚠️ Tugagan tovarlar tufayli kuniga ~{_money(lost)} so'm savdo yo'qolmoqda")
    return "\n".join(L)


def audit(lines: List[Line], window_days: int = 30) -> List[dict]:
    """Shubhali buyurtma qatorlarini topadi — «bu raqam qayerdan chiqdi?».

    Nima qidiriladi (har biri o'tmishda haqiqiy xatoga sabab bo'lgan):
      • buyurtma miqdori butun oynadagi SAVDODAN ko'p — tovar 30 kunda 1 dona
        sotilgan bo'lsa, 17 dona so'ralishi mumkin emas;
      • tovar oynaning ozgina qismida sotuvda turgan (bo'linma kichik);
      • jami 1-2 marta sotilgan — o'rtacha deb atash uchun kam;
      • miqdorning katta qismi bitta yirik savdodan kelgan.

    Bu hisobotni ALMASHTIRMAYDI, faqat qaysi qatorlarni odam ko'zdan
    kechirishi kerakligini aytadi.
    """
    out = []
    for l in lines:
        if l.need <= 0:
            continue
        r = l.rate
        why = []
        if r.sold_total > 0 and l.need > r.sold_total:
            why.append(f"buyurtma ({l.need}) {window_days} kunlik savdodan "
                       f"({r.sold_total:.0f}) ko'p")
        if r.thin:
            why.append(f"atigi {r.avail_days} kun sotuvda turgan")
        if r.events <= 2:
            why.append(f"jami {r.events} marta sotilgan")
        if r.bulk_count:
            why.append(f"{r.bulk_count}× yirik savdo aniqlangan")
        if why:
            out.append({"line": l, "why": why})
    out.sort(key=lambda x: -x["line"].amount)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Yetkazib beruvchi bo'yicha buyurtma ro'yxati")
    ap.add_argument("--days", type=int, default=30, help="ketish tezligi oynasi")
    ap.add_argument("--since", default="", help="davr boshlanishi YYYY-MM-DD")
    ap.add_argument("--until", default="", help="davr tugashi YYYY-MM-DD")
    ap.add_argument("--lead", type=int, default=LEAD_DAYS, help="yetkazish muddati (kun)")
    ap.add_argument("--cover", type=int, default=COVER_DAYS, help="zaxira qamrovi (kun)")
    ap.add_argument("--supplier", help="faqat shu yetkazib beruvchi (nomi bo'yicha)")
    ap.add_argument("--unassigned", action="store_true",
                    help="yetkazib beruvchi biriktirilmagan tovarlarni ko'rsatish")
    ap.add_argument("--all", action="store_true",
                    help="buyurtma kerak bo'lmaganlarni ham ko'rsatish")
    ap.add_argument("--csv", action="store_true", help="CSV fayl ham yozish")
    ap.add_argument("--audit", action="store_true",
                    help="shubhali qatorlarni tekshirish (hisobot o'rniga)")
    ap.add_argument("--telegram", action="store_true",
                    help="qisqa ro'yxatni Telegram'ga yuborish")
    args = ap.parse_args(argv)

    with MarketplaceDB() as db:
        days_have = db.snapshot_days()
        lines = build(db, days=args.days, lead=args.lead, cover=args.cover,
                      since=args.since, until=args.until)

    if days_have < 7:
        print(
            f"OGOHLANTIRISH: atigi {days_have} kunlik qoldiq tarixi bor. "
            "Tezlik taxminiy — kamida 7-14 kun yig'ilsin.\n"
        )

    if args.audit:
        found = audit(lines, args.days)
        need_all = [l for l in lines if l.need > 0]
        risky = sum(x["line"].amount for x in found)
        total = sum(l.amount for l in need_all)
        print(f"Buyurtma qatorlari: {len(need_all)}, jami {_money(total)} so'm")
        print(f"Ko'zdan kechirish kerak: {len(found)} qator, "
              f"{_money(risky)} so'm ({100 * risky / max(total, 1):.0f}%)\n")
        for x in found[:40]:
            l, r = x["line"], x["line"].rate
            print(f"  {l.supplier[:16]:<16} {r.sku:<7} {r.name[:30]:<30} "
                  f"buyurtma {l.need:>4}  ({_money(l.amount)} so'm)")
            for w in x["why"]:
                print(f"       – {w}")
        if len(found) > 40:
            print(f"\n  … yana {len(found) - 40} qator")
        return 0

    groups = group_by_supplier(lines)
    out_dir = os.path.join(os.path.dirname(os.path.abspath(config.DB_PATH)), "exports")

    print(f"Standart muddat: yetkazish {args.lead} + zaxira {args.cover} = "
          f"{args.lead + args.cover} kun. O'z muddati belgilangan kompaniyalar "
          "o'zinikini ishlatadi\n(supplier_link schedule).")

    grand = {"total": 0.0, "count": 0, "lost": 0.0}
    shown = 0
    named = sorted(
        ((sid, g) for sid, g in groups.items() if sid),
        key=lambda kv: kv[1][0].supplier.lower(),
    )
    for sid, group in named:
        title = group[0].supplier or f"#{sid}"
        if args.supplier and args.supplier.lower() not in title.lower():
            continue
        stat = _print_group(title, group, args.all)
        if args.csv and stat["count"]:
            print(f"  CSV: {_write_csv(title, group, out_dir)}")
        for k in grand:
            grand[k] += stat[k]
        shown += 1

    if args.unassigned or (not shown and not args.supplier):
        rest = [
            l for l in groups.get(0, [])
            if l.need > 0 or (args.all and l.rate.rate > 0)
        ]
        rest.sort(key=lambda l: (_ORDER_RANK[l.status], -l.amount))
        if rest:
            stat = _print_group(
                "BIRIKTIRILMAGAN (yetkazib beruvchi yo'q)", rest[:60], args.all
            )
            if args.csv and stat["count"]:
                print(f"  CSV: {_write_csv('biriktirilmagan', rest, out_dir)}")
            print(
                "\n  Bularni biriktirish:  python -m marketplace.supplier_link "
                "import --supplier \"Nomi\" --file royxat.txt"
            )

    if shown:
        print(f"\n{'═' * 86}")
        print(f"JAMI: {grand['count']} pozitsiya, taxminan {_money(grand['total'])} so'm")
        if grand["lost"] > 0:
            print(f"Tugagan tovarlar tufayli kuniga {_money(grand['lost'])} so'm savdo yo'qolmoqda")
    elif args.supplier:
        print(f"\n«{args.supplier}» topilmadi. Ro'yxat: "
              "python -m marketplace.supplier_link list")

    if args.telegram:
        # Kech import: `report` modul darajasida `.env` o'qiydi va requests
        # talab qiladi — Telegram so'ralmasa, bularning keragi yo'q.
        from marketplace.report import send_telegram

        print("\n— Telegram'ga yuborilmoqda…")
        ok = send_telegram(telegram_text(groups, args.lead, args.cover))
        print("✓ yuborildi" if ok else "✗ yuborilmadi")
    return 0


if __name__ == "__main__":
    sys.exit(main())
