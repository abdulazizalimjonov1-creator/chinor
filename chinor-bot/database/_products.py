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


def _norm_codes(barcodes, barcode="") -> List[str]:
    """Shtrix-kodlar ro'yxatini normallashtiradi: strip + bo'shlarni tashlash +
    tartibni saqlagan holda dublikatlarni olib tashlash. `barcode` (eski,
    yakka qiymat) ro'yxat berilmaganda ishlatiladi."""
    raw = barcodes if isinstance(barcodes, (list, tuple)) else None
    if raw is None:
        raw = [barcode] if barcode else []
    out: List[str] = []
    for c in raw:
        c = str(c or "").strip()
        if c and c not in out:
            out.append(c)
    return out


class ProductsMixin:
    # ── SKU (mahsulot ID) ────────────────────────────────────────────────────
    @staticmethod
    def _next_sku_sync(conn) -> int:
        """Keyingi SKU — 5 xonali (10000–99999) oraliqda ketma-ket.
        AUTOINCREMENT'ga ishonmaymiz: importdan qolgan katta (masalan 33 mln)
        idlar sequence'ni buzib, yangi tovarlarga sakragan raqam berardi.
        5 xonali oraliq to'lib qolsa — umumiy MAX(id)+1 ga tushamiz."""
        row = conn.execute(
            "SELECT COALESCE(MAX(id), 9999) FROM products WHERE id <= 99999"
        ).fetchone()
        nid = max(9999, int(row[0] or 9999)) + 1
        if nid > 99999:
            row = conn.execute(
                "SELECT COALESCE(MAX(id), 9999) FROM products"
            ).fetchone()
            nid = int(row[0] or 9999) + 1
        return nid

    def next_product_sku(self) -> int:
        """Keyingi beriladigan SKU (admin UI oldindan ko'rsatishi uchun)."""
        with self._lock:
            conn = self._conn()
            try:
                return self._next_sku_sync(conn)
            finally:
                conn.close()

    # ── Shtrix-kodlar (bitta tovarga bir nechta kod) ─────────────────────────
    @staticmethod
    def _attach_barcodes_sync(conn, prods: List[dict]) -> List[dict]:
        """Har bir mahsulot dict'iga `barcodes` ro'yxatini qo'shadi.
        products.barcode (asosiy/birinchi kod) ro'yxatda doim birinchi turadi."""
        if not prods:
            return prods
        m: Dict[int, List[str]] = {}
        for r in conn.execute(
            "SELECT product_id, code FROM product_barcodes ORDER BY rowid"
        ).fetchall():
            m.setdefault(r["product_id"], []).append(r["code"])
        for p in prods:
            codes = list(m.get(p.get("id"), []))
            bc = (p.get("barcode") or "").strip()
            if bc:
                if bc in codes:
                    codes.remove(bc)
                codes.insert(0, bc)
            p["barcodes"] = codes
        return prods

    async def barcode_owner(self, code: str) -> Optional[dict]:
        """Shu shtrix-kod qaysi tovarga tegishli (is_active'dan qat'i nazar) —
        to'qnashuv tekshiruvi uchun. Topilmasa None."""
        c = (code or "").strip()
        if not c:
            return None
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT * FROM products WHERE id IN ("
                    " SELECT product_id FROM product_barcodes WHERE code=?)"
                    " OR (barcode=? AND barcode!='') LIMIT 1",
                    (c, c)
                ).fetchone()
                return self._row_to_product(r) if r else None
            finally:
                conn.close()

    async def get_product_barcodes(self, pid: int) -> List[str]:
        with self._lock:
            conn = self._conn()
            try:
                p = conn.execute(
                    "SELECT id, barcode FROM products WHERE id=?", (pid,)
                ).fetchone()
                if not p:
                    return []
                d = dict(p)
                self._attach_barcodes_sync(conn, [d])
                return d["barcodes"]
            finally:
                conn.close()

    async def set_product_barcodes(self, pid: int, codes: List[str]) -> Tuple[bool, str]:
        """Tovarning BARCHA shtrix-kodlarini berilgan ro'yxatga almashtiradi.
        Birinchi kod products.barcode (asosiy) bo'lib yoziladi.
        Kod boshqa tovarniki bo'lsa — (False, xato_matni) qaytadi."""
        codes = _norm_codes(codes)
        with self._lock:
            conn = self._conn()
            try:
                for c in codes:
                    r = conn.execute(
                        "SELECT p.id, p.name FROM products p WHERE p.id IN ("
                        " SELECT product_id FROM product_barcodes WHERE code=?)"
                        " OR (p.barcode=? AND p.barcode!='')",
                        (c, c)
                    ).fetchone()
                    if r and int(r["id"]) != int(pid):
                        return False, f"«{c}» shtrix-kod «{r['name']}» da bor"
                conn.execute(
                    "DELETE FROM product_barcodes WHERE product_id=?", (pid,)
                )
                now = _now()
                for c in codes:
                    conn.execute(
                        "INSERT OR REPLACE INTO product_barcodes"
                        "(code, product_id, created_at) VALUES(?,?,?)",
                        (c, pid, now)
                    )
                conn.execute(
                    "UPDATE products SET barcode=? WHERE id=?",
                    (codes[0] if codes else "", pid)
                )
                conn.commit()
                return True, ""
            finally:
                conn.close()

    # ── Mahsulot ─────────────────────────────────────────────────────────────
    async def add_product(self, name: str, description: str,
                          sell_price: float, cost_price: float,
                          qty: float, image_file_id: str = "",
                          unit: str = "dona",
                          wholesale_price: float = 0,
                          barcode: str = "",
                          barcodes: Optional[List[str]] = None,
                          category_id: int = 0,
                          supplier_id: int = 0) -> int:
        """Mahsulot qo'shish — narxlar SO'M da (kanonik, butun so'mga
        yaxlitlanadi). USD ustunlari faqat ko'rsatkich keshi — joriy kurs
        bo'yicha hisoblanadi, so'm narxni hech qachon o'zgartirmaydi.
        barcodes — ixtiyoriy shtrix-kodlar ro'yxati (bir tovarga bir nechta);
        barcode (yakka) eski chaqiriqlar mosligi uchun qoldirilgan.
        category_id / supplier_id — ixtiyoriy bog'lanishlar (0 — biriktirilmagan)."""
        created = _now()
        rate = self.get_usd_rate()
        sell_sum = round(float(sell_price or 0))
        cost_sum = round(float(cost_price or 0))
        whs_sum = round(float(wholesale_price or 0))
        sell_usd = round(sum_to_usd(sell_sum, rate), 4)
        cost_usd = round(sum_to_usd(cost_sum, rate), 4)
        whs_usd = round(sum_to_usd(whs_sum, rate), 4) if whs_sum else 0
        codes = _norm_codes(barcodes, barcode)
        primary_bc = codes[0] if codes else ""
        category_id = int(category_id or 0)
        supplier_id = int(supplier_id or 0)
        with self._lock:
            conn = self._conn()
            try:
                pid = self._next_sku_sync(conn)
                conn.execute(
                    "INSERT INTO products(id, name, description, sell_price, "
                    "wholesale_price, cost_price, "
                    "sell_price_usd, wholesale_price_usd, cost_price_usd, "
                    "qty, unit, image_file_id, barcode, "
                    "category_id, supplier_id, is_active, created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)",
                    (pid, name, description, sell_sum, whs_sum, cost_sum,
                     sell_usd, whs_usd, cost_usd,
                     qty, unit, image_file_id, primary_bc,
                     category_id, supplier_id, created)
                )
                for c in codes:
                    conn.execute(
                        "INSERT OR REPLACE INTO product_barcodes"
                        "(code, product_id, created_at) VALUES(?,?,?)",
                        (c, pid, created)
                    )
                conn.commit()
            finally:
                conn.close()

        data = {
            "id": pid, "name": name, "description": description,
            "sell_price": sell_sum, "wholesale_price": whs_sum,
            "cost_price": cost_sum,
            "sell_price_usd": sell_usd,
            "wholesale_price_usd": whs_usd,
            "cost_price_usd": cost_usd,
            "qty": qty, "unit": unit, "image_file_id": image_file_id,
            "barcode": primary_bc, "barcodes": codes,
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
                if not r:
                    return None
                p = self._row_to_product(r)
                self._attach_barcodes_sync(conn, [p])
                return p
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
                if not r:
                    return None
                p = self._row_to_product(r)
                self._attach_barcodes_sync(conn, [p])
                return p
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
                def _out(rows):
                    prods = [self._row_to_product(r) for r in rows]
                    return self._attach_barcodes_sync(conn, prods)

                # 1) Aniq shtrix-kod bo'yicha (asosiy kod YOKI qo'shimcha
                #    kodlar jadvalidagi istalgan kod aniq mos kelsa)
                rows = conn.execute(
                    "SELECT * FROM products WHERE is_active=1 AND ("
                    " (barcode=? AND barcode!='') OR id IN ("
                    "  SELECT product_id FROM product_barcodes WHERE code=?))",
                    (q, q)
                ).fetchall()
                if rows:
                    return _out(rows)
                # 2) Raqam bo'lsa — ID yoki shtrix-kodning bir qismi
                if q.isdigit():
                    rows = conn.execute(
                        "SELECT * FROM products WHERE id=? AND is_active=1",
                        (int(q),)
                    ).fetchall()
                    if rows:
                        return _out(rows)
                    # Barcode'ning qismi bo'yicha qidirish (oxiri yoki ichida)
                    rows = conn.execute(
                        "SELECT * FROM products WHERE is_active=1 AND ("
                        " barcode LIKE ? OR id IN ("
                        "  SELECT product_id FROM product_barcodes WHERE code LIKE ?)) "
                        "ORDER BY name LIMIT ?",
                        (f"%{q}%", f"%{q}%", limit)
                    ).fetchall()
                    if rows:
                        return _out(rows)
                # 3) Nom bo'yicha — to'g'ridan-to'g'ri
                # Ikkala nom bo'yicha ham: asl `name` (xodimlar taniydigan)
                # va `display_name` (mijozga ko'rinadigan chiroyli nom).
                rows = conn.execute(
                    "SELECT * FROM products WHERE is_active=1 "
                    "AND (name LIKE ? OR display_name LIKE ?) "
                    "ORDER BY name LIMIT ?",
                    (f"%{q}%", f"%{q}%", limit)
                ).fetchall()
                if rows:
                    return _out(rows)

                # 4) Normalizatsiya bilan — apostrof, q/k, x/h, ch/sh farqi
                #    e'tiborga olinmaydi ("cho'tka" ↔ "chotka").
                from database._helpers import norm_search, skeleton_search
                nq = norm_search(q)
                if nq:
                    rows = conn.execute(
                        "SELECT * FROM products WHERE is_active=1 "
                        "AND (nrm(name) LIKE ? OR nrm(display_name) LIKE ?) "
                        "ORDER BY name LIMIT ?",
                        (f"%{nq}%", f"%{nq}%", limit)
                    ).fetchall()
                    if rows:
                        return _out(rows)

                # 5) ZAXIRA: unlilarsiz "skelet". Imlo farqi asosan unlilarda
                #    ("samorez" ↔ "samarez"), shuning uchun bu ko'p holatni
                #    qutqaradi. Faqat oldingilari bo'sh qolgandagina —
                #    aks holda ortiqcha natija chiqarardi.
                sq = skeleton_search(q)
                if len(sq) >= 3:
                    rows = conn.execute(
                        "SELECT * FROM products WHERE is_active=1 "
                        "AND (skl(name) LIKE ? OR skl(display_name) LIKE ?) "
                        "ORDER BY name LIMIT ?",
                        (f"%{sq}%", f"%{sq}%", limit)
                    ).fetchall()
                    return _out(rows)
                return []
            finally:
                conn.close()

    async def get_product_by_barcode(self, barcode: str) -> Optional[dict]:
        """Shtrix-kod bo'yicha mahsulotni qaytaradi (faqat is_active=1).
        Asosiy kod ham, qo'shimcha kodlar jadvalidagi kodlar ham tekshiriladi."""
        bc = (barcode or "").strip()
        if not bc:
            return None
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT * FROM products WHERE is_active=1 AND ("
                    " (barcode=? AND barcode!='') OR id IN ("
                    "  SELECT product_id FROM product_barcodes WHERE code=?)) LIMIT 1",
                    (bc, bc)
                ).fetchone()
                if not r:
                    return None
                p = self._row_to_product(r)
                self._attach_barcodes_sync(conn, [p])
                return p
            finally:
                conn.close()

    # products jadvalida tahrirlash mumkin bo'lgan ustunlar (oq ro'yxat).
    # Bu ro'yxatda yo'q kalitlar e'tiborsiz qoldiriladi — noto'g'ri nom
    # kelsa SQL crash bo'lmaydi (masalan eski 'sell' kabi xato kalitlar).
    _EDITABLE_COLUMNS = {
        "name", "display_name", "description",
        "sell_price", "wholesale_price", "cost_price",
        "sell_price_usd", "wholesale_price_usd", "cost_price_usd",
        "qty", "unit", "image_file_id", "image_url", "image_orig_url",
        "usage_tags", "usage_info", "usage_comment", "barcode", "is_active",
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
        # SO'M — kanonik. USD faqat kirishda bir marta so'mga aylantiriladi
        # (masalan bot oqimida $ kiritilsa); so'm kelganda USD kesh yangilanadi.
        rate = self.get_usd_rate()
        price_pairs = (
            ("sell_price",      "sell_price_usd"),
            ("wholesale_price", "wholesale_price_usd"),
            ("cost_price",      "cost_price_usd"),
        )
        for sum_col, usd_col in price_pairs:
            if usd_col in kw and sum_col not in kw:
                # $ kiritildi — kirish paytida BIR MARTA so'mga aylantiramiz
                kw[sum_col] = round(usd_to_sum(kw[usd_col], rate))
            if sum_col in kw:
                kw[sum_col] = round(float(kw[sum_col] or 0))
                kw[usd_col] = round(sum_to_usd(kw[sum_col], rate), 4)
        if "barcode" in kw:
            kw["barcode"] = (kw["barcode"] or "").strip()
        cols = ",".join(f"{k}=?" for k in kw.keys())
        vals = list(kw.values()) + [pid]
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(f"UPDATE products SET {cols} WHERE id=?", vals)
                # Yakka `barcode` yangilanganda qo'shimcha kodlar jadvali ham
                # sinxron bo'lsin: bo'sh → tovarning hamma kodlari o'chadi;
                # qiymat → ro'yxatga qo'shiladi (boshqa kodlari saqlanadi).
                if "barcode" in kw:
                    bc = kw["barcode"]
                    if bc:
                        conn.execute(
                            "INSERT OR REPLACE INTO product_barcodes"
                            "(code, product_id, created_at) VALUES(?,?,?)",
                            (bc, pid, _now())
                        )
                    else:
                        conn.execute(
                            "DELETE FROM product_barcodes WHERE product_id=?",
                            (pid,)
                        )
                conn.commit()
            finally:
                conn.close()
        p = await self.get_product_any(pid)
        if p:
            await self._refresh_product(p)

    async def reprice_all_with_rate(self, rate: float = None):
        """SO'M — kanonik: kurs o'zgarganda so'mdagi narx va qarzlar
        O'ZGARMAYDI. Bu metod endi faqat USD KO'RSATKICH keshlarini yangi
        kursda yangilaydi ($ ko'rinishlar va prixodda $ kiritish uchun).
        Ilgari teskari edi (USD kanonik) — bu 16 000 so'mlik narxni kurs
        aylanishida 15 999/16 001 qilib yuborardi."""
        if rate is None:
            rate = self.get_usd_rate()
        if rate <= 0:
            return
        with self._lock:
            conn = self._conn()
            try:
                conn.execute("""
                    UPDATE products
                       SET sell_price_usd = ROUND(COALESCE(sell_price,0) / ?, 4),
                           wholesale_price_usd = ROUND(COALESCE(wholesale_price,0) / ?, 4),
                           cost_price_usd = ROUND(COALESCE(cost_price,0) / ?, 4)
                """, (rate, rate, rate))
                conn.execute("""
                    UPDATE clients
                       SET debt_usd = ROUND(COALESCE(debt,0) / ?, 4)
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

