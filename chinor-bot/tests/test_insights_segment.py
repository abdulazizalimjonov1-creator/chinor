"""insights._segment: mijoz to'g'ri guruhga tushadimi.
Ishlatish (chinor-bot ichida): PYTHONPATH=. python3 tests/test_insights_segment.py"""

import sys
import types

# insights.py import paytida bot.api_receipts va database._helpers ni so'raydi —
# segment mantiqi ularsiz ishlaydi, shuning uchun yengil o'rinbosar qo'yamiz.
sys.modules.setdefault("bot.api_receipts", types.SimpleNamespace(_db=None, _product_costs=dict))
if "database._helpers" not in sys.modules:
    from datetime import datetime
    sys.modules["database._helpers"] = types.SimpleNamespace(
        DB_PATH="", now_local=lambda: datetime(2026, 11, 19, 12, 0))

from bot.insights import _segment, _alias, alias_question, unalias  # noqa: E402


def c(**k):
    base = dict(revenue=0.0, debt_now=0.0, debt_taken=0.0, debt_paid=0.0,
                first_at="2025-01-01", last_at="2026-11-18", checks_all=50)
    base.update(k)
    return base


def test():
    # Hamma narsani qarzga oldi, hech narsa to'lamadi → xavfli
    assert _segment(c(revenue=13e6, debt_now=13e6, debt_taken=13e6), 10e6, 90, "") == "risk"
    # Katta mijoz, qarzi o'sdi, lekin ko'p to'layapti → kuzatish (xavfli emas)
    assert _segment(c(revenue=84e6, debt_now=19e6, debt_taken=96e6, debt_paid=64e6), 10e6, 90, "") == "watch"
    # Top savdo, qarzi kam va kamaymoqda → oltin
    assert _segment(c(revenue=29e6, debt_now=3e6, debt_taken=29e6, debt_paid=32e6), 10e6, 90, "") == "gold"
    # 45 kundan beri kelmagan doimiy mijoz → uxlab qolgan
    assert _segment(c(revenue=2e6, last_at="2026-09-30"), 10e6, 90, "") == "sleep"
    # Davr ichida birinchi marta kelgan → yangi
    assert _segment(c(revenue=1e6, first_at="2026-11-01"), 10e6, 90, "") == "new"
    # Qarzi kamaygan, lekin savdosining yarmidan katta → kuzatish (yaxshi emas)
    assert _segment(c(revenue=35e6, debt_now=21e6, debt_taken=40e6, debt_paid=45e6), 10e6, 90, "") == "watch"
    # Oddiy, qarzsiz, o'rtacha savdo → yaxshi
    assert _segment(c(revenue=2e6), 10e6, 90, "") == "good"
    # Tashqi AI'ga ism ketmaydi: taxallus ↔ ism ikki tomonga to'g'ri
    a = {}
    for n in ["Ali aka usta", "Vali aka do'kon"] + [f"X{i}" for i in range(9)]:
        _alias(n, a)
    assert alias_question("VALI AKA do'kon qarzi?", a) == "Mijoz#02 qarzi?"
    assert unalias("Mijoz#10 va Mijoz#01", a) == "X7 va Ali aka usta"
    print("OK")


if __name__ == "__main__":
    test()
