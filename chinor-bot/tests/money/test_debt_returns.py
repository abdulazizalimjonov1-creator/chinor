"""Qarz to'lovi va qaytarish.

`xfail` = hozirgi kodda ISBOTLANGAN xato. Tuzatilganda test «kutilmaganda o'tdi»
deb qizaradi (strict) — o'shanda belgini olib tashlash kerak.
"""

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


@pytest.mark.xfail(strict=True, reason="BUG: debt_usd=0 bo'lsa, to'lov butun so'm qarzni o'chiradi")
def test_payment_on_som_only_debt_keeps_remainder(shop):
    c = shop.client(debt=100000, debt_usd=0)
    shop.run(shop.db.add_payment(c, 10000))
    assert shop.debt(c) == pytest.approx(90000, abs=1)


@pytest.mark.xfail(strict=True, reason="BUG: kurs o'zgarsa, to'lovda so'm qarz USD orqali qayta hisoblanadi")
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


@pytest.mark.xfail(strict=True, reason="BUG: 1-1 qaytarilgani 1-10 chekining qaytarishini ham kamaytiradi (LIKE)")
def test_return_of_one_receipt_does_not_block_similar_number(shop):
    p = shop.product(qty=10)
    shop.sale([(p, 2, 10000)], paid_cash=20000, receipt_no="1-10")
    shop.sale([(p, 2, 10000)], paid_cash=20000, receipt_no="1-1")
    shop.refund([(p, 2, 10000)], orig_receipt_no="1-10")
    r = shop.refund([(p, 2, 10000)], orig_receipt_no="1-1")
    assert r["total"] == -20000


@pytest.mark.xfail(strict=True, reason="BUG: asl chekda bo'lmagan tovarni ham qaytarib, pul berish mumkin")
def test_cannot_return_product_not_on_receipt(shop):
    sold, other = shop.product(), shop.product(qty=5)
    shop.sale([(sold, 1, 10000)], paid_cash=10000, receipt_no="1-1")
    r = shop.refund([(other, 3, 10000)], orig_receipt_no="1-1")
    assert r["total"] == 0
    assert shop.qty(other) == 5


@pytest.mark.xfail(strict=True, reason="BUG: qoldiqdan ko'p sotilsa kamomad yo'qoladi, qaytarishda ortiqcha qoldiq paydo bo'ladi")
def test_oversold_then_returned_stock_is_consistent(shop):
    p = shop.product(qty=2)
    shop.sale([(p, 5, 10000)], paid_cash=50000, receipt_no="1-1")
    shop.refund([(p, 5, 10000)], orig_receipt_no="1-1")
    assert shop.qty(p) == 2
