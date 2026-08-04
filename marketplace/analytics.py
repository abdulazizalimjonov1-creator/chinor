"""Qoldiq tarixidan xulosa chiqaradi — ikkita pul yo'qotadigan holat.

  1. Muzlagan tovar   — qoldiq bor, lekin qimirlamayapti. Pul javonda qotgan.
  2. Tugagan-u kelmayotgan — yaxshi sotilardi, qoldiq 0 ga tushdi va shundan
     beri keltirilmayapti. Har kuni yo'qotilgan savdo.

Muhim cheklov (ochiq aytiladi): gateway savdo tarixini bermaydi. Savdo
qoldiqning kamayishidan TAXMIN qilinadi. Inventarizatsiya yoki buzilgan
tovarni chiqarish ham qoldiqni kamaytiradi — shuning uchun bu aniq savdo
emas, balki ishonchli ko'rsatkich.

Ishlatish:
    python -m marketplace.analytics dead [--days 30]
    python -m marketplace.analytics gap  [--days 7] [--lookback 60]
"""

import argparse
import sys
from collections import defaultdict
from datetime import timedelta
from typing import Dict, List, Optional

from marketplace import config
from marketplace.db import MarketplaceDB, now_local


def _load_series(
    db: MarketplaceDB, days: int, branch: Optional[str] = None
) -> Dict[int, List[dict]]:
    """product_id → kun bo'yicha tartiblangan [{day, stock, price}, ...]."""
    branch = str(branch if branch is not None else config.BRANCH)
    since = (now_local() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = db.conn.execute(
        """SELECT product_id, day, stock, price
             FROM stock_daily
            WHERE branch = ? AND day >= ?
            ORDER BY product_id, day""",
        (branch, since),
    ).fetchall()
    series: Dict[int, List[dict]] = defaultdict(list)
    for r in rows:
        series[r["product_id"]].append(
            {"day": r["day"], "stock": float(r["stock"]), "price": float(r["price"])}
        )
    return series


def _names(db: MarketplaceDB) -> Dict[int, dict]:
    return {
        r["id"]: {"name": r["name"], "sku": r["sku"]}
        for r in db.conn.execute("SELECT id, name, sku FROM products").fetchall()
    }


def dead_stock(db: MarketplaceDB, days: int = None) -> List[dict]:
    """Qoldig'i shuncha kundan beri umuman kamaymagan tovarlar.

    Shart: butun oynada qoldiq > 0 va hech qachon kamaymagan (faqat turgan
    yoki ortgan). Kamaygan bo'lsa — demak sotilgan, muzlagan emas.
    """
    days = days or config.DEAD_STOCK_DAYS
    series = _load_series(db, days)
    meta = _names(db)
    out = []

    for pid, rows in series.items():
        if len(rows) < 2:
            continue  # bir kunlik ma'lumot bilan xulosa chiqarib bo'lmaydi
        span = (
            _date(rows[-1]["day"]) - _date(rows[0]["day"])
        ).days
        if span < days - 1:
            continue  # oyna to'lmagan — hali erta
        if min(r["stock"] for r in rows) <= 0:
            continue  # tugab turgan — bu boshqa muammo (gap)
        if any(rows[i]["stock"] < rows[i - 1]["stock"] for i in range(1, len(rows))):
            continue  # kamaygan → harakatda

        last = rows[-1]
        info = meta.get(pid, {})
        out.append(
            {
                "product_id": pid,
                "name": info.get("name", "?"),
                "sku": info.get("sku", ""),
                "stock": last["stock"],
                "price": last["price"],
                "locked": last["stock"] * last["price"],
                "days": span + 1,
            }
        )

    out.sort(key=lambda x: x["locked"], reverse=True)
    return out


def out_of_stock_gap(
    db: MarketplaceDB, days: int = None, lookback: int = 60
) -> List[dict]:
    """Tugagan-u qayta keltirilmayotgan, ilgari sotilgan tovarlar.

    Ikkita shart birga bajarilishi kerak:
      • oxirgi `days` kun davomida qoldiq 0;
      • 0 ga tushishdan oldin qoldiq kamayib turgan (ya'ni sotilardi).
    Ikkinchi shart hech qachon sotilmagan "o'lik" tovarni ajratib tashlaydi.
    """
    days = days or config.OUT_OF_STOCK_DAYS
    series = _load_series(db, lookback)
    meta = _names(db)
    out = []

    for pid, rows in series.items():
        if len(rows) < 2:
            continue

        # Oxiridan boshlab necha kun ketma-ket 0 turibdi.
        zero_rows = []
        for r in reversed(rows):
            if r["stock"] > 0:
                break
            zero_rows.append(r)
        if not zero_rows:
            continue
        zero_span = (
            _date(zero_rows[0]["day"]) - _date(zero_rows[-1]["day"])
        ).days + 1
        if zero_span < days:
            continue

        # 0 ga tushishdan oldingi qism — harakat bo'lganmi?
        before = rows[: len(rows) - len(zero_rows)]
        if len(before) < 2:
            continue
        drop = sum(
            max(0.0, before[i - 1]["stock"] - before[i]["stock"])
            for i in range(1, len(before))
        )
        if drop <= 0:
            continue  # hech qachon kamaymagan → sotilmagan, bu "gap" emas

        span_days = max(1, (_date(before[-1]["day"]) - _date(before[0]["day"])).days)
        per_day = drop / span_days
        price = next((r["price"] for r in reversed(rows) if r["price"] > 0), 0.0)

        info = meta.get(pid, {})
        out.append(
            {
                "product_id": pid,
                "name": info.get("name", "?"),
                "sku": info.get("sku", ""),
                "zero_days": zero_span,
                "per_day": per_day,
                "price": price,
                "lost_per_day": per_day * price,
            }
        )

    out.sort(key=lambda x: x["lost_per_day"], reverse=True)
    return out


def _date(s: str):
    from datetime import datetime

    return datetime.strptime(s, "%Y-%m-%d")


# ── CLI ───────────────────────────────────────────────────────────────────


def _guard(db: MarketplaceDB, need: int) -> bool:
    have = db.snapshot_days()
    if have < need:
        print(
            f"Yetarli tarix yo'q: {have} kunlik surat bor, kamida {need} kerak.\n"
            f"Har kuni `python -m marketplace.sync stock` ishlab tursin — "
            f"{need - have} kundan keyin natija chiqadi."
        )
        return False
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Qoldiq tarixi analitikasi")
    sub = ap.add_subparsers(dest="command", required=True)

    d = sub.add_parser("dead", help="muzlagan tovarlar")
    d.add_argument("--days", type=int, default=config.DEAD_STOCK_DAYS)
    d.add_argument("--top", type=int, default=30)

    g = sub.add_parser("gap", help="tugagan-u keltirilmayotgan tovarlar")
    g.add_argument("--days", type=int, default=config.OUT_OF_STOCK_DAYS)
    g.add_argument("--lookback", type=int, default=60)
    g.add_argument("--top", type=int, default=30)

    args = ap.parse_args(argv)

    with MarketplaceDB() as db:
        if args.command == "dead":
            if not _guard(db, args.days):
                return 1
            rows = dead_stock(db, args.days)
            print(f"Muzlagan tovarlar ({args.days} kundan beri qimirlamagan): {len(rows)} ta\n")
            total = sum(r["locked"] for r in rows)
            for r in rows[: args.top]:
                print(
                    f"  {r['locked']:>14,.0f} so'm  qoldiq {r['stock']:>8,.0f}  "
                    f"[{r['sku'] or r['product_id']}] {r['name']}"
                )
            print(f"\nJami qotib qolgan pul: {total:,.0f} so'm")

        elif args.command == "gap":
            if not _guard(db, args.days):
                return 1
            rows = out_of_stock_gap(db, args.days, args.lookback)
            print(
                f"Tugagan-u keltirilmayotgan tovarlar "
                f"({args.days}+ kundan beri 0): {len(rows)} ta\n"
            )
            total = sum(r["lost_per_day"] for r in rows)
            for r in rows[: args.top]:
                print(
                    f"  ~{r['lost_per_day']:>12,.0f} so'm/kun  {r['zero_days']:>3} kun 0  "
                    f"[{r['sku'] or r['product_id']}] {r['name']}"
                )
            print(f"\nTaxminiy yo'qotilayotgan savdo: ~{total:,.0f} so'm/kun")

    return 0


if __name__ == "__main__":
    sys.exit(main())
