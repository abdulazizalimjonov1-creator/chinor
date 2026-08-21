"""Tannarxni YesPOS «Товары» eksportidan yangilash.

Nega kerak: marketplace gateway tannarxni BERMAYDI (faqat sotuv narxi va
qoldiq), shuning uchun `pos.db` dagi `cost_price` qo'lda kiritilgan va vaqt
o'tishi bilan eskiradi. Eskirgan tannarx ikki joyda zarar keltiradi:
  • foyda noto'g'ri hisoblanadi (Mini App «Aylanma va foyda»);
  • buyurtma summasi noto'g'ri chiqadi — bir tovarda tannarx 1 850 000 deb
    turgan edi, aslida 40 000 (46 barobar farq).

Fayl: YesPOS admin panelidan «Товары» eksporti. Sarlavhasi:
    №, КОД, Товар, Категория, Остаток, Себестоимость, Наценко, Цена, ИКПУ

Nol tannarx YOZILMAYDI: eksportda 0 turishi «tannarx belgilanmagan» degani,
«tovar tekin» degani emas — ustidan yozsak bor ma'lumot yo'qolardi.

Ishlatish:
    python -m marketplace.costs --file data/prixod/tovarlar.xlsx
    python -m marketplace.costs --file data/prixod/tovarlar.xlsx --apply
"""

import argparse
import shutil
import sqlite3
import sys
from typing import Dict, Tuple

from marketplace import config
from marketplace.db import now_local
from marketplace.supplier_link import read_xlsx

# Shu summadan kichik farq e'tiborga olinmaydi (yaxlitlash).
MIN_DIFF = 1.0


def parse(path: str) -> Dict[int, Tuple[float, str, float]]:
    """{sku: (tannarx, nom, sotuv_narxi)} — nol tannarxlilar tashlanadi."""
    rows = read_xlsx(path)
    hdr = None
    for i, r in enumerate(rows[:20]):
        if any(c.strip().upper() == "КОД" for c in r):
            hdr = i
            break
    if hdr is None:
        raise SystemExit("«КОД» ustuni topilmadi — bu YesPOS «Товары» eksportimi?")

    cols = {c.strip().lower(): i for i, c in enumerate(rows[hdr])}
    for need in ("код", "товар", "себестоимость"):
        if need not in cols:
            raise SystemExit(f"«{need}» ustuni yo'q — fayl kutilganidan boshqa.")
    i_sku, i_name = cols["код"], cols["товар"]
    i_cost, i_price = cols["себестоимость"], cols.get("цена")

    out = {}
    for r in rows[hdr + 1:]:
        if len(r) <= max(i_sku, i_cost) or not r[i_sku].strip().isdigit():
            continue
        try:
            cost = float(r[i_cost] or 0)
            price = float(r[i_price] or 0) if i_price is not None and len(r) > i_price else 0.0
        except ValueError:
            continue
        if cost <= 0:
            continue
        out[int(r[i_sku])] = (cost, r[i_name].strip(), price)
    return out


def plan(costs: Dict[int, tuple], pos_db: str) -> dict:
    conn = sqlite3.connect(f"file:{pos_db}?mode=ro", uri=True)
    have = {int(i): float(c or 0) for i, c in
            conn.execute("SELECT id, cost_price FROM products")}
    conn.close()

    fill, change, missing, same = [], [], [], 0
    for sku, (cost, name, _price) in costs.items():
        if sku not in have:
            missing.append((sku, name))
        elif not have[sku]:
            fill.append((sku, name, 0.0, cost))
        elif abs(have[sku] - cost) > MIN_DIFF:
            change.append((sku, name, have[sku], cost))
        else:
            same += 1
    change.sort(key=lambda x: -abs(x[3] - x[2]))
    return {"fill": fill, "change": change, "missing": missing, "same": same}


def apply(p: dict, pos_db: str) -> int:
    stamp = now_local().strftime("%Y%m%d-%H%M%S")
    backup = f"{pos_db}.bak-cost-{stamp}"
    shutil.copy2(pos_db, backup)
    print(f"Zaxira nusxa: {backup}")

    conn = sqlite3.connect(pos_db, timeout=30)
    try:
        conn.executemany(
            "UPDATE products SET cost_price = ? WHERE id = ?",
            [(cost, sku) for sku, _n, _old, cost in p["fill"] + p["change"]],
        )
        conn.commit()
        return len(p["fill"]) + len(p["change"])
    finally:
        conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Tannarxni YesPOS eksportidan yangilash")
    ap.add_argument("--file", required=True, help="«Товары» eksporti (.xlsx)")
    ap.add_argument("--apply", action="store_true", help="haqiqatan yozish")
    ap.add_argument("--top", type=int, default=10, help="nechta o'zgarishni ko'rsatish")
    args = ap.parse_args(argv)

    costs = parse(args.file)
    p = plan(costs, config.POS_DB)

    print(f"Faylda tannarxli tovar: {len(costs)}")
    print(f"  o'zgarishsiz:        {p['same']}")
    print(f"  tannarx to'ldiriladi:{len(p['fill'])}")
    print(f"  tannarx yangilanadi: {len(p['change'])}")
    print(f"  pos.db da yo'q:      {len(p['missing'])}")

    if p["change"]:
        print("\nEng katta o'zgarishlar:")
        for sku, name, old, new in p["change"][:args.top]:
            print(f"   {sku:<7} {name[:34]:<34} {old:>12,.0f} → {new:>12,.0f}")

    if not args.apply:
        print("\n[sinov] Hech narsa yozilmadi. Yozish uchun: --apply")
        return 0

    n = apply(p, config.POS_DB)
    print(f"\n✓ {n} tovarning tannarxi yangilandi.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
