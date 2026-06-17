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

