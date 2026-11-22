"""Sotuv: summa, chegirma, qaytim, nasiya, aralash to'lov, ichki rasxod."""

import pytest


def test_cash_sale_records_total_and_reduces_stock(shop):
    p = shop.product(qty=10)
    s = shop.sale([(p, 3, 10000)], paid_cash=30000)
    assert s["total"] == 30000
    assert s["paid_total"] == 30000
    assert s["change"] == 0
    assert shop.qty(p) == 7


def test_change_is_paid_minus_total(shop):
    p = shop.product()
    s = shop.sale([(p, 1, 16000)], paid_cash=20000)
    assert s["change"] == 4000


def test_som_price_is_kept_exactly(shop):
    # Eski xato: so'm USDga bo'linib qaytganda 16 000 → 15 999.72 bo'lardi.
    shop.db.set_usd_rate(12683.37)
    p = shop.product()
    s = shop.sale([(p, 1, 16000)], paid_cash=16000)
    assert s["items"][0]["price"] == 16000
    assert s["total"] == 16000


def test_fractional_quantity(shop):
    p = shop.product(qty=10)
    s = shop.sale([(p, 2.5, 10000)], paid_cash=25000)
    assert s["total"] == 25000
    assert shop.qty(p) == pytest.approx(7.5)


def test_multiple_items_sum(shop):
    a, b = shop.product(), shop.product()
    s = shop.sale([(a, 2, 5000), (b, 1, 12000)], paid_cash=22000)
    assert s["total"] == 22000
    assert shop.qty(a) == 8 and shop.qty(b) == 9


def test_rounded_total_becomes_discount(shop):
    p = shop.product()
    s = shop.sale([(p, 1, 10500)], paid_cash=10000, override_total_sum=10000)
    assert s["subtotal"] == 10500
    assert s["total"] == 10000
    assert s["discount"] == 500


def test_nasiya_adds_full_total_to_debt(shop):
    p, c = shop.product(), shop.client()
    s = shop.sale([(p, 2, 10000)], client_id=c, is_nasiya=True)
    assert s["paid_total"] == 0
    assert shop.debt(c) == 20000


def test_split_payment_puts_only_remainder_on_debt(shop):
    p, c = shop.product(), shop.client()
    s = shop.sale([(p, 1, 100000)], client_id=c, paid_cash=30000,
                  paid_card=20000, debt_sum=50000)
    assert s["paid_cash"] == 30000 and s["paid_card"] == 20000
    assert s["paid_total"] == 50000
    assert shop.debt(c) == 50000


def test_internal_expense_uses_cost_and_takes_no_money(shop):
    p, c = shop.product(sell=10000, cost=7000), shop.client()
    s = shop.sale([(p, 2, 10000)], client_id=c, is_internal=True, paid_cash=99)
    assert s["total"] == 14000
    assert s["paid_total"] == 0
    assert shop.debt(c) == 0
    assert shop.qty(p) == 8


def test_exchange_rate_change_does_not_move_saved_sale(shop):
    p = shop.product()
    sid = shop.sale([(p, 1, 10000)], paid_cash=10000)["id"]
    shop.db.set_usd_rate(15000)
    assert shop.run(shop.db.get_sale(sid))["total"] == 10000
