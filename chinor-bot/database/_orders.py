"""Mijoz buyurtmalari."""

import json
import sqlite3
from typing import Optional, List, Dict, Callable, Tuple

from bot.config import CHANNEL_ID, LOW_STOCK_THRESHOLD, USD_RATE_DEFAULT
from database._helpers import (
    DB_PATH, SCHEMA, now_local, _now,
    fmt_usd, fmt_sum, fmt_money, usd_to_sum, sum_to_usd,
)
from database._formatters import (
    _fmt_product, _fmt_client, _fmt_order, _fmt_sale, _fmt_payment, _fmt_admin,
)


class OrdersMixin:
    # ── Buyurtma ─────────────────────────────────────────────────────────────
    async def create_order(self, client_id: int, items: list, note: str = ""):
        c = await self.get_client_by_id(client_id)
        if not c:
            return None, 0
        created = _now()
        order_items = []
        total = 0.0
        for it in items:
            p = await self.get_product_any(it["product_id"])
            line = it["qty"] * it["price"]
            total += line
            order_items.append({
                "product_id": it["product_id"],
                "name": p["name"] if p else "?",
                "qty": it["qty"],
                "price": it["price"],
                "total": line,
                "cost_price": p.get("cost_price", 0) if p else 0
            })
            if p:
                await self.change_qty(it["product_id"], -it["qty"])
        await self.add_debt(client_id, total)

        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    "INSERT INTO orders(client_id, client_tg_id, shop_name, phone, "
                    "items, total, note, status, created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (client_id, c["telegram_id"], c["shop_name"], c.get("phone", ""),
                     json.dumps(order_items, ensure_ascii=False),
                     total, note, "accepted", created)
                )
                oid = cur.lastrowid
                conn.commit()
            finally:
                conn.close()

        data = {
            "id": oid, "client_id": client_id, "client_tg_id": c["telegram_id"],
            "shop_name": c["shop_name"], "phone": c.get("phone", ""),
            "items": order_items, "total": total,
            "note": note, "status": "accepted", "created_at": created
        }
        mid = await self._send(_fmt_order(data))
        if mid:
            with self._lock:
                conn = self._conn()
                try:
                    conn.execute(
                        "UPDATE orders SET channel_msg_id=? WHERE id=?", (mid, oid)
                    )
                    conn.commit()
                finally:
                    conn.close()
        return oid, total

    async def get_order(self, oid: int) -> Optional[dict]:
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT * FROM orders WHERE id=?", (oid,)
                ).fetchone()
                return self._row_to_order(r) if r else None
            finally:
                conn.close()

    async def update_order_status(self, oid: int, status: str):
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE orders SET status=? WHERE id=?", (status, oid)
                )
                conn.commit()
            finally:
                conn.close()
        o = await self.get_order(oid)
        if o:
            mid = o.get("channel_msg_id", 0)
            if mid:
                await self._edit(mid, _fmt_order(o))

    async def get_client_orders(self, cid: int, month: str = None) -> List[dict]:
        with self._lock:
            conn = self._conn()
            try:
                if month:
                    rows = conn.execute(
                        "SELECT * FROM orders WHERE client_id=? AND created_at LIKE ? "
                        "ORDER BY created_at DESC",
                        (cid, f"{month}%")
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM orders WHERE client_id=? ORDER BY created_at DESC",
                        (cid,)
                    ).fetchall()
                return [self._row_to_order(r) for r in rows]
            finally:
                conn.close()

    async def get_recent_orders(self, limit: int = 20) -> List[dict]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM orders ORDER BY created_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()
                return [self._row_to_order(r) for r in rows]
            finally:
                conn.close()

