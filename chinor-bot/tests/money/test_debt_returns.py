"""Qarz to'lovi va qaytarish."""

import pytest


def test_payment_reduces_debt(shop):
    c = shop.client(debt=100000)
    shop.run(shop.db.add_payment(c, 40000))
    assert shop.debt(c) == pytest.approx(60000, abs=1)
    assert shop.count("payments") == 1


def test_overpayment_stops_at_zero(shop):
    c = shop.client(debt=10000)
    shop.run(shop.db.add_payment(c, 25000))
    assert shop.debt(c) == 0


def test_payment_for_unknown_client_writes_nothing(shop):
    assert shop.run(shop.db.add_payment(999999, 1000)) == {}
    assert shop.count("payments") == 0


def test_payment_on_som_only_debt_keeps_remainder(shop):
    c = shop.client(debt=100000, debt_usd=0)
    shop.run(shop.db.add_payment(c, 10000))
    assert shop.debt(c) == pytest.approx(90000, abs=1)


def test_payment_after_rate_change_is_plain_som_math(shop):
    c = shop.client(debt=100000)
    shop.db.set_usd_rate(15000)
    shop.run(shop.db.add_payment(c, 10000))
    assert shop.debt(c) == pytest.approx(90000, abs=1)


def test_return_restores_stock_with_negative_total(shop):
    p = shop.product(qty=10)
    shop.sale([(p, 3, 10000)], paid_cash=30000, receipt_no="1-1")
    r = shop.refund([(p, 2, 10000)], orig_receipt_no="1-1")
    assert r["total"] == -20000
    assert r["is_return"] is True
    assert shop.qty(p) == 9


def test_return_cannot_exceed_sold_qty(shop):
    p = shop.product(qty=10)
    shop.sale([(p, 2, 10000)], paid_cash=20000, receipt_no="1-1")
    r = shop.refund([(p, 5, 10000)], orig_receipt_no="1-1")
    assert r["total"] == -20000
    assert shop.qty(p) == 10


def test_second_return_only_gets_the_remainder(shop):
    p = shop.product(qty=10)
    shop.sale([(p, 3, 10000)], paid_cash=30000, receipt_no="1-1")
    shop.refund([(p, 2, 10000)], orig_receipt_no="1-1")
    r = shop.refund([(p, 2, 10000)], orig_receipt_no="1-1")
    assert r["total"] == -10000
    assert shop.qty(p) == 10


def test_return_to_debt_reduces_debt_and_takes_no_cash(shop):
    p, c = shop.product(), shop.client()
    shop.sale([(p, 3, 10000)], client_id=c, is_nasiya=True, receipt_no="1-1")
    r = shop.refund([(p, 1, 10000)], method="debt", client_id=c,
                    orig_receipt_no="1-1")
    assert r["paid_total"] == 0
    assert shop.debt(c) == 20000


def test_return_of_one_receipt_does_not_block_similar_number(shop):
    p = shop.product(qty=10)
    shop.sale([(p, 2, 10000)], paid_cash=20000, receipt_no="1-10")
    shop.sale([(p, 2, 10000)], paid_cash=20000, receipt_no="1-1")
    shop.refund([(p, 2, 10000)], orig_receipt_no="1-10")
    r = shop.refund([(p, 2, 10000)], orig_receipt_no="1-1")
    assert r["total"] == -20000


def test_cannot_return_product_not_on_receipt(shop):
    sold, other = shop.product(), shop.product(qty=5)
    shop.sale([(sold, 1, 10000)], paid_cash=10000, receipt_no="1-1")
    r = shop.refund([(other, 3, 10000)], orig_receipt_no="1-1")
    assert r["total"] == 0
    assert shop.qty(other) == 5


def test_oversold_then_returned_stock_is_consistent(shop):
    p = shop.product(qty=2)
    shop.sale([(p, 5, 10000)], paid_cash=50000, receipt_no="1-1")
    shop.refund([(p, 5, 10000)], orig_receipt_no="1-1")
    assert shop.qty(p) == 2


