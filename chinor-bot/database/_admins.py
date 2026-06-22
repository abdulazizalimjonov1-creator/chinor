"""Adminlar, rollar, ruxsatlar va global valyuta rejimi."""

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


class AdminsMixin:
    # ── Admin ────────────────────────────────────────────────────────────────
    async def add_admin(self, tg_id: int, full_name: str, added_by: int) -> bool:
        with self._lock:
            conn = self._conn()
            try:
                exists = conn.execute(
                    "SELECT 1 FROM admins WHERE telegram_id=?", (tg_id,)
                ).fetchone()
                if exists:
                    return False
                conn.execute(
                    "INSERT INTO admins(telegram_id, full_name, added_by, created_at) "
                    "VALUES(?,?,?,?)",
                    (tg_id, full_name, added_by, _now())
                )
                conn.commit()
            finally:
                conn.close()
        # Kanalga post
        data = {"telegram_id": tg_id, "full_name": full_name,
                "added_by": added_by, "created_at": _now()}
        mid = await self._send(_fmt_admin(data))
        if mid:
            with self._lock:
                conn = self._conn()
                try:
                    conn.execute("UPDATE admins SET channel_msg_id=? WHERE telegram_id=?",
                                 (mid, tg_id))
                    conn.commit()
                finally:
                    conn.close()
        return True

    async def remove_admin(self, tg_id: int):
        mid = 0
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT channel_msg_id FROM admins WHERE telegram_id=?", (tg_id,)
                ).fetchone()
                if row:
                    mid = row["channel_msg_id"]
                conn.execute("DELETE FROM admins WHERE telegram_id=?", (tg_id,))
                conn.commit()
            finally:
                conn.close()
        if mid:
            await self._delete_msg(mid)

    async def get_all_admins(self) -> List[dict]:
        # Og'ir o'qish — ishchi oqimda (event loop muzlamaydi)
        return await self._in_thread(self._all_admins)

    async def is_admin(self, tg_id: int) -> bool:
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT 1 FROM admins WHERE telegram_id=?", (tg_id,)
                ).fetchone()
                return bool(r)
            finally:
                conn.close()

    # ── Admin rollar va ruxsatlar ────────────────────────────────────────────
    async def get_admin(self, tg_id: int) -> Optional[dict]:
        """Bitta adminning to'liq yozuvini qaytaradi (role, permissions, currency_mode bilan)."""
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT * FROM admins WHERE telegram_id=?", (tg_id,)
                ).fetchone()
                return self._row_to_admin(r) if r else None
            finally:
                conn.close()

    async def set_admin_role(self, tg_id: int, role: str):
        """Admin rolini saqlaydi (full / products / cashier / stats / custom)."""
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE admins SET role=? WHERE telegram_id=?",
                    (role or "full", tg_id)
                )
                conn.commit()
            finally:
                conn.close()

    async def set_admin_permissions(self, tg_id: int, perms_json: str):
        """Adminning maxsus ruxsatlar JSON'ini saqlaydi (role='custom' bo'lganda)."""
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE admins SET permissions=? WHERE telegram_id=?",
                    (perms_json or "", tg_id)
                )
                conn.commit()
            finally:
                conn.close()

    async def set_admin_currency_mode(self, tg_id: int, mode: str):
        """Admin uchun valyuta rejimi override:
        '' (yoki None) → global rejim ishlatiladi
        'hybrid' | 'uzs_only' | 'usd_only' → shu rejim majburan."""
        m = (mode or "").strip().lower()
        if m not in ("", "hybrid", "uzs_only", "usd_only"):
            m = ""
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE admins SET currency_mode=? WHERE telegram_id=?",
                    (m, tg_id)
                )
                conn.commit()
            finally:
                conn.close()

    # ── Mini App credentials (login + parol hash) ────────────────────────────
    async def get_admin_by_username(self, username: str) -> Optional[dict]:
        u = (username or "").strip()
        if not u:
            return None
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT * FROM admins WHERE LOWER(username)=LOWER(?)", (u,)
                ).fetchone()
                return self._row_to_admin(r) if r else None
            finally:
                conn.close()

    async def ensure_admin_row(self, tg_id: int, full_name: str = "",
                                role: str = "full") -> None:
        """Berilgan tg_id uchun admins jadvalida qator bo'lishini ta'minlaydi.
        MUHIM: Bosh admin (GLAVNIY_ADMIN_ID) ko'pincha jadvalda bo'lmaydi —
        u faqat konstanta orqali tan olinadi. Login/parol saqlash, rol/ruxsat
        ko'rsatish kabi amallar uchun unga ham qator kerak."""
        with self._lock:
            conn = self._conn()
            try:
                exists = conn.execute(
                    "SELECT 1 FROM admins WHERE telegram_id=?", (int(tg_id),)
                ).fetchone()
                if not exists:
                    conn.execute(
                        "INSERT INTO admins(telegram_id, full_name, added_by, "
                        "created_at, role) VALUES(?,?,?,?,?)",
                        (int(tg_id), full_name or str(tg_id), int(tg_id),
                         _now(), role or "full")
                    )
                    conn.commit()
            finally:
                conn.close()

    async def set_admin_credentials(self, tg_id: int, username: str,
                                     password_hash: str) -> bool:
        """Mini App uchun login/parol saqlash. Boshqa adminda shu login bor
        bo'lsa False (band). Admin qatori bo'lmasa — avtomatik yaratiladi
        (bosh admin uchun muhim)."""
        u = (username or "").strip()
        if not u or not password_hash:
            return False
        # Bosh admin (yoki har qanday) qatori bo'lmasa — yaratamiz
        await self.ensure_admin_row(tg_id)
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT telegram_id FROM admins "
                    "WHERE LOWER(username)=LOWER(?) AND telegram_id!=?",
                    (u, int(tg_id))
                ).fetchone()
                if r:
                    return False
                # mijozlarda ham band emasligini tekshiramiz (umumiy login fazo)
                r2 = conn.execute(
                    "SELECT id FROM clients WHERE LOWER(username)=LOWER(?)", (u,)
                ).fetchone()
                if r2:
                    return False
                cur = conn.execute(
                    "UPDATE admins SET username=?, password_hash=? "
                    "WHERE telegram_id=?",
                    (u, password_hash, int(tg_id))
                )
                conn.commit()
                # rowcount 0 bo'lsa — kutilmagan holat (qator yo'q); False
                return cur.rowcount > 0
            finally:
                conn.close()

    async def clear_admin_credentials(self, tg_id: int) -> None:
        """Adminning login + parolini butunlay o'chiradi.
        Foydalanuvchi keyin qaytadan «🔑 Login/parol» dan yaratishi mumkin."""
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE admins SET username='', password_hash='' "
                    "WHERE telegram_id=?", (int(tg_id),)
                )
                conn.commit()
            finally:
                conn.close()

    async def admin_has_credentials(self, tg_id: int) -> bool:
        with self._lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT username, password_hash FROM admins WHERE telegram_id=?",
                    (int(tg_id),)
                ).fetchone()
                if not r:
                    return False
                return bool(r["username"]) and bool(r["password_hash"])
            finally:
                conn.close()

    # ── Global valyuta rejimi (settings) ─────────────────────────────────────
    def get_currency_mode_global(self) -> str:
        """Default 'hybrid' — ikkalasi ko'rinadi.
        Mumkin qiymatlar: 'hybrid' | 'uzs_only' | 'usd_only'."""
        v = (self.get_setting("currency_mode_global", "hybrid") or "hybrid").strip().lower()
        if v not in ("hybrid", "uzs_only", "usd_only"):
            v = "hybrid"
        return v

    def set_currency_mode_global(self, mode: str):
        m = (mode or "").strip().lower()
        if m not in ("hybrid", "uzs_only", "usd_only"):
            m = "hybrid"
        self.set_setting("currency_mode_global", m)

