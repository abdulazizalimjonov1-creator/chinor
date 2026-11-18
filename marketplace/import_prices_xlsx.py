"""YesPOS «Товары» Excel eksportidan tannarx va optom narxni Chinor'ga yozadi.

Nega Excel: marketplace API tannarx va optom narxni BERMAYDI (pos_bridge faqat
qoldiq, dona narx va nomni sinxronlaydi). YesPOS'da Товары → eksport qilingan
faylda ular bor: «Себестоимость» = tannarx, «Цена» = tanlangan narx turi.
Optom narx turi tanlab eksport qilingan fayl beriladi.

Bog'lash — pos_bridge bilan bir xil: «КОД» (YesPOS sku) → marketplace.db
products.id (yespos_id) → pos.db products.yespos_id. Shu sabab sku boshqa
tovarga berilgan bo'lsa ham noto'g'ri tovar ustidan yozilmaydi.

Optom 0 bo'lsa 0 yoziladi: kassa bunda dona narxda sotib, qizil ogohlantiradi.
Dona narxga tegilmaydi — uni pos_bridge har 15 daqiqada yangilaydi.

    python -m marketplace.import_prices_xlsx optom.xlsx            # ko'rsatadi
    python -m marketplace.import_prices_xlsx optom.xlsx --apply    # yozadi
"""

import argparse
import shutil
import sqlite3
import sys
import xml.etree.ElementTree as ET
import zipfile

from marketplace import config
from marketplace.db import MarketplaceDB, now_local

_M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def read_xlsx(path: str) -> dict:
    """sku → {"name", "cost", "price"}. openpyxl'siz: xlsx = zip ichidagi XML."""
    z = zipfile.ZipFile(path)
    ss = []
    if "xl/sharedStrings.xml" in z.namelist():
        ss = ["".join(t.text or "" for t in si.iter(_M + "t"))
              for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(_M + "si")]
    rows = []
    for r in ET.fromstring(z.read("xl/worksheets/sheet1.xml")).iter(_M + "row"):
        vals = []
        for c in r.iter(_M + "c"):
            v, t = c.find(_M + "v"), c.get("t")
            if t == "inlineStr":
                vals.append("".join(x.text or "" for x in c.iter(_M + "t")))
            elif v is None:
                vals.append("")
            else:
                vals.append(ss[int(v.text)] if t == "s" else v.text)
        rows.append(vals)
    head = next((r for r in rows if "КОД" in r and "Себестоимость" in r), None)
    if not head:
        raise ValueError("«КОД» va «Себестоимость» ustunlari topilmadi — YesPOS Товары eksporti emas")
    ix = {k: head.index(k) for k in ("Товар", "КОД", "Себестоимость", "Цена")}

    def num(x):
        try:
            return float(str(x).replace(",", ".").replace(" ", "") or 0)
        except ValueError:
            return 0.0
    out = {}
    for r in rows[rows.index(head) + 1:]:
        if len(r) <= max(ix.values()) or not str(r[ix["КОД"]]).strip():
            continue
        out[str(r[ix["КОД"]]).strip()] = {
            "name": r[ix["Товар"]], "cost": num(r[ix["Себестоимость"]]),
            "price": num(r[ix["Цена"]])}
    return out


def plan(pos_db: str, rows: dict, sku_to_yid: dict) -> dict:
    conn = sqlite3.connect(f"file:{pos_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        local = conn.execute(
            "SELECT id, name, sell_price, cost_price, wholesale_price, "
            "COALESCE(yespos_id,0) yid FROM products").fetchall()
    finally:
        conn.close()
    by_yid = {r["yid"]: r for r in local if r["yid"]}
    changes, missing, dearer, below_cost = [], [], [], []
    for sku, x in rows.items():
        r = by_yid.get(sku_to_yid.get(sku))
        if r is None:
            missing.append((sku, x["name"]))
            continue
        # YesPOS'da tannarx 0 — yo'q degani, Chinor'dagi borini o'chirmaymiz
        cost = round(x["cost"], 2) if x["cost"] > 0 else float(r["cost_price"] or 0)
        whs = round(x["price"], 2)
        if whs > 0 and float(r["sell_price"] or 0) > 0 and whs > float(r["sell_price"]):
            dearer.append((r["id"], r["name"], whs, float(r["sell_price"])))
        if 0 < whs < cost:
            below_cost.append((r["id"], r["name"], whs, cost))
        if (abs(float(r["cost_price"] or 0) - cost) > 0.01
                or abs(float(r["wholesale_price"] or 0) - whs) > 0.01):
            changes.append((r["id"], r["name"], float(r["cost_price"] or 0), cost,
                            float(r["wholesale_price"] or 0), whs))
    return {"changes": changes, "missing": missing, "dearer": dearer,
            "below_cost": below_cost, "total": len(rows)}


def apply(pos_db: str, changes: list) -> None:
    backup = f"{pos_db}.bak-prices-{now_local().strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(pos_db, backup)
    print(f"Zaxira nusxa: {backup}")
    conn = sqlite3.connect(pos_db, timeout=30)
    try:
        with conn:
            conn.executemany(
                "UPDATE products SET cost_price = ?, wholesale_price = ? WHERE id = ?",
                [(cost, whs, pid) for pid, _n, _oc, cost, _ow, whs in changes])
    finally:
        conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="YesPOS Excel → tannarx + optom narx")
    ap.add_argument("xlsx", help="optom narx turi bilan eksport qilingan Товары fayli")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--pos-db", default=config.POS_DB)
    a = ap.parse_args(argv)

    rows = read_xlsx(a.xlsx)
    with MarketplaceDB() as db:
        sku_to_yid = {str(r["sku"]): int(r["id"]) for r in
                      db.conn.execute("SELECT id, sku FROM products WHERE sku <> ''")}
    p = plan(a.pos_db, rows, sku_to_yid)

    print(f"Excel'da: {p['total']:,} tovar, optom narxli: "
          f"{sum(v['price'] > 0 for v in rows.values()):,}")
    print(f"O'zgaradi: {len(p['changes']):,}   Chinor'da topilmadi: {len(p['missing']):,}")
    for pid, n, oc, c, ow, w in p["changes"][:8]:
        print(f"  [{pid}] {n[:32]:<32} tannarx {oc:>9,.0f}→{c:>9,.0f}  optom {ow:>9,.0f}→{w:>9,.0f}")
    if p["dearer"]:
        print(f"\n⚠ Optom narx dona narxdan qimmat: {len(p['dearer'])} ta (YesPOS'da tekshiring)")
        for pid, n, w, s in p["dearer"][:40]:
            print(f"  [{pid}] {n[:40]:<40} optom {w:,.0f} > dona {s:,.0f}")
    if p["below_cost"]:
        print(f"\n⚠ Optom narx tannarxdan arzon: {len(p['below_cost'])} ta")
        for pid, n, w, c in p["below_cost"][:40]:
            print(f"  [{pid}] {n[:40]:<40} optom {w:,.0f} < tannarx {c:,.0f}")
    if not a.apply:
        print("\n[dry-run] Hech narsa yozilmadi. Yozish uchun: --apply")
        return 0
    if p["changes"]:
        apply(a.pos_db, p["changes"])
    print(f"\n✓ {len(p['changes']):,} tovar yangilandi.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