def test_partial_returns_after_oversell_restore_only_what_was_taken(shop):
    p = shop.product(qty=2)
    shop.sale([(p, 5, 10000)], paid_cash=50000, receipt_no="1-1")
    shop.refund([(p, 3, 10000)], orig_receipt_no="1-1")
    shop.refund([(p, 2, 10000)], orig_receipt_no="1-1")
    assert shop.qty(p) == 2


def test_old_receipt_without_stock_field_still_restores(shop):
    import json
    p = shop.product(qty=10)
    sid = shop.sale([(p, 3, 10000)], paid_cash=30000, receipt_no="1-1")["id"]
    items = json.loads(shop.one("SELECT items FROM sales WHERE id=?", sid))
    del items[0]["stock_taken"]
    shop.db._conn().execute("UPDATE sales SET items=? WHERE id=?",
                            (json.dumps(items), sid))
    shop.db._conn().commit()
    shop.refund([(p, 3, 10000)], orig_receipt_no="1-1")
    assert shop.qty(p) == 10


def test_return_without_found_receipt_is_still_allowed(shop):
    p = shop.product(qty=1)
    r = shop.refund([(p, 2, 10000)], orig_receipt_no="9-99")
    assert r["total"] == -20000
    assert shop.qty(p) == 3


def test_debt_return_without_client_records_no_cash(shop):
    p = shop.product(qty=5)
    r = shop.refund([(p, 1, 10000)], method="debt")
    assert r["paid_total"] == 0 and r["warning"]
    assert shop.one("SELECT paid_cash FROM sales WHERE id=?", r["id"]) == 0
    assert shop.qty(p) == 6


def test_debt_return_for_deleted_client_records_no_cash(shop):
    p, c = shop.product(qty=5), shop.client(debt=50000)
    shop.run(shop.db.delete_client(c))
    r = shop.refund([(p, 1, 10000)], method="debt", client_id=c)
    assert r["paid_total"] == 0 and r["warning"]


def order(shop, pid, cid, qty):
    oid, _ = shop.run(shop.db.create_order(
        cid, [{"product_id": pid, "qty": qty, "price": 10000}]))
    return oid


def test_cancel_order_restores_stock_and_debt(shop):
    p, c = shop.product(qty=10), shop.client()
    oid = order(shop, p, c, 3)
    assert shop.qty(p) == 7 and shop.debt(c) == 30000
    shop.run(shop.db.cancel_order(oid))
    assert shop.qty(p) == 10 and shop.debt(c) == 0


def test_cancel_oversold_order_restores_only_what_was_taken(shop):
    p, c = shop.product(qty=2), shop.client()
    oid = order(shop, p, c, 5)
    assert shop.qty(p) == 0
    shop.run(shop.db.cancel_order(oid))
    assert shop.qty(p) == 2


def test_cancel_twice_restores_once(shop):
    p, c = shop.product(qty=2), shop.client()
    oid = order(shop, p, c, 1)
    shop.run(shop.db.cancel_order(oid))
    shop.run(shop.db.cancel_order(oid))
    assert shop.qty(p) == 2 and shop.debt(c) == 0


def test_cancel_old_order_without_stock_field(shop):
    import json
    p, c = shop.product(qty=10), shop.client()
    oid = order(shop, p, c, 3)
    items = json.loads(shop.one("SELECT items FROM orders WHERE id=?", oid))
    del items[0]["stock_taken"]
    shop.db._conn().execute("UPDATE orders SET items=? WHERE id=?",
                            (json.dumps(items), oid))
    shop.db._conn().commit()
    shop.run(shop.db.cancel_order(oid))
    assert shop.qty(p) == 10


def test_return_of_delivered_oversold_order_keeps_stock_consistent(shop):
    p, c = shop.product(qty=2), shop.client()
    oid = order(shop, p, c, 5)
    o = shop.run(shop.db.get_order(oid))
    shop.run(shop.db.record_order_sale(o))
    shop.refund([(p, 5, 10000)], orig_receipt_no=f"B-{oid}")
    assert shop.qty(p) == 2
