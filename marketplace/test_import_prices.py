"""import_prices_xlsx: Excel o'qiladi, yespos_id bo'yicha to'g'ri tovarga yoziladi.
Ishlatish: python -m marketplace.test_import_prices"""

import os
import sqlite3
import tempfile
import zipfile

from marketplace import import_prices_xlsx as m


def _xlsx(path, rows):
    cell = lambda v: (f'<c t="inlineStr"><is><t>{v}</t></is></c>' if isinstance(v, str)
                      else f"<c><v>{v}</v></c>")
    body = "".join("<row>" + "".join(cell(v) for v in r) + "</row>" for r in rows)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml",
                   '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   f"<sheetData>{body}</sheetData></worksheet>")


def test():
    d = tempfile.mkdtemp()
    x = os.path.join(d, "o.xlsx")
    _xlsx(x, [["Товары по состоянию на ..."],
              ["№", "Товар", "КОД", "Категория", "Ед.изм.", "Остаток", "Мин.Остаток", "Себестоимость", "Цена"],
              [1, "Luk 60x60", "17394", "g", "Штука", 3, 0, 140000, 160000],
              [2, "Toffix", "17404", "g", "Штука", 9, 0, 722.82, 0],
              [3, "Yangi sku", "99999", "g", "Штука", 1, 0, 5, 6],
              [4, "Tannarxsiz", "17405", "g", "Штука", 1, 0, 0, 900]])
    rows = m.read_xlsx(x)
    assert rows["17394"] == {"name": "Luk 60x60", "cost": 140000.0, "price": 160000.0}

    db = os.path.join(d, "pos.db")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, sell_price REAL,"
              " cost_price REAL, wholesale_price REAL, yespos_id INTEGER)")
    # 17404 ning sku'si Chinor'da 800001 qatorga yespos_id orqali bog'langan
    c.executemany("INSERT INTO products VALUES (?,?,?,?,?,?)",
                  [(17394, "Luk 60x60", 150000, 0, 0, 501),
                   (17404, "parol", 1000, 0, 0, 0),
                   (800001, "Toffix", 1000, 0, 500, 777),
                   (17405, "Tannarxsiz", 1000, 650, 0, 502)])
    c.commit(); c.close()
    p = m.plan(db, rows, {"17394": 501, "17404": 777, "99999": 888, "17405": 502})
    m.apply(db, p["changes"])
    c = sqlite3.connect(db)
    got = {r[0]: r[1:] for r in c.execute("SELECT id, cost_price, wholesale_price FROM products")}
    assert got[17394] == (140000, 160000), got
    assert got[800001] == (722.82, 0), got     # optom 0 → 0
    assert got[17404] == (0, 0), got           # boshqa tovarga tegilmadi
    assert got[17405] == (650, 900), got       # Excel'da tannarx 0 → eskisi qoladi
    assert [s for s, _ in p["missing"]] == ["99999"]
    assert [r[0] for r in p["dearer"]] == [17394]  # optom 160k > dona 150k
    print("OK")


if __name__ == "__main__":
    test()
