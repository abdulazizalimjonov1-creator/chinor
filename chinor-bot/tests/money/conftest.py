"""Pul testlari uchun vaqtinchalik baza.

MUHIM: `database` import qilinishi bilan bazani ochib migratsiya qiladi, shuning
uchun DB_PATH birinchi importdan OLDIN vaqtinchalik faylga qaratiladi. Tokenlar
ham bo'shatiladi: testlardan Telegram'ga hech narsa ketmaydi.

Ishga tushirish:  cd chinor-bot && python -m pytest tests/money
"""

import asyncio
import atexit
import os
import shutil
import sys
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="chinor-test-")
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)
_TEST_DB = os.path.join(_TMP, "pos.db")
os.environ.update(
    DATA_DIR=_TMP, DB_PATH=_TEST_DB, BOT_TOKEN="", CHEK_BOT_TOKEN="",
    PAYMENT_BOT_TOKEN="", CHANNEL_ID="",
)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from database._helpers import DB_PATH  # noqa: E402

# Real bazaga yozib yubormaslik uchun: boshqa yo'l bo'lsa, testlar boshlanmaydi.
if os.path.abspath(DB_PATH) != os.path.abspath(_TEST_DB):
    pytest.exit(f"Test real bazaga ulangan: {DB_PATH}", returncode=2)

from database.channel_db import db as _db  # noqa: E402
from bot import web_api  # noqa: E402


class Shop:
    """Testlarni qisqa yozish uchun yordamchi."""

    def __init__(self):
        self.db = _db
        self._phone = 998900000000

    def run(self, coro):
        return asyncio.run(coro)

    def product(self, qty=10, sell=10000, cost=7000):
        self._phone += 1
        return self.run(_db.add_product(
            f"Tovar {self._phone}", "", sell, cost, qty))

    def client(self, debt=0, debt_usd=None):
        self._phone += 1
        phone = str(self._phone)
        assert self.run(_db.add_client(None, f"Mijoz {phone}", phone, 1))
        cid = self.one("SELECT id FROM clients WHERE shop_name=?",
                       f"Mijoz {phone}")
        if debt:
            usd = debt / _db.get_usd_rate() if debt_usd is None else debt_usd
            _db._conn().execute(
                "UPDATE clients SET debt=?, debt_usd=? WHERE id=?",
                (debt, usd, cid))
            _db._conn().commit()
        return cid

    def one(self, sql, *args):
        return _db._conn().execute(sql, args).fetchone()[0]

    def qty(self, pid):
        return self.one("SELECT qty FROM products WHERE id=?", pid)

    def debt(self, cid):
        return self.one("SELECT debt FROM clients WHERE id=?", cid)

    def count(self, table):
        return self.one(f"SELECT COUNT(*) FROM {table}")

    def sale(self, items, **kw):
        lines = [{"product_id": p, "qty": q, "price_sum": s} for p, q, s in items]
        return self.run(_db.create_sale(1, "Kassir", lines, **kw))

    def refund(self, items, **kw):
        lines = [{"product_id": p, "qty": q, "price_sum": s} for p, q, s in items]
        return self.run(_db.create_return(1, "Kassir", lines, **kw))

    def _post(self, path, handler, body):
        async def go():
            app = web.Application()
            app.router.add_post(path, handler)
            async with TestClient(TestServer(app)) as c:
                r = await c.post(path, json=body)
                assert r.status == 200
                return (await r.json())["results"]
        return self.run(go())

    def sync_sales(self, *sales):
        return self._post("/api/sync/sales", web_api.api_sync_sales,
                          {"sales": list(sales)})

    def sync_txns(self, *txns):
        return self._post("/api/sync/cash-txns", web_api.api_sync_cash_txns,
                          {"txns": list(txns)})


@pytest.fixture
def shop(monkeypatch):
    async def admin(request, db):
        return {"role": "admin", "tg_id": 1, "is_glavniy": True,
                "user": {"full_name": "Test kassir"}}
    monkeypatch.setattr(web_api, "_authenticate", admin)

    conn = _db._conn()
    for table in ("sales", "orders", "payments", "cash_movements", "products"):
        conn.execute(f"DELETE FROM {table}")
    conn.execute("DELETE FROM clients WHERE COALESCE(is_internal,0)=0")
    conn.commit()
    _db.set_usd_rate(12500)
    return Shop()
