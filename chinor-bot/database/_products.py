"""Mahsulotlar: qo'shish, qidirish, narx, qoldiq."""

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


class ProductsMixin:
    # ── Mahsulot ─────────────────────────────────────────────────────────────
    async def add_product(self, name: str, description: str,
                          sell_price_usd: float, cost_price_usd: float,
                          qty: float, image_file_id: str = "",
                          unit: str = "dona",
                          wholesale_price_usd: float = 0,
                          barcode: str = "",
                          category_id: int = 0,
                          supplier_id: int = 0) -> int:
        """Mahsulot qo'shish — narxlar USD da kelinadi (sentlarda).
        So'm versiyasi joriy kurs bo'yicha keshlanadi.
        barcode — ixtiyoriy, shtrix-kod matni (raqamlar yoki kod).
        category_id / supplier_id — ixtiyoriy bog'lanishlar (0 — biriktirilmagan)."""
        created = _now()
        rate = self.get_usd_rate()
        sell_sum = round(usd_to_sum(sell_price_usd, rate), 2)
        cost_sum = round(usd_to_sum(cost_price_usd, rate), 2)
        whs_sum = round(usd_to_sum(wholesale_price_usd, rate), 2) \
            if wholesale_price_usd else 0
        barcode = (barcode or "").strip()
        category_id = int(category_id or 0)
        supplier_id = int(supplier_id or 0)
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    "INSERT INTO products(name, description, sell_price, "
                    "wholesale_price, cost_price, "
                    "sell_price_usd, wholesale_price_usd, cost_price_usd, "
                    "qty, unit, image_file_id, barcode, "
                    "category_id, supplier_id, is_active, created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)",
                    (name, description, sell_sum, whs_sum, cost_sum,
                     sell_price_usd, wholesale_price_usd, cost_price_usd,
                     qty, unit, image_file_id, barcode,
                     category_id, supplier_id, created)
                )
                pid = cur.lastrowid
                conn.commit()
            finally:
                conn.close()

        data = {
            "id": pid, "name": name, "description": description,
            "sell_price": sell_sum, "wholesale_price": whs_sum,
            "cost_price": cost_sum,
            "sell_price_usd": sell_price_usd,
            "wholesale_price_usd": wholesale_price_usd,
            "cost_price_usd": cost_price_usd,
            "qty": qty, "unit": unit, "image_file_id": image_file_id,
            "barcode": barcode,
            "category_id": category_id, "supplier_id": supplier_id,
            "is_active": 1, "created_at": created
        }
        # Kanalga FAQAT rasmli mahsulot yuboriladi (narxi caption'da + 'Sotib olish' tugmasi).
        mid = 0
        if image_file_id:
            cap = _fmt_product(data)
            mid = await self._send_photo(image_file_id, cap, product_id=pid)
        if mid:
            with self._lock:
                conn = self._conn()
                try:
                    conn.execute(
                        "UPDATE products SET channel_msg_id=? WHERE id=?", (mid, pid)
                    )
                    conn.commit()
                finally:
                    conn.close()
        return pid

    async def get_product(self, pid: int) -> Optional[dict]:
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT * FROM products WHERE id=? AND is_active=1", (pid,)
                ).fetchone()
                return self._row_to_product(r) if r else None
            finally:
                conn.close()

    async def get_product_any(self, pid: int) -> Optional[dict]:
        """is_active e'tiborsiz holda mahsulotni qaytaradi."""
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT * FROM products WHERE id=?", (pid,)
                ).fetchone()
                return self._row_to_product(r) if r else None
            finally:
                conn.close()

    async def get_all_products(self, active_only=True) -> List[dict]:
        # Og'ir o'qish — ishchi oqimda
        return await self._in_thread(self._all_products, active_only)

    def _sales_rank_sync(self) -> Dict[int, float]:
        """{product_id: jami_sotilgan_miqdor} — barcha sotuvlar bo'yicha.
        Sinxron — _in_thread orqali ishchi oqimda chaqiriladi."""
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute("SELECT items FROM sales").fetchall()
            finally:
                conn.close()
        rank: Dict[int, float] = {}
        for r in rows:
            try:
                items = json.loads(r["items"]) if r["items"] else []
            except Exception:
                items = []
            for it in items:
                pid = it.get("product_id")
                if pid:
                    rank[pid] = rank.get(pid, 0) + (it.get("qty", 0) or 0)
        return rank

    async def top_selling_products(self, limit: int = None,
                                   available_only: bool = True) -> List[dict]:
        """Mahsulotlarni SOTUV REYTINGI bo'yicha tartiblab qaytaradi —
        eng ko'p sotilgani birinchi, sotilmaganlar oxirida (nom bo'yicha).

        • limit=None → barcha mahsulotlar (sahifalash uchun)
        • limit=6    → faqat top-6
        • available_only=True → faqat qoldig'i bor (qty>0) mahsulotlar

        Shu metod yordamida mahsulot ro'yxatining 1-sahifasi avtomatik
        "6 ta eng ko'p sotiladigan" bo'ladi."""
        rank = await self._in_thread(self._sales_rank_sync)
        prods = await self.get_all_products(active_only=True)
        if available_only:
            prods = [p for p in prods if (p.get("qty", 0) or 0) > 0]
        # Ko'p sotilgani oldinda; teng bo'lsa nom bo'yicha
        prods.sort(key=lambda p: (-rank.get(p["id"], 0), (p.get("name") or "").lower()))
        return prods[:limit] if limit else prods

    async def search_products(self, query: str, limit: int = 20) -> List[dict]:
        q = (query or "").strip()
        if not q:
            return []
        with self._lock:
            conn = self._conn()
            try:
                # 1) Aniq shtrix-kod bo'yicha (barcode bo'sh emas va aniq mos kelsa)
                rows = conn.execute(
                    "SELECT * FROM products WHERE is_active=1 AND barcode=? AND barcode!=''",
                    (q,)
                ).fetchall()
                if rows:
                    return [self._row_to_product(r) for r in rows]
                # 2) Raqam bo'lsa — ID yoki shtrix-kodning bir qismi
                if q.isdigit():
                    rows = conn.execute(
                        "SELECT * FROM products WHERE id=? AND is_active=1",
                        (int(q),)
                    ).fetchall()
                    if rows:
                        return [self._row_to_product(r) for r in rows]
                    # Barcode'ning qismi bo'yicha qidirish (oxiri yoki ichida)
                    rows = conn.execute(
                        "SELECT * FROM products WHERE is_active=1 AND barcode LIKE ? "
                        "ORDER BY name LIMIT ?",
                        (f"%{q}%", limit)
                    ).fetchall()
                    if rows:
                        return [self._row_to_product(r) for r in rows]
                # 3) Nom bo'yicha
                rows = conn.execute(
                    "SELECT * FROM products WHERE is_active=1 AND name LIKE ? "
                    "ORDER BY name LIMIT ?",
                    (f"%{q}%", limit)
                ).fetchall()
                return [self._row_to_product(r) for r in rows]
            finally:
                conn.close()

    async def get_product_by_barcode(self, barcode: str) -> Optional[dict]:
        """Shtrix-kod bo'yicha mahsulotni qaytaradi (faqat is_active=1)."""
        bc = (barcode or "").strip()
        if not bc:
            return None
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT * FROM products WHERE barcode=? AND is_active=1 LIMIT 1",
                    (bc,)
                ).fetchone()
                return self._row_to_product(r) if r else None
            finally:
                conn.close()

    # products jadvalida tahrirlash mumkin bo'lgan ustunlar (oq ro'yxat).
    # Bu ro'yxatda yo'q kalitlar e'tiborsiz qoldiriladi — noto'g'ri nom
    # kelsa SQL crash bo'lmaydi (masalan eski 'sell' kabi xato kalitlar).
    _EDITABLE_COLUMNS = {
        "name", "description",
        "sell_price", "wholesale_price", "cost_price",
        "sell_price_usd", "wholesale_price_usd", "cost_price_usd",
        "qty", "unit", "image_file_id", "image_url", "barcode", "is_active",
        "category_id", "supplier_id", "channel_msg_id",
    }

    async def update_product(self, pid: int, **kw):
        if not kw:
            return
        # Faqat ruxsat etilgan ustunlarni qoldiramiz.
        bad = [k for k in kw if k not in self._EDITABLE_COLUMNS]
        if bad:
            # Noto'g'ri ustun(lar) — jim e'tiborsiz qoldiramiz, lekin log qilamiz.
            print(f"[update_product] e'tiborsiz qoldirilgan noma'lum ustun(lar): {bad}")
            kw = {k: v for k, v in kw.items() if k in self._EDITABLE_COLUMNS}
        if not kw:
            return
        # USD narxlari yangilanganda — so'm kesh ham qayta hisoblanadi.
        rate = self.get_usd_rate()
        usd_to_sum_map = {
            "sell_price_usd":      "sell_price",
            "wholesale_price_usd": "wholesale_price",
            "cost_price_usd":      "cost_price",
        }
        for usd_col, sum_col in usd_to_sum_map.items():
            if usd_col in kw and sum_col not in kw:
                kw[sum_col] = round(usd_to_sum(kw[usd_col], rate), 2)
        cols = ",".join(f"{k}=?" for k in kw.keys())
        vals = list(kw.values()) + [pid]
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(f"UPDATE products SET {cols} WHERE id=?", vals)
                conn.commit()
            finally:
                conn.close()
        p = await self.get_product_any(pid)
        if p:
            await self._refresh_product(p)

    async def reprice_all_with_rate(self, rate: float = None):
        """Barcha mahsulot narxlarini va mijoz qarzlarining so'mdagi keshini
        joriy USD kurs bo'yicha qayta hisoblaydi. USD qiymatlari — kanonik."""
        if rate is None:
            rate = self.get_usd_rate()
        if rate <= 0:
            return
        with self._lock:
            conn = self._conn()
            try:
                conn.execute("""
                    UPDATE products
                       SET sell_price = ROUND(COALESCE(sell_price_usd,0) * ?, 2),
                           wholesale_price = ROUND(COALESCE(wholesale_price_usd,0) * ?, 2),
                           cost_price = ROUND(COALESCE(cost_price_usd,0) * ?, 2)
                     WHERE COALESCE(sell_price_usd,0) > 0
                """, (rate, rate, rate))
                conn.execute("""
                    UPDATE clients
                       SET debt = ROUND(COALESCE(debt_usd,0) * ?, 2)
                     WHERE COALESCE(debt_usd,0) > 0
                """, (rate,))
                conn.commit()
            finally:
                conn.close()

    async def change_qty(self, pid: int, delta: float):
        new_qty = 0
        prev_qty = 0
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT qty FROM products WHERE id=?", (pid,)
                ).fetchone()
                if not r:
                    return
                prev_qty = r["qty"]
                new_qty = max(0.0, prev_qty + delta)
                conn.execute("UPDATE products SET qty=? WHERE id=?", (new_qty, pid))
                conn.commit()
            finally:
                conn.close()
        p = await self.get_product_any(pid)
        if p:
            await self._refresh_product(p)
            # Past qoldiq ogohlantirish (faqat kamayganda yangi tushganda)
            if (delta < 0 and prev_qty > LOW_STOCK_THRESHOLD
                    and new_qty <= LOW_STOCK_THRESHOLD
                    and self._low_stock_alert):
                try:
                    await self._low_stock_alert(p)
                except Exception as e:
                    print(f"[Past qoldiq xato] {e}")

    async def deactivate_product(self, pid: int):
        with self._lock:
            conn = self._conn()
            try:
                conn.execute("UPDATE products SET is_active=0 WHERE id=?", (pid,))
                conn.commit()
            finally:
                conn.close()
        p = await self.get_product_any(pid)
        if p:
            await self._refresh_product(p)

    async def _refresh_product(self, p: dict):
        # Kanalda faqat rasmli mahsulot postlari mavjud bo'ladi.
        # Shuning uchun caption'ni faqat image_file_id bor postlarda yangilaymiz.
        mid = p.get("channel_msg_id", 0)
        if not mid or not p.get("image_file_id"):
            return
        cap = _fmt_product(p)
        await self._edit_cap(mid, cap, product_id=p.get("id", 0))

