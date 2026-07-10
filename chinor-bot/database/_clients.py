"""Mijozlar: qo'shish, qarz, to'lov."""

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


def normalize_phone(phone: str) -> str:
    """Telefon raqamni faqat raqamlardan iborat ko'rinishga keltiradi.
    '+998 (90) 123-45-67' → '998901234567'. Bo'sh bo'lsa ''."""
    return "".join(c for c in (phone or "") if c.isdigit())


def phones_match(a: str, b: str) -> bool:
    """Ikki raqamni solishtiradi (mamlakat kodi tushib qolishi mumkin)."""
    a = normalize_phone(a)
    b = normalize_phone(b)
    if not a or not b:
        return False
    if a == b:
        return True
    # Mamlakat kodi tushib qolishi mumkin — oxirgi 9 raqam mos kelsa kifoya
    if len(a) >= 9 and len(b) >= 9 and a[-9:] == b[-9:]:
        return True
    return False


class ClientsMixin:
    # ── Mijoz ────────────────────────────────────────────────────────────────
    async def add_client(self, tg_id: Optional[int], shop_name: str,
                         phone: str, registered_by: int,
                         client_type: str = "dona") -> bool:
        """Mijoz qo'shish.
        tg_id: Telegram ID — ODATDA None bo'ladi. Mijoz keyin bot orqali
        kontaktini yuborib o'zini avtorizatsiya qiladi (set_client_tg_id).
        phone: telefon raqami — normalizatsiyalanib saqlanadi (faqat raqamlar).
        Bir xil telefon raqamli mijoz allaqachon bor bo'lsa — False qaytaradi."""
        created = _now()
        ctype = "optom" if str(client_type).lower().startswith("opt") else "dona"
        try:
            tg_norm = int(tg_id) if tg_id not in (None, "", 0, "0") else None
        except (ValueError, TypeError):
            tg_norm = None
        phone_norm = normalize_phone(phone)
        with self._lock:
            conn = self._conn()
            try:
                if tg_norm is not None:
                    exists = conn.execute(
                        "SELECT 1 FROM clients WHERE telegram_id=?", (tg_norm,)
                    ).fetchone()
                    if exists:
                        return False
                # Telefon dublikat tekshiruvi
                if phone_norm:
                    for r in conn.execute(
                        "SELECT phone FROM clients WHERE phone IS NOT NULL AND phone != ''"
                    ).fetchall():
                        if phones_match(r["phone"], phone_norm):
                            return False
                conn.execute(
                    "INSERT INTO clients(telegram_id, shop_name, phone, debt, "
                    "client_type, registered_by, created_at) VALUES(?,?,?,0,?,?,?)",
                    (tg_norm, shop_name, phone_norm, ctype, registered_by, created)
                )
                conn.commit()
            finally:
                conn.close()
        return True

    async def get_client_by_phone(self, phone: str) -> Optional[dict]:
        """Telefon raqami bo'yicha mijozni topadi.
        Raqamlarni normallashtirib solishtiradi; mamlakat kodi tushib qolsa ham
        ishlaydi (oxirgi 9 raqam bo'yicha mos kelishi yetarli)."""
        norm = normalize_phone(phone)
        if not norm:
            return None
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM clients WHERE phone IS NOT NULL AND phone != ''"
                ).fetchall()
                for r in rows:
                    if phones_match(r["phone"], norm):
                        return self._row_to_client(r)
                return None
            finally:
                conn.close()

    # ── Mini App credentials ────────────────────────────────────────────────
    async def get_client_by_username(self, username: str) -> Optional[dict]:
        u = (username or "").strip()
        if not u:
            return None
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT * FROM clients WHERE LOWER(username)=LOWER(?)", (u,)
                ).fetchone()
                return self._row_to_client(r) if r else None
            finally:
                conn.close()

    async def set_client_credentials(self, cid: int, username: str,
                                      password_hash: str) -> bool:
        """Mijoz uchun login/parol saqlash. Boshqa mijoz/adminda shu login
        bor bo'lsa False (band)."""
        u = (username or "").strip()
        if not u or not password_hash:
            return False
        with self._lock:
            conn = self._conn()
            try:
                # mijozlarda dublikatmi?
                r = conn.execute(
                    "SELECT id FROM clients WHERE LOWER(username)=LOWER(?) AND id!=?",
                    (u, int(cid))
                ).fetchone()
                if r:
                    return False
                # adminlarda dublikatmi?
                r2 = conn.execute(
                    "SELECT telegram_id FROM admins WHERE LOWER(username)=LOWER(?)",
                    (u,)
                ).fetchone()
                if r2:
                    return False
                conn.execute(
                    "UPDATE clients SET username=?, password_hash=? WHERE id=?",
                    (u, password_hash, int(cid))
                )
                conn.commit()
                return True
            finally:
                conn.close()

    async def clear_client_credentials(self, cid: int) -> None:
        """Mijozning login + parolini butunlay o'chiradi."""
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE clients SET username='', password_hash='' WHERE id=?",
                    (int(cid),)
                )
                conn.commit()
            finally:
                conn.close()

    async def client_has_credentials(self, cid: int) -> bool:
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT username, password_hash FROM clients WHERE id=?",
                    (int(cid),)
                ).fetchone()
                if not r:
                    return False
                return bool(r["username"]) and bool(r["password_hash"])
            finally:
                conn.close()

    async def set_client_tg_id(self, cid: int, tg_id: int) -> bool:
        """Mijozga Telegram ID biriktiradi.
        • Boshqa mijozda bu tg_id bor bo'lsa → False (rad etiladi).
        • Mijozda ID allaqachon bor va boshqa — False (xavfsizlik uchun).
        Aks holda saqlanadi → True."""
        if not tg_id:
            return False
        with self._lock:
            conn = self._conn()
            try:
                # Boshqa mijozda shu ID bormi?
                r = conn.execute(
                    "SELECT id FROM clients WHERE telegram_id=? AND id!=?",
                    (int(tg_id), cid)
                ).fetchone()
                if r:
                    return False
                # Mijozning hozirgi ID si
                cur = conn.execute(
                    "SELECT telegram_id FROM clients WHERE id=?", (cid,)
                ).fetchone()
                if cur and cur["telegram_id"] not in (None, 0) \
                        and int(cur["telegram_id"]) != int(tg_id):
                    return False
                conn.execute(
                    "UPDATE clients SET telegram_id=? WHERE id=?",
                    (int(tg_id), cid)
                )
                conn.commit()
                return True
            finally:
                conn.close()

    async def get_client_by_tg(self, tg_id: int) -> Optional[dict]:
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT * FROM clients WHERE telegram_id=?", (tg_id,)
                ).fetchone()
                return self._row_to_client(r) if r else None
            finally:
                conn.close()

    async def get_client_by_id(self, cid: int) -> Optional[dict]:
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT * FROM clients WHERE id=?", (cid,)
                ).fetchone()
                return self._row_to_client(r) if r else None
            finally:
                conn.close()

    async def get_all_clients(self) -> List[dict]:
        # Og'ir o'qish — ishchi oqimda
        return await self._in_thread(self._all_clients)

    async def get_internal_client(self) -> Optional[dict]:
        """«Chinor» — do'konning o'zi (ichki rasxod mijozi). Bo'lmasa None."""
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT * FROM clients WHERE is_internal=1 ORDER BY id LIMIT 1"
                ).fetchone()
                return self._row_to_client(r) if r else None
            finally:
                conn.close()

    async def set_client_type(self, cid: int, client_type: str) -> None:
        """Mijoz turini almashtiradi: dona ⇄ optom (ulgurji)."""
        ctype = "optom" if str(client_type).lower().startswith("opt") else "dona"
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE clients SET client_type=? WHERE id=?",
                    (ctype, int(cid))
                )
                conn.commit()
            finally:
                conn.close()

    async def set_client_allow_credit(self, cid: int, allow: bool) -> None:
        """Mijozni qarzga (nasiya) savdoga belgilash / belgini olib tashlash."""
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE clients SET allow_credit=? WHERE id=?",
                    (1 if allow else 0, int(cid))
                )
                conn.commit()
            finally:
                conn.close()
        c = await self.get_client_by_id(cid)
        if c:
            await self._refresh_client(c)

    async def update_client(self, cid: int, shop_name: str = None,
                            phone: str = None) -> Tuple[bool, str]:
        """Mijoz nomi/telefonini tahrirlash. None berilgan maydon o'zgarmaydi.
        Telefon boshqa mijozda band bo'lsa (False, sabab) qaytaradi."""
        c = await self.get_client_by_id(cid)
        if not c:
            return False, "Mijoz topilmadi"
        phone_norm = normalize_phone(phone) if phone is not None else None
        with self._lock:
            conn = self._conn()
            try:
                if phone_norm:
                    for r in conn.execute(
                        "SELECT id, phone FROM clients WHERE phone IS NOT NULL "
                        "AND phone != '' AND id != ?", (int(cid),)
                    ).fetchall():
                        if phones_match(r["phone"], phone_norm):
                            return False, "Bu telefon boshqa mijozda mavjud"
                sets, vals = [], []
                if shop_name is not None and shop_name.strip():
                    sets.append("shop_name=?")
                    vals.append(shop_name.strip()[:80])
                if phone_norm is not None:
                    sets.append("phone=?")
                    vals.append(phone_norm)
                if not sets:
                    return False, "O'zgartiriladigan maydon yo'q"
                vals.append(int(cid))
                conn.execute(f"UPDATE clients SET {', '.join(sets)} WHERE id=?", vals)
                conn.commit()
            finally:
                conn.close()
        c = await self.get_client_by_id(cid)
        if c:
            await self._refresh_client(c)
        return True, ""

    # ── Bonus tizimi ─────────────────────────────────────────────────────────
    async def add_bonus(self, cid: int, delta: float, reason: str = "",
                        by_name: str = "", sale_id: int = 0) -> Optional[float]:
        """Mijoz bonusini o'zgartiradi (delta: + yig'ildi / - ishlatildi) va
        bonus_log ga iz yozadi. Balans 0 dan pastga tushmaydi (delta shunga
        qarab qirqiladi). Yangi balansni qaytaradi, mijoz topilmasa None."""
        delta = float(delta or 0)
        if not delta:
            return None
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT bonus_balance FROM clients WHERE id=?", (int(cid),)
                ).fetchone()
                if not r:
                    return None
                cur_bal = float(r["bonus_balance"] or 0)
                new_bal = max(0.0, round(cur_bal + delta, 2))
                real_delta = round(new_bal - cur_bal, 2)
                if not real_delta:
                    return cur_bal
                conn.execute(
                    "UPDATE clients SET bonus_balance=? WHERE id=?",
                    (new_bal, int(cid))
                )
                conn.execute(
                    "INSERT INTO bonus_log(client_id, sale_id, delta, "
                    "balance_after, reason, by_name, created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (int(cid), int(sale_id or 0), real_delta, new_bal,
                     (reason or "")[:160], (by_name or "")[:80], _now())
                )
                conn.commit()
                return new_bal
            finally:
                conn.close()

    def _bonus_log(self, cid: int, limit: int = 100) -> List[Dict]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM bonus_log WHERE client_id=? "
                    "ORDER BY id DESC LIMIT ?", (int(cid), int(limit))
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    async def get_bonus_log(self, cid: int, limit: int = 100) -> List[Dict]:
        return await self._in_thread(self._bonus_log, cid, limit)

    # ── Mijoz kartochkasi: xaridlar, to'lovlar, statistika ───────────────────
    def _client_sales(self, cid: int, limit: int = 60) -> List[Dict]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM sales WHERE client_id=? "
                    "ORDER BY id DESC LIMIT ?", (int(cid), int(limit))
                ).fetchall()
                return [self._row_to_sale(r) for r in rows]
            finally:
                conn.close()

    async def get_client_sales(self, cid: int, limit: int = 60) -> List[Dict]:
        return await self._in_thread(self._client_sales, cid, limit)

    def _client_payments(self, cid: int, limit: int = 60) -> List[Dict]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM payments WHERE client_id=? "
                    "ORDER BY id DESC LIMIT ?", (int(cid), int(limit))
                ).fetchall()
                return [self._row_to_payment(r) for r in rows]
            finally:
                conn.close()

    async def get_client_payments(self, cid: int, limit: int = 60) -> List[Dict]:
        return await self._in_thread(self._client_payments, cid, limit)

    def _client_stats(self, cid: int) -> Dict:
        """Mijozning umumiy xarid statistikasi (barcha davr).
        Qaytarish (is_return) manfiy hisoblanadi, ichki rasxod kirmaydi."""
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT COUNT(*) AS cnt,"
                    " COALESCE(SUM(CASE WHEN is_return=1 THEN -total ELSE total END),0) AS revenue,"
                    " MAX(created_at) AS last_at"
                    " FROM sales WHERE client_id=? AND COALESCE(is_internal,0)=0",
                    (int(cid),)
                ).fetchone()
                cnt = int(r["cnt"] or 0)
                revenue = float(r["revenue"] or 0)
                pays = conn.execute(
                    "SELECT COALESCE(SUM(amount),0) AS paid, COUNT(*) AS pcnt "
                    "FROM payments WHERE client_id=?", (int(cid),)
                ).fetchone()
                return {
                    "sale_count": cnt,
                    "revenue_sum": round(revenue, 2),
                    "avg_check": round(revenue / cnt, 2) if cnt else 0.0,
                    "last_sale_at": r["last_at"] or "",
                    "paid_sum": float(pays["paid"] or 0),
                    "payment_count": int(pays["pcnt"] or 0),
                }
            finally:
                conn.close()

    async def get_client_stats(self, cid: int) -> Dict:
        return await self._in_thread(self._client_stats, cid)

    # ── Top mijozlar (davr bo'yicha reyting) ─────────────────────────────────
    def _top_clients(self, since: str = "", limit: int = 50) -> List[Dict]:
        """Sotuvlar bo'yicha mijozlar reytingi. since — 'YYYY-MM-DD HH:MM:SS'
        (bo'sh = barcha davr). Ichki (Chinor) mijoz kirmaydi, qaytarishlar
        ayiriladi. Har mijoz: revenue, cheklar soni, oxirgi xarid."""
        with self._lock:
            conn = self._conn()
            try:
                where = "s.client_id > 0 AND COALESCE(s.is_internal,0)=0"
                params: list = []
                if since:
                    where += " AND s.created_at >= ?"
                    params.append(since)
                params.append(int(limit))
                rows = conn.execute(
                    "SELECT s.client_id AS id,"
                    " COALESCE(SUM(CASE WHEN s.is_return=1 THEN -s.total ELSE s.total END),0) AS revenue,"
                    " SUM(CASE WHEN COALESCE(s.is_return,0)=0 THEN 1 ELSE 0 END) AS checks,"
                    " MAX(s.created_at) AS last_at,"
                    " c.shop_name, c.phone, c.client_type, c.debt,"
                    " COALESCE(c.bonus_balance,0) AS bonus_balance"
                    " FROM sales s JOIN clients c ON c.id = s.client_id"
                    f" WHERE {where}"
                    " GROUP BY s.client_id"
                    " ORDER BY revenue DESC LIMIT ?",
                    params
                ).fetchall()
                out = []
                for r in rows:
                    d = dict(r)
                    checks = int(d.get("checks") or 0)
                    rev = float(d.get("revenue") or 0)
                    d["revenue"] = round(rev, 2)
                    d["checks"] = checks
                    d["avg_check"] = round(rev / checks, 2) if checks else 0.0
                    out.append(d)
                return out
            finally:
                conn.close()

    async def get_top_clients(self, since: str = "", limit: int = 50) -> List[Dict]:
        return await self._in_thread(self._top_clients, since, limit)

    async def delete_client(self, cid: int):
        mid = 0
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT channel_msg_id FROM clients WHERE id=?", (cid,)
                ).fetchone()
                if r:
                    mid = r["channel_msg_id"]
                conn.execute("DELETE FROM clients WHERE id=?", (cid,))
                conn.commit()
            finally:
                conn.close()
        if mid:
            await self._delete_msg(mid)

    async def _refresh_client(self, c: dict):
        mid = c.get("channel_msg_id", 0)
        if mid:
            await self._edit(mid, _fmt_client(c))

    async def add_debt(self, cid: int, amount_sum: float = 0,
                       amount_usd: float = 0):
        """Mijoz qarziga qo'shish. Ikkala valyuta ham yangilanadi.
        Faqat birini bersangiz, ikkinchisi joriy kurs bo'yicha hisoblanadi."""
        rate = self.get_usd_rate()
        if amount_usd and not amount_sum:
            amount_sum = round(usd_to_sum(amount_usd, rate), 2)
        elif amount_sum and not amount_usd:
            amount_usd = round(sum_to_usd(amount_sum, rate), 4)
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE clients SET debt = COALESCE(debt,0) + ?, "
                    "debt_usd = COALESCE(debt_usd,0) + ? WHERE id=?",
                    (amount_sum, amount_usd, cid)
                )
                conn.commit()
            finally:
                conn.close()
        c = await self.get_client_by_id(cid)
        if c:
            await self._refresh_client(c)

    async def add_payment(self, cid: int, amount: float,
                          currency: str = "sum", note: str = "") -> dict:
        """Mijozdan to'lov qabul qilish. currency='sum' yoki 'usd'.
        Qarz USD da kamaytiriladi (ikkala kesh ham yangilanadi)."""
        c = await self.get_client_by_id(cid)
        if not c:
            return {}
        rate = self.get_usd_rate()
        currency = "usd" if str(currency).lower() == "usd" else "sum"
        if currency == "usd":
            amount_usd = float(amount)
            amount_sum = round(usd_to_sum(amount_usd, rate), 2)
        else:
            amount_sum = float(amount)
            amount_usd = round(sum_to_usd(amount_sum, rate), 4)
        new_debt_usd = max(0.0, float(c.get("debt_usd", 0) or 0) - amount_usd)
        new_debt_sum = round(usd_to_sum(new_debt_usd, rate), 2)
        created = _now()
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE clients SET debt=?, debt_usd=? WHERE id=?",
                    (new_debt_sum, new_debt_usd, cid)
                )
                cur = conn.execute(
                    "INSERT INTO payments(client_id, client_tg_id, shop_name, "
                    "amount, amount_usd, currency, usd_rate, note, created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (cid, c["telegram_id"], c["shop_name"],
                     amount_sum, amount_usd, currency, rate, note, created)
                )
                pid = cur.lastrowid
                conn.commit()
            finally:
                conn.close()
        c = await self.get_client_by_id(cid)
        if c:
            await self._refresh_client(c)
        data = {
            "id": pid, "client_id": cid, "client_tg_id": c["telegram_id"],
            "shop_name": c["shop_name"],
            "amount": amount_sum, "amount_usd": amount_usd,
            "currency": currency, "usd_rate": rate,
            "note": note, "created_at": created
        }
        mid = await self._send(_fmt_payment(data))
        if mid:
            with self._lock:
                conn = self._conn()
                try:
                    conn.execute(
                        "UPDATE payments SET channel_msg_id=? WHERE id=?", (mid, pid)
                    )
                    conn.commit()
                finally:
                    conn.close()
        return data

