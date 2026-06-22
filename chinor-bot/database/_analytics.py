"""AI tahlili uchun ma'lumotlar yig'ish (AnalyticsMixin).

Bu modul ChannelDB'ga AI 'analitika' tugmasi uchun kerakli barcha
agregatsiyalarni qo'shadi:

  • log_search()              — har bir qidiruvni jurnal qiladi
  • get_search_misses()       — mijozlar qidirib, topa olmagan tovarlar
  • get_slow_moving_products() — omborda turib, sotilmayotgan tovarlar
  • get_top_selling_recent()  — yaqindagi eng yaxshi sotilganlar
  • gather_ai_context()       — Geminiga uzatiladigan to'liq kontekst dict

Hech bir metod LLM bilan ishlamaydi — faqat raw ma'lumotlarni qaytaradi.
LLM bilan ishlash bot/gemini_analyzer.py ichida.
"""

import json
import logging
from datetime import timedelta
from typing import Optional, List, Dict

from database._helpers import _now, now_local

logger = logging.getLogger(__name__)


class AnalyticsMixin:
    # ─── Qidiruv jurnali ─────────────────────────────────────────────────────
    async def log_search(self, user_id: int, query: str,
                         found_count: int, source: str = "") -> None:
        """Bitta qidiruv urinishini jurnalga yozadi. Hech qanday xato
        bo'lmasligi kerak — bot'ning asosiy oqimini bloklamasin."""
        q = (query or "").strip()
        if not q:
            return
        qn = q.lower()
        try:
            with self._lock:
                conn = self._conn()
                try:
                    conn.execute(
                        "INSERT INTO search_log(user_id, query, query_norm, "
                        "found_count, source, created_at) VALUES(?,?,?,?,?,?)",
                        (int(user_id or 0), q, qn,
                         int(found_count or 0), source[:32], _now())
                    )
                    conn.commit()
                finally:
                    conn.close()
        except Exception as e:
            # Jurnaldagi xato — sotuv/qidiruvni buzmasin, lekin log qilamiz
            logger.warning(f"log_search failed: {e}")

    async def get_search_misses(self, days: int = 30,
                                 limit: int = 20) -> List[dict]:
        """Mijozlar qidirib, hech narsa topmagan so'rovlar (top-N).
        Bir xil so'z bir necha marta qidirilsa — guruhlab beradi.

        Qaytaradi: [{'query', 'attempts', 'last_at'}, ...]"""
        since = (now_local() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT query_norm AS q, "
                    "       COUNT(*)   AS attempts, "
                    "       MAX(query) AS query, "
                    "       MAX(created_at) AS last_at "
                    "FROM search_log "
                    "WHERE found_count = 0 AND created_at >= ? "
                    "GROUP BY query_norm "
                    "ORDER BY attempts DESC, last_at DESC "
                    "LIMIT ?",
                    (since, limit)
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    # ─── Sotuv reytingi (yaqindagi) ──────────────────────────────────────────
    def _sales_since_sync(self, since: str) -> list:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT items, total, total_usd, created_at FROM sales "
                    "WHERE created_at >= ?", (since,)
                ).fetchall()
            finally:
                conn.close()
        out = []
        for r in rows:
            try:
                items = json.loads(r["items"]) if r["items"] else []
            except Exception:
                items = []
            out.append({
                "items": items,
                "total": r["total"] or 0,
                "total_usd": (r["total_usd"] if "total_usd" in r.keys() else 0) or 0,
                "created_at": r["created_at"],
            })
        return out

    async def get_top_selling_recent(self, days: int = 30,
                                      limit: int = 10) -> List[dict]:
        """So'nggi N kun ichidagi top sotilgan mahsulotlar.
        Qaytaradi: [{'product_id', 'name', 'qty', 'revenue', 'revenue_usd'}, ...]"""
        since = (now_local() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        sales = await self._in_thread(self._sales_since_sync, since)
        agg: Dict[int, dict] = {}
        for s in sales:
            for i in s.get("items", []):
                pid = i.get("product_id")
                if not pid:
                    continue
                a = agg.setdefault(pid, {
                    "product_id": pid, "name": i.get("name", ""),
                    "qty": 0.0, "revenue": 0.0, "revenue_usd": 0.0,
                })
                a["qty"] += float(i.get("qty", 0) or 0)
                a["revenue"] += float(i.get("total", 0) or 0)
                a["revenue_usd"] += float(i.get("total_usd", 0) or 0)
        return sorted(agg.values(), key=lambda x: x["qty"], reverse=True)[:limit]

    # ─── Sotilmayotgan tovarlar (slow moving) ───────────────────────────────
    async def get_slow_moving_products(self, days: int = 60,
                                        limit: int = 20) -> List[dict]:
        """Omborda turibdi-yu, so'nggi N kunda kam (yoki umuman) sotilmagan tovarlar.
        Faqat is_active=1 va qty>0 bo'lganlari hisobga olinadi.

        Qaytaradi: [{...product, 'sold_qty': N, 'days_in_stock': D}, ...]"""
        since = (now_local() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        sales = await self._in_thread(self._sales_since_sync, since)
        sold_qty: Dict[int, float] = {}
        for s in sales:
            for i in s.get("items", []):
                pid = i.get("product_id")
                if pid:
                    sold_qty[pid] = sold_qty.get(pid, 0) + float(i.get("qty", 0) or 0)
        prods = await self.get_all_products(active_only=True)
        slow = []
        for p in prods:
            if (p.get("qty", 0) or 0) <= 0:
                continue
            sq = sold_qty.get(p["id"], 0)
            slow.append({**p, "sold_qty_recent": sq})
        # Eng sotilmaganlar oldinda; teng bo'lsa eski qoldiq oldinda
        slow.sort(key=lambda x: (x["sold_qty_recent"], -float(x.get("qty", 0) or 0)))
        return slow[:limit]

    # ─── To'liq AI-context yig'ish ───────────────────────────────────────────
    async def gather_ai_context(self, days: int = 30) -> dict:
        """Geminiga uzatish uchun to'liq biznes konteksti.
        Hammasi bitta dict — keyin gemini_analyzer.py promptga aylantiradi."""
        # Umumiy raqamlar
        rate = self.get_usd_rate()
        all_prods = await self.get_all_products(active_only=True)
        clients = await self.get_all_clients()
        # Bu oy / barcha vaqt
        month = now_local().strftime("%Y-%m")
        st_month = await self.stats_month(month)
        st_all = await self.stats_all_time()
        low_stock = await self.get_low_stock()
        debtors = await self.get_debtors()
        # Analitika
        top_recent = await self.get_top_selling_recent(days=days, limit=10)
        slow_moving = await self.get_slow_moving_products(days=days * 2, limit=15)
        search_misses = await self.get_search_misses(days=days, limit=20)

        # Mahsulot xaritalari (id → name, qty, unit)
        prod_map = {p["id"]: p for p in all_prods}

        # Slow moving uchun do'kon-ichki yorliq
        slow_list = []
        for p in slow_moving:
            slow_list.append({
                "id": p["id"],
                "name": p.get("name", ""),
                "unit": p.get("unit", "dona"),
                "qty_in_stock": float(p.get("qty", 0) or 0),
                "sold_last_period": float(p.get("sold_qty_recent", 0) or 0),
                "cost_price_usd": float(p.get("cost_price_usd", 0) or 0),
            })

        top_list = []
        for t in top_recent:
            p = prod_map.get(t["product_id"], {})
            top_list.append({
                "name": t.get("name", ""),
                "unit": p.get("unit", "dona"),
                "qty_sold": float(t.get("qty", 0) or 0),
                "revenue_sum": float(t.get("revenue", 0) or 0),
                "revenue_usd": float(t.get("revenue_usd", 0) or 0),
                "qty_left_in_stock": float(p.get("qty", 0) or 0),
            })

        low_list = [{
            "name": p["name"],
            "unit": p.get("unit", "dona"),
            "qty_left": float(p.get("qty", 0) or 0),
        } for p in low_stock[:30]]

        return {
            "period_days": days,
            "today": now_local().strftime("%Y-%m-%d"),
            "currency_rate_usd_to_sum": rate,
            "totals": {
                "active_products": len(all_prods),
                "clients": len(clients),
                "debtors": len(debtors),
                "low_stock_count": len(low_stock),
                "month": {
                    "sales_count": st_month.get("sale_count", 0),
                    "revenue_sum": st_month.get("revenue", 0),
                    "revenue_usd": st_month.get("revenue_usd", 0),
                    "profit_sum": st_month.get("profit", 0),
                    "profit_usd": st_month.get("profit_usd", 0),
                },
                "all_time": {
                    "revenue_sum": st_all.get("revenue", 0),
                    "revenue_usd": st_all.get("revenue_usd", 0),
                    "profit_sum": st_all.get("profit", 0),
                    "profit_usd": st_all.get("profit_usd", 0),
                },
            },
            "top_selling": top_list,
            "slow_moving": slow_list,
            "search_misses": [
                {"query": m["query"],
                 "attempts": int(m["attempts"]),
                 "last_at": m.get("last_at", "")}
                for m in search_misses
            ],
            "low_stock": low_list,
        }
