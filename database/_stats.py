"""Statistika va hisobotlar (debtorlar, past qoldiq ham)."""

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


class StatsMixin:
    # ── Statistika ───────────────────────────────────────────────────────────
    def _stats_from_sales(self, sales: list) -> dict:
        """Sotuvlar ro'yxatidan so'm va USD bo'yicha jami metrikalar.
        «Chinor» ichki rasxod sotuvlari (is_internal) daromad/foyda hisobiga
        KIRMAYDI — ular alohida 'expense' (rasxod, tannarx bo'yicha) sifatida
        qaytariladi."""
        normal = [s for s in sales if not s.get("is_internal")]
        internal = [s for s in sales if s.get("is_internal")]
        rev_sum = sum(s.get("total", 0) or 0 for s in normal)
        rev_usd = sum(s.get("total_usd", 0) or 0 for s in normal)
        cost_sum = 0.0
        cost_usd = 0.0
        for s in normal:
            for i in s.get("items", []):
                cost_sum += float(i.get("cost_price", 0) or 0) * float(i.get("qty", 0) or 0)
                cost_usd += float(i.get("cost_price_usd", 0) or 0) * float(i.get("qty", 0) or 0)
        cash_sum = sum(s.get("paid_cash", 0) or 0 for s in normal)
        cash_usd = sum(s.get("paid_cash_usd", 0) or 0 for s in normal)
        card_sum = sum(s.get("paid_card", 0) or 0 for s in normal)
        card_usd = sum(s.get("paid_card_usd", 0) or 0 for s in normal)
        nasiya_sum = sum(s.get("total", 0) or 0 for s in normal if s.get("is_nasiya"))
        nasiya_usd = sum(s.get("total_usd", 0) or 0 for s in normal if s.get("is_nasiya"))
        disc_sum = sum(s.get("discount", 0) or 0 for s in normal)
        disc_usd = sum(s.get("discount_usd", 0) or 0 for s in normal)
        # «Chinor» ichki rasxod (tovar tannarx narxida chiqarilgan)
        expense_sum = sum(s.get("total", 0) or 0 for s in internal)
        expense_usd = sum(s.get("total_usd", 0) or 0 for s in internal)
        return {
            "revenue": rev_sum, "revenue_usd": rev_usd,
            "cost": cost_sum, "cost_usd": cost_usd,
            "profit": rev_sum - cost_sum, "profit_usd": rev_usd - cost_usd,
            "cash_total": cash_sum, "cash_total_usd": cash_usd,
            "card_total": card_sum, "card_total_usd": card_usd,
            "nasiya_total": nasiya_sum, "nasiya_total_usd": nasiya_usd,
            "discount_total": disc_sum, "discount_total_usd": disc_usd,
            "expense": expense_sum, "expense_usd": expense_usd,
            "expense_count": len(internal),
            "sale_count": len(normal),
        }

    async def stats_day(self, date: str) -> dict:
        sales = await self.get_sales_by_date(date)
        with self._lock:
            conn = self._conn()
            try:
                ord_row = conn.execute(
                    "SELECT COUNT(*) AS c, COALESCE(SUM(total),0) AS t "
                    "FROM orders WHERE created_at LIKE ?", (f"{date}%",)
                ).fetchone()
            finally:
                conn.close()
        m = self._stats_from_sales(sales)  # sale_count = ichki bo'lmagan sotuvlar
        m.update({
            "order_count": ord_row["c"] if ord_row else 0,
            "order_revenue": ord_row["t"] if ord_row else 0,
        })
        return m

    async def stats_month(self, month: str) -> dict:
        sales = await self.get_sales_by_month(month)
        with self._lock:
            conn = self._conn()
            try:
                ord_row = conn.execute(
                    "SELECT COUNT(*) AS c, COALESCE(SUM(total),0) AS t "
                    "FROM orders WHERE created_at LIKE ?", (f"{month}%",)
                ).fetchone()
            finally:
                conn.close()
        m = self._stats_from_sales(sales)  # sale_count = ichki bo'lmagan sotuvlar
        m.update({
            "order_count": ord_row["c"] if ord_row else 0,
            "order_revenue": ord_row["t"] if ord_row else 0,
        })
        return m

    def _stats_all_time_sync(self) -> dict:
        """Barcha sotuvlar bo'yicha umumiy statistika — sinxron, ishchi oqimda."""
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT items, total, total_usd, is_internal FROM sales"
                ).fetchall()
            finally:
                conn.close()
        sales = []
        for r in rows:
            try:
                items = json.loads(r["items"])
            except Exception:
                items = []
            keys = r.keys()
            sales.append({
                "total": r["total"] or 0,
                "total_usd": (r["total_usd"] if "total_usd" in keys else 0) or 0,
                "is_internal": (r["is_internal"] if "is_internal" in keys else 0) or 0,
                "items": items,
            })
        m = self._stats_from_sales(sales)
        return {
            "revenue": m["revenue"], "revenue_usd": m["revenue_usd"],
            "cost": m["cost"], "cost_usd": m["cost_usd"],
            "profit": m["profit"], "profit_usd": m["profit_usd"],
            "expense": m["expense"], "expense_usd": m["expense_usd"],
        }

    async def stats_all_time(self) -> dict:
        # Og'ir agregatsiya — ishchi oqimda
        return await self._in_thread(self._stats_all_time_sync)

    async def top_products(self, month: str = None, limit: int = 10) -> List[dict]:
        if month:
            sales = await self.get_sales_by_month(month)
        else:
            sales = await self.get_all_sales()  # executor orqali — bloklamaydi
        stats: Dict[int, dict] = {}
        for s in sales:
            for i in s.get("items", []):
                pid = i["product_id"]
                if pid not in stats:
                    stats[pid] = {"name": i["name"], "qty": 0,
                                   "revenue": 0, "revenue_usd": 0}
                stats[pid]["qty"] += i["qty"]
                stats[pid]["revenue"] += i.get("total", 0) or 0
                stats[pid]["revenue_usd"] += i.get("total_usd", 0) or 0
        return sorted(stats.values(), key=lambda x: x["qty"], reverse=True)[:limit]

    def _top_clients_sync(self, limit: int) -> List[dict]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute("""
                    SELECT c.*,
                           COALESCE(SUM(o.total), 0) AS total_spent,
                           COUNT(o.id)               AS order_count
                    FROM clients c
                    LEFT JOIN orders o ON o.client_id = c.id
                    GROUP BY c.id
                    ORDER BY total_spent DESC
                    LIMIT ?
                """, (limit,)).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    async def top_clients(self, limit: int = 10) -> List[dict]:
        # JOIN + GROUP BY — ishchi oqimda
        return await self._in_thread(self._top_clients_sync, limit)

    async def client_monthly_report(self, cid: int, month: str) -> dict:
        with self._lock:
            conn = self._conn()
            try:
                ord_row = conn.execute(
                    "SELECT COALESCE(SUM(total),0) AS t FROM orders "
                    "WHERE client_id=? AND created_at LIKE ?",
                    (cid, f"{month}%")
                ).fetchone()
                pay_row = conn.execute(
                    "SELECT COALESCE(SUM(amount),0) AS t FROM payments "
                    "WHERE client_id=? AND created_at LIKE ?",
                    (cid, f"{month}%")
                ).fetchone()
                cli_row = conn.execute(
                    "SELECT debt FROM clients WHERE id=?", (cid,)
                ).fetchone()
            finally:
                conn.close()
        return {
            "total_ordered": ord_row["t"] if ord_row else 0,
            "total_paid":    pay_row["t"] if pay_row else 0,
            "current_debt":  cli_row["debt"] if cli_row else 0,
        }

    async def get_debtors(self) -> List[dict]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM clients WHERE debt > 0 ORDER BY debt DESC"
                ).fetchall()
                return [self._row_to_client(r) for r in rows]
            finally:
                conn.close()

    async def get_low_stock(self, threshold: float = None) -> List[dict]:
        if threshold is None:
            threshold = LOW_STOCK_THRESHOLD
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM products WHERE is_active=1 AND qty<=? "
                    "ORDER BY qty ASC, name",
                    (threshold,)
                ).fetchall()
                return [self._row_to_product(r) for r in rows]
            finally:
                conn.close()
