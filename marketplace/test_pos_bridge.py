"""pos_bridge: birinchi bog'lash sku bo'yicha (qayta nomlansa ham), keyin
yespos_id — raqam boshqa tovarga berilsa eski tovar ustidan yozilmaydi.  Ishlatish: python -m marketplace.test_pos_bridge"""

import os
import sqlite3
import tempfile

from marketplace import pos_bridge as b


def _db(rows):
    path = os.path.join(tempfile.mkdtemp(), "pos.db")
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, description TEXT,"
              " sell_price REAL, wholesale_price REAL, cost_price REAL, qty REAL, unit TEXT,"
              " is_active INTEGER, created_at TEXT, category_id INTEGER, barcode TEXT DEFAULT '')")
    c.executemany("INSERT INTO products (id, name, sell_price, cost_price, qty, created_at)"
                  " VALUES (?,?,?,0,?,'')", rows)
    c.commit()
    c.close()
    return path


def _mp(*items):
    return {sku: {"yespos_id": yid, "name": n, "price": pr, "stock": st}
            for sku, yid, n, pr, st in items}


def _rows(path):
    c = sqlite3.connect(path)
    out = {r[0]: r[1:] for r in c.execute(
        "SELECT id, name, sell_price, qty, yespos_id FROM products")}
    c.close()
    return out


def run(path, mp):
    p = b.plan(path, mp, {})
    b.apply(path, mp, p)
    return p


def test():
    path = _db([(10001, "Gel sovun", 5000, 3), (14220, "Termos vacum cup 1l", 67000, 0),
                (13682, "Krushka keramik 400ml", 21000, 1)])
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE product_barcodes (code TEXT PRIMARY KEY, product_id INTEGER)")
    c.execute("ALTER TABLE products ADD COLUMN yespos_id INTEGER DEFAULT 0")
    c.execute("ALTER TABLE products ADD COLUMN image_url TEXT DEFAULT ''")
    # 11.2026 dagi xato: 13682 «to'qnashuv» deb ajratilib, dublikat 800001 yaratilgan
    c.execute("INSERT INTO products (id, name, sell_price, cost_price, wholesale_price, qty,"
              " created_at, yespos_id, barcode) VALUES (800001,'Keramik bokal katta',58000,40000,52000,6,'',3683,'')")
    c.execute("INSERT INTO product_barcodes VALUES ('8605061230674', 800001)")
    c.commit(); c.close()

    mp = _mp(("10001", 501, "Gel sovun 1L", 6000, 4),
             ("14220", 4221, "Termos 700 ml UYLEE", 66000, 5),       # qayta nomlangan
             ("13682", 3683, "Keramik bokal katta", 58000, 6))
    p = run(path, mp)
    r = _rows(path)
    assert r[10001] == ("Gel sovun 1L", 6000, 4, 501), r[10001]
    # Nomi butunlay boshqa, lekin raqam bir xil — bog'lanadi, YesPOS narx/qoldig'i keladi
    assert r[14220] == ("Termos 700 ml UYLEE", 66000, 5, 4221), r[14220]
    # Dublikat asl qatorga birlashdi: o'chdi, tannarx/optom/shtrix-kod ko'chdi
    assert 800001 not in r and r[13682][3] == 3683, r
    c = sqlite3.connect(path)
    assert c.execute("SELECT cost_price, wholesale_price FROM products WHERE id=13682").fetchone() == (40000, 52000)
    assert c.execute("SELECT product_id FROM product_barcodes").fetchone()[0] == 13682
    c.close()
    assert p["merges"] == [(13682, 800001, 3683)], p["merges"]

    # Keyin YesPOS 10001 raqamini BOSHQA tovarga berdi — eski tovarga tegilmaydi
    run(path, _mp(("10001", 999, "Mix bo'yoq", 50000, 7),
                  ("14220", 4221, "Termos 700 ml UYLEE", 66000, 5),
                  ("13682", 3683, "Keramik bokal katta", 58000, 6)))
    r = _rows(path)
    assert r[10001] == ("Gel sovun 1L", 6000, 4, 501), r[10001]
    new = [k for k, v in r.items() if v[3] == 999]
    assert new and new[0] >= 800001 and r[new[0]][0] == "Mix bo'yoq", r

    # Qayta ishga tushirish hech narsani takrorlamaydi
    p = b.plan(path, _mp(("10001", 999, "Mix bo'yoq", 50000, 7),
                         ("14220", 4221, "Termos 700 ml UYLEE", 66000, 5),
                         ("13682", 3683, "Keramik bokal katta", 58000, 6)), {})
    assert not (p["new_products"] or p["new_links"] or p["merges"] or p["qty_changes"]
                or p["price_changes"] or p["name_changes"]), p
    print("OK")


if __name__ == "__main__":
    test()
