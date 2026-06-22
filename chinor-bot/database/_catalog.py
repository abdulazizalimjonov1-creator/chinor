"""Kategoriyalar va yetkazib beruvchilar — CRUD va bog'liq so'rovlar.

Bu `CatalogMixin` — `ChannelDB` undan meros oladi.

  • categories — mahsulot kategoriyalari (products.category_id orqali bog'lanadi)
  • suppliers  — yetkazib beruvchilar (products.supplier_id orqali bog'lanadi)

Kategoriya/yetkazib beruvchi o'chirilsa — unga biriktirilgan mahsulotlar
o'chirilmaydi, faqat bog'lanish (category_id / supplier_id) 0 ga qaytadi.
"""

from typing import Optional, List

from bot.config import LOW_STOCK_THRESHOLD
from database._helpers import _now


class CatalogMixin:
    # ─── Kategoriyalar ───────────────────────────────────────────────────────
    async def add_category(self, name: str) -> int:
        name = (name or "").strip()
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    "INSERT INTO categories(name, created_at) VALUES(?,?)",
                    (name, _now())
                )
                conn.commit()
                return cur.lastrowid
            finally:
                conn.close()

    async def get_all_categories(self) -> List[dict]:
        """Barcha kategoriyalar — har biriga `product_count` qo'shilgan holda."""
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT c.*, "
                    "(SELECT COUNT(*) FROM products p "
                    "  WHERE p.category_id=c.id AND p.is_active=1) AS product_count "
                    "FROM categories c ORDER BY c.name"
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    async def get_category(self, cid: int) -> Optional[dict]:
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT * FROM categories WHERE id=?", (cid,)
                ).fetchone()
                return dict(r) if r else None
            finally:
                conn.close()

    async def category_exists(self, name: str) -> bool:
        name = (name or "").strip().lower()
        if not name:
            return False
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT 1 FROM categories WHERE LOWER(name)=?", (name,)
                ).fetchone()
                return bool(r)
            finally:
                conn.close()

    async def update_category(self, cid: int, name: str):
        name = (name or "").strip()
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE categories SET name=? WHERE id=?", (name, cid)
                )
                conn.commit()
            finally:
                conn.close()

    async def delete_category(self, cid: int):
        """Kategoriyani o'chiradi — mahsulotlar saqlanadi, faqat category_id=0."""
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE products SET category_id=0 WHERE category_id=?", (cid,)
                )
                conn.execute("DELETE FROM categories WHERE id=?", (cid,))
                conn.commit()
            finally:
                conn.close()

    async def get_products_by_category(self, cid: int,
                                       active_only: bool = True) -> List[dict]:
        with self._lock:
            conn = self._conn()
            try:
                sql = "SELECT * FROM products WHERE category_id=?"
                if active_only:
                    sql += " AND is_active=1"
                sql += " ORDER BY name"
                rows = conn.execute(sql, (cid,)).fetchall()
                return [self._row_to_product(r) for r in rows]
            finally:
                conn.close()

    # ─── Yetkazib beruvchilar ────────────────────────────────────────────────
    async def add_supplier(self, name: str, phone: str = "",
                           note: str = "") -> int:
        name = (name or "").strip()
        phone = (phone or "").strip()
        note = (note or "").strip()
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    "INSERT INTO suppliers(name, phone, note, created_at) "
                    "VALUES(?,?,?,?)",
                    (name, phone, note, _now())
                )
                conn.commit()
                return cur.lastrowid
            finally:
                conn.close()

    async def get_all_suppliers(self, low_threshold: float = None) -> List[dict]:
        """Barcha yetkazib beruvchilar — har biriga `product_count` va
        `low_count` (qoldig'i past mahsulotlar soni) qo'shilgan holda."""
        if low_threshold is None:
            low_threshold = LOW_STOCK_THRESHOLD
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT s.*, "
                    "(SELECT COUNT(*) FROM products p "
                    "  WHERE p.supplier_id=s.id AND p.is_active=1) AS product_count, "
                    "(SELECT COUNT(*) FROM products p "
                    "  WHERE p.supplier_id=s.id AND p.is_active=1 AND p.qty<=?) "
                    "  AS low_count "
                    "FROM suppliers s ORDER BY s.name",
                    (low_threshold,)
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    async def get_supplier(self, sid: int) -> Optional[dict]:
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT * FROM suppliers WHERE id=?", (sid,)
                ).fetchone()
                return dict(r) if r else None
            finally:
                conn.close()

    async def update_supplier(self, sid: int, **kw):
        allowed = {"name", "phone", "note"}
        kw = {k: (v or "").strip() if isinstance(v, str) else v
              for k, v in kw.items() if k in allowed}
        if not kw:
            return
        cols = ",".join(f"{k}=?" for k in kw)
        vals = list(kw.values()) + [sid]
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(f"UPDATE suppliers SET {cols} WHERE id=?", vals)
                conn.commit()
            finally:
                conn.close()

    async def delete_supplier(self, sid: int):
        """Yetkazib beruvchini o'chiradi — mahsulotlar saqlanadi, supplier_id=0."""
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE products SET supplier_id=0 WHERE supplier_id=?", (sid,)
                )
                conn.execute("DELETE FROM suppliers WHERE id=?", (sid,))
                conn.commit()
            finally:
                conn.close()

    async def get_products_by_supplier(self, sid: int,
                                       active_only: bool = True) -> List[dict]:
        """Yetkazib beruvchining mahsulotlari — qoldig'i kam birinchi."""
        with self._lock:
            conn = self._conn()
            try:
                sql = "SELECT * FROM products WHERE supplier_id=?"
                if active_only:
                    sql += " AND is_active=1"
                sql += " ORDER BY qty ASC, name"
                rows = conn.execute(sql, (sid,)).fetchall()
                return [self._row_to_product(r) for r in rows]
            finally:
                conn.close()

    async def get_low_stock_by_supplier(self, sid: int,
                                        threshold: float = None) -> List[dict]:
        """Yetkazib beruvchining qoldig'i past (yoki tugagan) mahsulotlari."""
        if threshold is None:
            threshold = LOW_STOCK_THRESHOLD
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM products WHERE supplier_id=? AND is_active=1 "
                    "AND qty<=? ORDER BY qty ASC, name",
                    (sid, threshold)
                ).fetchall()
                return [self._row_to_product(r) for r in rows]
            finally:
                conn.close()

    # ─── Mahsulotga kategoriya/yetkazib beruvchi biriktirish ────────────────
    async def set_product_category(self, pid: int, cid: int):
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE products SET category_id=? WHERE id=?", (cid or 0, pid)
                )
                conn.commit()
            finally:
                conn.close()

    async def set_product_supplier(self, pid: int, sid: int):
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE products SET supplier_id=? WHERE id=?", (sid or 0, pid)
                )
                conn.commit()
            finally:
                conn.close()
