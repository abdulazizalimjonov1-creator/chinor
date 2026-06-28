"""Kassa sotuvlari."""

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
    _fmt_client_receipt, _fmt_cash_movement,
)


class SalesMixin:
    # ── Kassa terminallari (offline) ──────────────────────────────────────────
    async def assign_kassa_no(self, device_id: str, name: str = "") -> int:
        """Qurilmaga (device_id) DOIMIY noyob kassa raqamini biriktiradi.
        Tanish qurilma — o'sha raqamni qaytaradi; yangi qurilma — mavjud
        eng katta raqam + 1 (birinchisi 1). Shu raqam chek prefiksi bo'ladi
        ("<kassa_no>-<seq>"), shunda terminallar raqamlari to'qnashmaydi.
        device_id bo'sh bo'lsa 0 qaytaradi (kassa eski default 1 da qoladi)."""
        device_id = (device_id or "").strip()[:64]
        if not device_id:
            return 0
        name = (name or "").strip()[:80]
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT kassa_no FROM kassa_devices WHERE device_id=?",
                    (device_id,)
                ).fetchone()
                if row:
                    conn.execute(
                        "UPDATE kassa_devices SET last_seen=?, "
                        "name=CASE WHEN ?<>'' THEN ? ELSE name END WHERE device_id=?",
                        (_now(), name, name, device_id)
                    )
                    conn.commit()
                    return int(row[0])
                mx = conn.execute(
                    "SELECT MAX(kassa_no) FROM kassa_devices"
                ).fetchone()[0]
                next_no = (int(mx) if mx else 0) + 1
                conn.execute(
                    "INSERT INTO kassa_devices"
                    "(device_id, kassa_no, name, created_at, last_seen) "
                    "VALUES(?,?,?,?,?)",
                    (device_id, next_no, name, _now(), _now())
                )
                conn.commit()
                return next_no
            finally:
                conn.close()

    # ── Sotuv (kassa) ────────────────────────────────────────────────────────
    async def create_sale(self, cashier_id: int, cashier_name: str,
                          items: list,
                          paid_cash: float = 0, paid_cash_usd: float = 0,
                          paid_card: float = 0, paid_card_usd: float = 0,
                          paid_other: float = 0, paid_other_usd: float = 0,
                          paid_currency: str = "sum",
                          client_id: int = 0, is_nasiya: bool = False,
                          override_total_usd: float = 0,
                          override_total_sum: float = 0,
                          created_at: str = "",
                          source: str = "",
                          receipt_no: str = "",
                          is_internal: bool = False) -> dict:
        """Sotuv yaratadi.
        items — dictlar ro'yxati: {product_id, qty, price (USD)}.
        override_total_* — sotuvchining 'yumaloqlangan' jami summasi (chegirma).
                          Faqat birini bersangiz, ikkinchisi joriy kurs bo'yicha hisoblanadi.
        paid_*_usd — agar to'lov USDda qabul qilingan bo'lsa, USD qiymati bilan birga keladi.
        paid_currency — 'sum' yoki 'usd' (sotuv qaysi valyutada amalga oshganini belgilaydi).
        created_at — offline kassadan kelgan sotuvlar uchun asl vaqtni saqlash
                     (bo'sh bo'lsa hozirgi vaqt olinadi).
        is_internal — «Chinor» (do'konning o'zi) uchun ichki rasxod: tovar
                      TANNARX narxida chiqadi, to'lov/qarz yozilmaydi, foyda/
                      daromadga kirmaydi (faqat ichki hisob-kitobda rasxod)."""
        created = (created_at or "").strip() or _now()
        if is_internal:
            # Ichki rasxod — chegirma, nasiya va to'lovlar bo'lmaydi
            is_nasiya = False
        rate = self.get_usd_rate()
        sale_items = []
        subtotal_usd = 0.0
        subtotal_sum = 0.0

        for it in items:
            p = await self.get_product_any(it["product_id"])
            name = p["name"] if p else "?"
            unit = p.get("unit", "dona") if p else "dona"
            cost_usd = float(p.get("cost_price_usd", 0)) if p else 0.0
            cost_sum = float(p.get("cost_price", 0)) if p else 0.0
            if is_internal:
                # «Chinor» rasxodi — tovar tannarx narxida hisobdan chiqadi
                price_usd = cost_usd if cost_usd > 0 else round(sum_to_usd(cost_sum, rate), 4)
                price_sum = cost_sum if cost_sum > 0 else round(usd_to_sum(cost_usd, rate), 2)
            else:
                price_usd = float(it["price"])
                price_sum = round(usd_to_sum(price_usd, rate), 2)
            line_usd = price_usd * it["qty"]
            line_sum = round(price_sum * it["qty"], 2)
            subtotal_usd += line_usd
            subtotal_sum += line_sum
            sale_items.append({
                "product_id": it["product_id"],
                "name": name,
                "qty": it["qty"],
                "unit": unit,
                "price": price_sum,
                "total": line_sum,
                "cost_price": cost_sum,
                "price_usd": price_usd,
                "total_usd": line_usd,
                "cost_price_usd": cost_usd,
            })
            if p:
                await self.change_qty(it["product_id"], -it["qty"])

        # Chegirma + jami (override) hisobi
        if is_internal:
            # Ichki rasxod — chegirma yo'q, jami = tannarx bo'yicha subtotal
            total_usd = subtotal_usd
            total_sum = subtotal_sum
        else:
            if override_total_usd and not override_total_sum:
                override_total_sum = round(usd_to_sum(override_total_usd, rate), 2)
            elif override_total_sum and not override_total_usd:
                override_total_usd = round(sum_to_usd(override_total_sum, rate), 4)

            if override_total_usd > 0 or override_total_sum > 0:
                total_usd = float(override_total_usd)
                total_sum = float(override_total_sum)
            else:
                total_usd = subtotal_usd
                total_sum = subtotal_sum
        discount_usd = max(0.0, subtotal_usd - total_usd)
        discount_sum = max(0.0, subtotal_sum - total_sum)

        # To'lov / qarz hisobi
        if is_internal:
            # «Chinor» rasxodi — to'lov ham, qarz ham yozilmaydi
            paid_total = 0.0
            paid_total_usd = 0.0
            paid_cash = paid_card = paid_other = 0
            paid_cash_usd = paid_card_usd = paid_other_usd = 0
        elif is_nasiya and client_id:
            paid_total = 0.0
            paid_total_usd = 0.0
            paid_cash = paid_card = paid_other = 0
            paid_cash_usd = paid_card_usd = paid_other_usd = 0
            await self.add_debt(client_id, amount_sum=total_sum,
                                amount_usd=total_usd)
        else:
            # Berilmagan USD qiymatlarini joriy kurs bo'yicha to'ldiramiz
            if paid_cash and not paid_cash_usd:
                paid_cash_usd = round(sum_to_usd(paid_cash, rate), 4)
            elif paid_cash_usd and not paid_cash:
                paid_cash = round(usd_to_sum(paid_cash_usd, rate), 2)
            if paid_card and not paid_card_usd:
                paid_card_usd = round(sum_to_usd(paid_card, rate), 4)
            elif paid_card_usd and not paid_card:
                paid_card = round(usd_to_sum(paid_card_usd, rate), 2)
            if paid_other and not paid_other_usd:
                paid_other_usd = round(sum_to_usd(paid_other, rate), 4)
            paid_total = paid_cash + paid_card + paid_other
            paid_total_usd = paid_cash_usd + paid_card_usd + paid_other_usd

        if paid_currency not in ("sum", "usd"):
            paid_currency = "sum"

        if paid_total > 0:
            change = max(0.0, paid_total - total_sum)
            change_usd = max(0.0, paid_total_usd - total_usd)
        else:
            change = 0.0
            change_usd = 0.0

        client_name = ""
        if client_id:
            c = await self.get_client_by_id(client_id)
            if c:
                client_name = c.get("shop_name", "")

        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    "INSERT INTO sales(cashier_id, cashier_name, items, "
                    "total, total_usd, "
                    "subtotal, subtotal_usd, discount, discount_usd, "
                    "usd_rate, paid_cash, paid_cash_usd, "
                    "paid_card, paid_card_usd, paid_other, paid_other_usd, "
                    "paid_total, paid_total_usd, paid_currency, "
                    "change_amount, change_usd, "
                    "is_nasiya, client_id, client_name, created_at, source, "
                    "receipt_no, is_internal) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (cashier_id, cashier_name,
                     json.dumps(sale_items, ensure_ascii=False),
                     total_sum, total_usd,
                     subtotal_sum, subtotal_usd, discount_sum, discount_usd,
                     rate, paid_cash, paid_cash_usd,
                     paid_card, paid_card_usd, paid_other, paid_other_usd,
                     paid_total, paid_total_usd, paid_currency,
                     change, change_usd,
                     1 if is_nasiya else 0, client_id, client_name, created, source,
                     receipt_no, 1 if is_internal else 0)
                )
                sid = cur.lastrowid
                conn.commit()
            finally:
                conn.close()

        data = {
            "id": sid, "cashier_id": cashier_id, "cashier_name": cashier_name,
            "items": sale_items,
            "total": total_sum, "total_usd": total_usd,
            "subtotal": subtotal_sum, "subtotal_usd": subtotal_usd,
            "discount": discount_sum, "discount_usd": discount_usd,
            "usd_rate": rate,
            "paid_cash": paid_cash, "paid_cash_usd": paid_cash_usd,
            "paid_card": paid_card, "paid_card_usd": paid_card_usd,
            "paid_other": paid_other, "paid_other_usd": paid_other_usd,
            "paid_total": paid_total, "paid_total_usd": paid_total_usd,
            "paid_currency": paid_currency,
            "change": change, "change_usd": change_usd,
            "is_nasiya": is_nasiya, "client_id": client_id,
            "client_name": client_name, "created_at": created,
            "source": source, "receipt_no": receipt_no,
            "is_internal": is_internal
        }
        mid = await self._send(_fmt_sale(data))
        if mid:
            with self._lock:
                conn = self._conn()
                try:
                    conn.execute(
                        "UPDATE sales SET channel_msg_id=? WHERE id=?", (mid, sid)
                    )
                    conn.commit()
                finally:
                    conn.close()
        # Mijoz nomiga sotuv bo'lsa — unga Telegram orqali elektron chek yuboramiz.
        # «Chinor» ichki rasxodi bundan mustasno (chek mijozga ketmaydi).
        if client_id and not is_internal:
            try:
                await self._send_client_receipt(client_id, data)
            except Exception:
                pass
        return data

    async def add_cash_movement(self, direction: str, amount: float, *,
                                category: str = "", note: str = "",
                                recipient: str = "", cashier_id: int = 0,
                                cashier_name: str = "", source: str = "",
                                created_at: str = "") -> dict:
        """Naqd kassa harakati (sotuvga bog'liq emas).
        direction='in' — kassaga pul qo'shildi; 'out' — kassadan pul olindi.
        Telegram kanaliga ham log yuboradi."""
        direction = "out" if str(direction).lower() == "out" else "in"
        amount = abs(float(amount or 0))
        created = (created_at or "").strip() or _now()
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    "INSERT INTO cash_movements(direction, amount, category, note, "
                    "recipient, cashier_id, cashier_name, source, created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (direction, amount, category, note, recipient,
                     cashier_id, cashier_name, source, created)
                )
                mid_id = cur.lastrowid
                conn.commit()
            finally:
                conn.close()
        data = {
            "id": mid_id, "direction": direction, "amount": amount,
            "category": category, "note": note, "recipient": recipient,
            "cashier_id": cashier_id, "cashier_name": cashier_name,
            "source": source, "created_at": created,
        }
        mid = await self._send(_fmt_cash_movement(data))
        if mid:
            with self._lock:
                conn = self._conn()
                try:
                    conn.execute(
                        "UPDATE cash_movements SET channel_msg_id=? WHERE id=?",
                        (mid, mid_id)
                    )
                    conn.commit()
                finally:
                    conn.close()
        return data

    async def create_return(self, cashier_id: int, cashier_name: str,
                            items: list, *,
                            payment: str = "cash",
                            method: str = "",
                            client_id: int = 0,
                            created_at: str = "",
                            source: str = "",
                            receipt_no: str = "",
                            orig_receipt_no: str = "") -> dict:
        """Sotuvni (qisman yoki to'liq) qaytaradi (refund).
        items — [{product_id, qty, price (USD)}] — qty MUSBAT (qaytarilgan dona).
        method — pul qaysi usulda qaytarildi: cash|card|click|debt. `debt` bo'lsa
                 pul berilmaydi, mijoz qarzidan ayiriladi (kassadan naqd chiqmaydi).
        Qoldiqni TIKLAYDI (+qty) va sotuvlar jadvaliga MANFIY summali, is_return=1
        yozuv qo'shadi — shunda daromad/foyda hisobotlarida netto (kamayadi)."""
        created = (created_at or "").strip() or _now()
        rate = self.get_usd_rate()

        # Qayta-qaytarishni oldini olish: shu asl chek (orig_receipt_no) bo'yicha
        # har bir mahsulotdan SOTILGAN va AVVAL QAYTARILGAN miqdorni aniqlaymiz —
        # qaytariladigan miqdor "sotilgan − avval qaytarilgan" dan oshmaydi.
        sold_map = {}
        prior_returned = {}
        if orig_receipt_no:
            with self._lock:
                conn = self._conn()
                try:
                    r0 = conn.execute(
                        "SELECT items FROM sales WHERE receipt_no=? "
                        "AND COALESCE(is_return,0)=0 ORDER BY id LIMIT 1",
                        (orig_receipt_no,)
                    ).fetchone()
                    if r0 and r0["items"]:
                        for it0 in json.loads(r0["items"]):
                            k = int(it0.get("product_id") or 0)
                            sold_map[k] = sold_map.get(k, 0.0) + abs(float(it0.get("qty") or 0))
                    rrows = conn.execute(
                        "SELECT items FROM sales WHERE COALESCE(is_return,0)=1 "
                        "AND source LIKE ?",
                        (f"%qaytarish←{orig_receipt_no}%",)
                    ).fetchall()
                    for rr in rrows:
                        for itr in json.loads(rr["items"] or "[]"):
                            k = int(itr.get("product_id") or 0)
                            prior_returned[k] = prior_returned.get(k, 0.0) + abs(float(itr.get("qty") or 0))
                finally:
                    conn.close()

        sale_items = []
        subtotal_usd = 0.0
        subtotal_sum = 0.0
        for it in items:
            pid = it["product_id"]
            qty = abs(float(it.get("qty") or 0))
            # Asl chek topilgan bo'lsa — qolgan miqdordan oshirmaymiz
            pidk = int(pid or 0)
            if orig_receipt_no and pidk in sold_map:
                allowed = sold_map[pidk] - prior_returned.get(pidk, 0.0)
                if qty > allowed:
                    qty = max(0.0, allowed)
            if qty <= 0:
                continue
            p = await self.get_product_any(pid)
            name = p["name"] if p else "?"
            unit = p.get("unit", "dona") if p else "dona"
            cost_usd = float(p.get("cost_price_usd", 0)) if p else 0.0
            cost_sum = float(p.get("cost_price", 0)) if p else 0.0
            price_usd = float(it.get("price") or 0)
            price_sum = round(usd_to_sum(price_usd, rate), 2)
            # Qaytarish — manfiy miqdor/summalar
            line_usd = -price_usd * qty
            line_sum = round(-price_sum * qty, 2)
            subtotal_usd += line_usd
            subtotal_sum += line_sum
            sale_items.append({
                "product_id": pid, "name": name, "qty": -qty, "unit": unit,
                "price": price_sum, "total": line_sum, "cost_price": cost_sum,
                "price_usd": price_usd, "total_usd": line_usd,
                "cost_price_usd": cost_usd,
            })
            if p:
                await self.change_qty(pid, qty)   # qoldiqni tiklaymiz (+)

        total_usd = subtotal_usd
        total_sum = subtotal_sum

        # Pul qaysi usulda qaytarildi (kassadagi naqd aniq chiqishi uchun muhim)
        method = (method or payment or "cash").lower()
        paid_cash = paid_card = paid_other = 0.0
        paid_cash_usd = paid_card_usd = paid_other_usd = 0.0
        paid_total = 0.0
        paid_total_usd = 0.0
        if method == "debt" and client_id:
            # Pul berilmaydi — mijoz qarzidan ayiramiz (manfiy = kamaytirish).
            # Kassadan naqd chiqmaydi, paid_* = 0.
            await self.add_debt(client_id, amount_sum=total_sum, amount_usd=total_usd)
        elif method == "card":
            paid_card, paid_card_usd = total_sum, total_usd
            paid_total, paid_total_usd = total_sum, total_usd
        elif method in ("click", "payme", "other"):
            paid_other, paid_other_usd = total_sum, total_usd
            paid_total, paid_total_usd = total_sum, total_usd
        else:  # cash
            paid_cash, paid_cash_usd = total_sum, total_usd
            paid_total, paid_total_usd = total_sum, total_usd

        client_name = ""
        if client_id:
            c = await self.get_client_by_id(client_id)
            if c:
                client_name = c.get("shop_name", "")

        # Asl chek raqamini source'ga belgilab qo'yamiz (alohida ustunsiz)
        src = source or ""
        if orig_receipt_no:
            tag = f"qaytarish←{orig_receipt_no}"
            src = (src + " · " + tag) if src else tag
        src = src[:80]

        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    "INSERT INTO sales(cashier_id, cashier_name, items, "
                    "total, total_usd, subtotal, subtotal_usd, discount, discount_usd, "
                    "usd_rate, paid_cash, paid_cash_usd, paid_card, paid_card_usd, "
                    "paid_other, paid_other_usd, paid_total, paid_total_usd, paid_currency, "
                    "change_amount, change_usd, is_nasiya, client_id, client_name, "
                    "created_at, source, receipt_no, is_internal, is_return) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (cashier_id, cashier_name,
                     json.dumps(sale_items, ensure_ascii=False),
                     total_sum, total_usd, subtotal_sum, subtotal_usd, 0.0, 0.0,
                     rate, paid_cash, paid_cash_usd, paid_card, paid_card_usd,
                     paid_other, paid_other_usd, paid_total, paid_total_usd, "sum",
                     0.0, 0.0, 0, client_id, client_name,
                     created, src, receipt_no, 0, 1)
                )
                sid = cur.lastrowid
                conn.commit()
            finally:
                conn.close()

        return {
            "id": sid, "cashier_id": cashier_id, "cashier_name": cashier_name,
            "items": sale_items, "total": total_sum, "total_usd": total_usd,
            "subtotal": subtotal_sum, "subtotal_usd": subtotal_usd,
            "discount": 0.0, "discount_usd": 0.0, "usd_rate": rate,
            "paid_total": paid_total, "paid_total_usd": paid_total_usd,
            "paid_currency": "sum", "is_nasiya": False, "client_id": client_id,
            "client_name": client_name, "created_at": created, "source": src,
            "receipt_no": receipt_no, "is_internal": False, "is_return": True,
            "orig_receipt_no": orig_receipt_no,
        }

    async def _send_client_receipt(self, client_id: int, sale: dict) -> None:
        """Sotuv mijoz nomiga bo'lsa — mijozga Telegram orqali elektron chek
        yuboradi. Mijozda Telegram ID bo'lmasa yoki botni bloklasa — jim o'tadi."""
        if not getattr(self, "_bot", None):
            return
        c = await self.get_client_by_id(client_id)
        if not c:
            return
        tg = c.get("telegram_id")
        if not tg:
            return
        try:
            await self._bot.send_message(
                int(tg), _fmt_client_receipt(sale, c), parse_mode="HTML"
            )
        except Exception:
            # mijoz botni bloklagan / chatni boshlamagan bo'lishi mumkin
            pass

    async def get_sale(self, sid: int) -> Optional[dict]:
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute("SELECT * FROM sales WHERE id=?", (sid,)).fetchone()
                return self._row_to_sale(r) if r else None
            finally:
                conn.close()

    def _sales_by_prefix(self, prefix: str) -> List[dict]:
        """created_at boshlanishi `prefix` ga mos sotuvlar (sana yoki oy).
        Sinxron — _in_thread orqali ishchi oqimda chaqiriladi."""
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM sales WHERE created_at LIKE ? ORDER BY id",
                    (f"{prefix}%",)
                ).fetchall()
                return [self._row_to_sale(r) for r in rows]
            finally:
                conn.close()

    async def get_sales_by_date(self, date: str) -> List[dict]:
        return await self._in_thread(self._sales_by_prefix, date)

    async def get_sales_by_month(self, month: str) -> List[dict]:
        return await self._in_thread(self._sales_by_prefix, month)

    async def get_all_sales(self) -> List[dict]:
        # Og'ir o'qish — ishchi oqimda
        return await self._in_thread(self._all_sales)

    def _recent_sales(self, limit: int, since: str = "") -> List[dict]:
        with self._lock:
            conn = self._conn()
            try:
                if since:
                    # Inkremental sinxron: faqat `since` dan keyingi sotuvlar
                    rows = conn.execute(
                        "SELECT * FROM sales WHERE created_at > ? "
                        "ORDER BY id DESC LIMIT ?", (since, limit)
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM sales ORDER BY id DESC LIMIT ?", (limit,)
                    ).fetchall()
                return [self._row_to_sale(r) for r in rows]
            finally:
                conn.close()

    async def get_recent_sales(self, limit: int = 50, since: str = "") -> List[dict]:
        return await self._in_thread(self._recent_sales, limit, since)

    # ── Retention: eski cheklarni asta-sekin o'chirish ───────────────────────
    def _purge_old_sales(self, cutoff: str, batch: int) -> int:
        """`cutoff` (created_at) dan eski sotuvlardan ko'pi bilan `batch` tasini
        o'chiradi. O'chirilgan sonni qaytaradi (0 = boshqa eski yozuv yo'q)."""
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    "DELETE FROM sales WHERE id IN ("
                    "  SELECT id FROM sales WHERE created_at < ? "
                    "  ORDER BY id LIMIT ?)",
                    (cutoff, batch),
                )
                conn.commit()
                return cur.rowcount or 0
            finally:
                conn.close()

    async def purge_old_sales(self, cutoff: str, batch: int = 500) -> int:
        return await self._in_thread(self._purge_old_sales, cutoff, batch)

