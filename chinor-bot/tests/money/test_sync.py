"""Oflayn kassa sinxroni: /api/sync/sales va /api/sync/cash-txns.

Asosiy xavf: internet uzilib, kassa javobni olmasa, o'sha yozuvni QAYTA yuboradi.
"""

import pytest


def sale(pid, no="1-1", qty=1, price=10000, **kw):
    return {"local_id": no, "receipt_no": no, "source": "Kassa-1",
            "items": [{"product_id": pid, "qty": qty, "price_sum": price}],
            "total_sum": qty * price, **kw}


@pytest.mark.parametrize("method,column", [
    ("cash", "paid_cash"), ("card", "paid_card"), ("click", "paid_other"),
])
def test_payment_method_goes_to_its_column(shop, method, column):
    p = shop.product()
    r = shop.sync_sales(sale(p, payment=method))
    assert r[0]["ok"]
    assert shop.one(f"SELECT {column} FROM sales WHERE id=?", r[0]["server_id"]) == 10000


def test_split_sale_with_debt(shop):
    p, c = shop.product(), shop.client()
    r = shop.sync_sales(sale(p, price=100000, payment="split", client_id=c,
                             paid_cash=30000, paid_card=20000, debt_sum=50000))
    assert r[0]["ok"]
    assert shop.one("SELECT paid_total FROM sales") == 50000
    assert shop.debt(c) == 50000


def test_nasiya_sale(shop):
    p, c = shop.product(), shop.client()
    shop.sync_sales(sale(p, is_nasiya=True, client_id=c))
    assert shop.debt(c) == 10000
    assert shop.one("SELECT paid_total FROM sales") == 0


def test_retried_sale_is_not_counted_twice(shop):
    p, c = shop.product(qty=10), shop.client()
    s = sale(p, is_nasiya=True, client_id=c)
    first = shop.sync_sales(s)[0]
    again = shop.sync_sales(s)[0]
    assert again["duplicate"] and again["server_id"] == first["server_id"]
    assert shop.count("sales") == 1
    assert shop.qty(p) == 9
    assert shop.debt(c) == 10000


def test_same_receipt_from_other_kassa_is_a_separate_sale(shop):
    p = shop.product(qty=10)
    shop.sync_sales(sale(p), {**sale(p), "source": "Kassa-2"})
    assert shop.count("sales") == 2
    assert shop.qty(p) == 8


def test_retried_return_is_not_counted_twice(shop):
    p = shop.product(qty=10)
    shop.sync_sales(sale(p, qty=3))
    ret = sale(p, no="1-2", qty=1, is_return=True, orig_receipt_no="1-1",
               return_method="cash")
    shop.sync_sales(ret)
    assert shop.sync_sales(ret)[0]["duplicate"]
    assert shop.qty(p) == 8


def test_bad_sale_does_not_block_the_rest_of_batch(shop):
    p = shop.product(qty=10)
    bad = {"local_id": "x", "items": [{"product_id": "abc", "qty": 1}]}
    r = shop.sync_sales(bad, sale(p, no="1-2"))
    assert [x["ok"] for x in r] == [False, True]
    assert shop.qty(p) == 9


def test_empty_cart_is_rejected(shop):
    r = shop.sync_sales({"local_id": "x", "items": []})
    assert not r[0]["ok"]
    assert shop.count("sales") == 0


def test_debt_payment_txn(shop):
    c = shop.client(debt=50000)
    r = shop.sync_txns({"local_id": "t1", "type": "debt_payment",
                        "amount": 20000, "client_id": c})
    assert r[0]["ok"]
    assert shop.debt(c) == pytest.approx(30000, abs=1)


def test_cash_out_txn(shop):
    r = shop.sync_txns({"local_id": "t1", "type": "cash_out", "amount": 5000})
    assert r[0]["ok"]
    assert shop.one("SELECT direction FROM cash_movements") == "out"


def test_retried_debt_payment_is_not_counted_twice(shop):
    c = shop.client(debt=50000)
    t = {"local_id": "t1", "source": "Kassa-1", "type": "debt_payment",
         "amount": 20000, "client_id": c}
    shop.sync_txns(t)
    shop.sync_txns(t)
    assert shop.debt(c) == pytest.approx(30000, abs=1)


def test_retried_cash_movement_is_not_counted_twice(shop):
    t = {"local_id": "t1", "source": "Kassa-1", "type": "cash_out", "amount": 5000}
    shop.sync_txns(t)
    shop.sync_txns(t)
    assert shop.count("cash_movements") == 1


def test_nasiya_without_client_is_kept_but_not_recorded_as_cash(shop):
    p = shop.product(qty=10)
    r = shop.sync_sales(sale(p, is_nasiya=True))[0]
    # Rad etilsa kassa cheksiz qayta yuboradi — sotuv saqlanadi, pul yozilmaydi.
    assert r["ok"] and r["warning"]
    assert shop.one("SELECT paid_total FROM sales") == 0
    assert shop.one("SELECT is_nasiya FROM sales") == 1
    assert shop.qty(p) == 9


def test_split_debt_without_client_keeps_only_paid_part(shop):
    p = shop.product()
    r = shop.sync_sales(sale(p, payment="split", paid_cash=4000, debt_sum=6000))[0]
    assert r["ok"] and r["warning"]
    assert shop.one("SELECT paid_total FROM sales") == 4000
    assert shop.one("SELECT total FROM sales") == 10000


def test_client_deleted_while_offline(shop):
    p, c = shop.product(), shop.client()
    shop.run(shop.db.delete_client(c))
    r = shop.sync_sales(sale(p, is_nasiya=True, client_id=c))[0]
    assert r["ok"] and r["warning"]
    assert shop.one("SELECT paid_total FROM sales") == 0
    assert shop.one("SELECT client_id FROM sales") == 0


def test_synced_debt_return_without_client_warns_and_takes_no_cash(shop):
    p = shop.product(qty=5)
    r = shop.sync_sales(sale(p, is_return=True, return_method="debt"))[0]
    assert r["ok"] and r["warning"]
    assert shop.one("SELECT paid_cash FROM sales") == 0


def test_same_local_id_at_other_time_is_a_new_movement(shop):
    t = {"local_id": "t1", "type": "cash_in", "amount": 5000,
         "created_at": "2026-11-22 10:00:00"}
    shop.sync_txns(t, {**t, "created_at": "2026-11-22 11:00:00"})
    assert shop.count("cash_movements") == 2


def test_migration_runs_twice_safely(shop):
    from database.channel_db import ChannelDB
    ChannelDB()._persistent._really_close()
    c = shop.client(debt=1000)
    assert shop.sync_txns({"local_id": "m1", "type": "debt_payment",
                           "amount": 500, "client_id": c})[0]["ok"]
