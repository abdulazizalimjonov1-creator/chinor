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

    async def cancel_order(self, oid: int):
        """Buyurtmani bekor qiladi: ombor qoldig'i TIKLANADI, yaratilishda
        yozilgan qarz QAYTARILADI. Yetkazilgan/tasdiqlangan buyurtma bekor
        qilinmaydi (u allaqachon sotuvga aylangan).
        Qaytaradi: (order|None, xato_matni)."""
        o = await self.get_order(oid)
        if not o:
            return None, "Buyurtma topilmadi"
        st = o.get("status") or ""
        if st == "cancelled":
            return o, ""   # allaqachon bekor — takror tiklamaymiz (idempotent)
        if st in ("delivered", "confirmed"):
            return None, "Yetkazilgan buyurtma bekor qilinmaydi"
        # 1) Ombor qoldig'ini tiklaymiz
        for it in o.get("items", []):
            try:
                await self.change_qty(it["product_id"], abs(float(it.get("qty") or 0)))
            except Exception:
                pass
        # 2) Yaratilishda qo'shilgan qarzni qaytaramiz
        total = float(o.get("total", 0) or 0)
        if total > 0 and o.get("client_id"):
            try:
                await self.add_debt(o["client_id"], -total)
            except Exception:
                pass
        # 3) Statusni yangilaymiz (kanal posti ham yangilanadi)
        await self.update_order_status(oid, "cancelled")
        return await self.get_order(oid), ""

    async def record_order_sale(self, order: dict) -> Optional[dict]:
        """Yetkazilgan buyurtmani `sales` jadvaliga yozadi — shunda u tushum/
        foyda hisobotlariga kiradi. Ombor va qarz TEGILMAYDI (ikkalasi buyurtma
        yaratilganda hisoblangan). Chek raqami "B-<id>" — takror chaqirilsa
        (masalan, admin tugmani ikki marta bossa) qayta yozilmaydi."""
        if not order:
            return None
        oid = int(order.get("id") or 0)
        receipt_no = f"B-{oid}"
        source = "buyurtma"
        if await self.find_sale_by_receipt(receipt_no, source):
            return None   # allaqachon sotuvga aylangan
        rate = self.get_usd_rate()
        sale_items = []
        total_sum = 0.0
        cost_known = True
        for it in order.get("items", []):
            qty = float(it.get("qty") or 0)
            price_sum = float(it.get("price") or 0)
            line_sum = round(price_sum * qty, 2)
            total_sum += line_sum
            cost_sum = float(it.get("cost_price") or 0)
            cost_usd = 0.0
            p = await self.get_product_any(it.get("product_id") or 0)
            if p:
                if cost_sum <= 0:
                    cost_sum = float(p.get("cost_price", 0) or 0)
                cost_usd = float(p.get("cost_price_usd", 0) or 0)
            if cost_sum <= 0:
                cost_known = False
            sale_items.append({
                "product_id": it.get("product_id") or 0,
                "name": it.get("name", "") or "?",
                "qty": qty,
                "unit": (p.get("unit", "dona") if p else "dona"),
                "price": price_sum,
                "total": line_sum,
                "cost_price": cost_sum,
                "price_usd": round(sum_to_usd(price_sum, rate), 4),
                "total_usd": round(sum_to_usd(line_sum, rate), 4),
                "cost_price_usd": cost_usd or round(sum_to_usd(cost_sum, rate), 4),
            })
        if not sale_items:
            return None
        total_usd = round(sum_to_usd(total_sum, rate), 4)
        created = _now()   # yetkazilgan payt — hisobotga shu sana kiradi
        client_name = order.get("shop_name", "") or ""
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    "INSERT INTO sales(cashier_id, cashier_name, items, "
                    "total, total_usd, subtotal, subtotal_usd, discount, discount_usd, "
                    "usd_rate, paid_cash, paid_cash_usd, paid_card, paid_card_usd, "
                    "paid_other, paid_other_usd, paid_total, paid_total_usd, paid_currency, "
                    "change_amount, change_usd, is_nasiya, client_id, client_name, "
                    "created_at, source, receipt_no, is_internal) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (0, "Buyurtma (bot)",
                     json.dumps(sale_items, ensure_ascii=False),
                     total_sum, total_usd, total_sum, total_usd, 0.0, 0.0,
                     rate, 0, 0, 0, 0, 0, 0, 0, 0, "sum",
                     0.0, 0.0, 1, order.get("client_id") or 0, client_name,
                     created, source, receipt_no, 0)
                )
                sid = cur.lastrowid
                conn.commit()
            finally:
                conn.close()
        data = {
            "id": sid, "items": sale_items, "total": total_sum,
            "total_usd": total_usd, "subtotal": total_sum, "discount": 0.0,
            "usd_rate": rate, "is_nasiya": True, "is_internal": False,
            "client_id": order.get("client_id") or 0, "client_name": client_name,
            "created_at": created, "source": source, "receipt_no": receipt_no,
            "cashier_name": "Buyurtma (bot)", "paid_total": 0.0,
            "paid_cash": 0, "paid_card": 0, "paid_other": 0,
            "cost_known": cost_known,
        }
        # Oddiy sotuvlar kanaliga ham post (kassa sotuvlari bilan bir xil oqim)
        try:
            from bot.config import CHANNEL_SALES_ID
            await self._post_to(CHANNEL_SALES_ID, _fmt_sale(data))
        except Exception:
            pass
        return data

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

