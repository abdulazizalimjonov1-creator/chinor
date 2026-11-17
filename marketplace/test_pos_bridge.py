"""pos_bridge: tovar yespos_id orqali bog'lanadi, raqam to'qnashuvi boshqa
tovar ustidan yozmaydi.  Ishlatish: python -m marketplace.test_pos_bridge"""

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
    path = _db([(10001, "Gel sovun", 5000, 3), (16877, "parol", 1000, 1)])
    # 1-ishga tushish: 10001 ulanadi; 16877 — to'qnashuv, tegilmaydi,
    # YesPOS tovari 800001 bo'lib qo'shiladi.
    p = run(path, _mp(("10001", 501, "Gel sovun 1L", 6000, 4),
                      ("16877", 777, "Gel sovun idish", 9000, 2)))
    r = _rows(path)
    assert r[10001] == ("Gel sovun 1L", 6000, 4, 501), r[10001]
    assert r[16877] == ("parol", 1000, 1, 0), r[16877]
    assert r[800001] == ("Gel sovun idish", 9000, 2, 777), r[800001]
    assert len(p["clashes"]) == 1

    # 2: YesPOS 10001 raqamini BOSHQA (yangi) tovarga berdi, eskisi yo'qoldi.
    # Chinor 10001 ga tegilmaydi; 800001 o'z tovari bilan davom etadi.
    run(path, _mp(("10001", 999, "Mix bo'yoq", 50000, 7),
                  ("16877", 777, "Gel sovun idish YANGI", 9500, 1)))
    r = _rows(path)
    assert r[10001] == ("Gel sovun 1L", 6000, 4, 501), r[10001]
    assert r[800001] == ("Gel sovun idish YANGI", 9500, 1, 777), r[800001]
    assert r[800002][3] == 999 and r[800002][0] == "Mix bo'yoq", r.get(800002)
    assert r[16877][3] == 0     # «parol» hech qachon ulanmaydi

    # 3: qayta ishga tushirish hech narsani takrorlamaydi.
    p = b.plan(path, _mp(("10001", 999, "Mix bo'yoq", 50000, 7),
                         ("16877", 777, "Gel sovun idish YANGI", 9500, 1)), {})
    assert not (p["new_products"] or p["new_links"] or p["qty_changes"]
                or p["price_changes"] or p["name_changes"]), p
    print("OK")


if __name__ == "__main__":
    test()
