"""Mini App auth — login/parol validatsiyasi va xavfsiz hashing.

Mantiq:
  • Login/parol ikkalasi ham kamida 8 belgi, harf VA son qatnashishi shart.
  • Parol PBKDF2-HMAC-SHA256 (Python stdlib) bilan hashlanadi — bcrypt
    paketini o'rnatish shart emas, lekin yetarli darajada xavfsiz
    (200,000 iteratsiya + tasodifiy salt).
  • Hash formati: 'pbkdf2$<iterations>$<salt_b64>$<hash_b64>' — bitta
    matn maydoniga osongina sig'adi.
  • Brute-force himoyasi: LoginAttemptTracker — 5 noto'g'ri urinish/15 min
    → 30 minutga blokirovka. In-memory (single-bot deployment uchun yetarli).
  • Telegram WebApp initData HMAC-SHA256 tekshirgich — frontend yuborgan
    foydalanuvchini Telegram tomonidan tasdiqlanganligini tasdiqlash uchun.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import string
import time
from collections import deque
from typing import Dict, Tuple


# ─── Validatsiya qoidalari ──────────────────────────────────────────────────

MIN_LENGTH = 8
MAX_LENGTH = 64

# Login: faqat ASCII harflar, raqamlar, '.' '_' '-'  — boshqa belgilarsiz.
_USERNAME_OK = re.compile(r"^[A-Za-z0-9._-]+$")


def _has_letter_and_digit(s: str) -> bool:
    has_letter = any(c.isalpha() for c in s)
    has_digit = any(c.isdigit() for c in s)
    return has_letter and has_digit


def validate_username(s: str) -> tuple[bool, str]:
    """Login qoidalariga mosmi tekshiradi.
    Qaytaradi: (ok, xato_xabari). Xabar o'zbek tilida — to'g'ridan-to'g'ri
    foydalanuvchiga ko'rsatish mumkin."""
    s = (s or "").strip()
    if not s:
        return False, "Login bo'sh."
    if len(s) < MIN_LENGTH:
        return False, f"Login kamida {MIN_LENGTH} ta belgi bo'lishi kerak."
    if len(s) > MAX_LENGTH:
        return False, f"Login {MAX_LENGTH} belgidan oshmasin."
    if not _USERNAME_OK.fullmatch(s):
        return False, ("Loginda faqat lotin harflari, raqamlar va "
                        "_ . - belgilari ishlatilishi mumkin.")
    if not _has_letter_and_digit(s):
        return False, "Loginda harf VA son ikkalasi qatnashishi kerak."
    return True, ""


def validate_password(s: str) -> tuple[bool, str]:
    """Parol qoidalari (login bilan deyarli bir xil)."""
    if not s:
        return False, "Parol bo'sh."
    if len(s) < MIN_LENGTH:
        return False, f"Parol kamida {MIN_LENGTH} ta belgi bo'lishi kerak."
    if len(s) > MAX_LENGTH:
        return False, f"Parol {MAX_LENGTH} belgidan oshmasin."
    if not _has_letter_and_digit(s):
        return False, "Parolda harf VA son ikkalasi qatnashishi kerak."
    if any(ord(c) < 32 for c in s):
        return False, "Parolda boshqaruv belgilari (yangi qator va h.k.) bo'lmasin."
    return True, ""


# ─── Hashing (PBKDF2-HMAC-SHA256) ───────────────────────────────────────────

PBKDF2_ITERATIONS = 200_000
PBKDF2_HASH = "sha256"
PBKDF2_SALT_BYTES = 16


def hash_password(password: str) -> str:
    """Tasodifiy salt bilan parolni hashlaydi va `pbkdf2$...` formatida
    saqlash uchun yagona qator qaytaradi."""
    salt = secrets.token_bytes(PBKDF2_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac(
        PBKDF2_HASH, password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    salt_b64 = base64.b64encode(salt).decode("ascii")
    dk_b64 = base64.b64encode(dk).decode("ascii")
    return f"pbkdf2${PBKDF2_ITERATIONS}${salt_b64}${dk_b64}"


def verify_password(password: str, stored: str) -> bool:
    """Berilgan parolni saqlangan hash bilan vaqtga doimiy solishtiradi.
    Format buzilgan bo'lsa False qaytaradi (raise qilmaydi)."""
    if not stored or not password:
        return False
    try:
        algo, iters_s, salt_b64, dk_b64 = stored.split("$", 3)
        if algo != "pbkdf2":
            return False
        iters = int(iters_s)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
        actual = hashlib.pbkdf2_hmac(
            PBKDF2_HASH, password.encode("utf-8"), salt, iters
        )
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


# ─── Tasodifiy parol/login yaratuvchi (admin uchun qulay) ───────────────────

def generate_password(length: int = 10) -> str:
    """Harf+son aralash tasodifiy parol — qoidalarga avtomatik mos keladi."""
    length = max(MIN_LENGTH, min(length, MAX_LENGTH))
    alphabet = string.ascii_letters + string.digits
    # Kamida bitta harf VA bitta son bo'lishini ta'minlaymiz
    while True:
        p = "".join(secrets.choice(alphabet) for _ in range(length))
        if _has_letter_and_digit(p):
            return p


async def verify_login(db, username: str, password: str) -> dict | None:
    """Login + parolni admin/mijoz jadvallaridan tekshiradi.

    Qaytaradi:
      None — login topilmadi yoki parol noto'g'ri
      {"role": "admin"|"client", "user": <dict>} — muvaffaqiyatli

    'admin' rolida user — admin yozuvi (telegram_id bor);
    'client' rolida user — mijoz yozuvi (telegram_id bo'lishi yoki bo'lmasligi mumkin)."""
    u = (username or "").strip()
    if not u or not password:
        return None
    # Avval admin
    admin = await db.get_admin_by_username(u)
    if admin and admin.get("password_hash"):
        if verify_password(password, admin["password_hash"]):
            return {"role": "admin", "user": admin}
    # Keyin mijoz
    client = await db.get_client_by_username(u)
    if client and client.get("password_hash"):
        if verify_password(password, client["password_hash"]):
            return {"role": "client", "user": client}
    return None


# ─── Brute-force himoyasi (rate limit + lockout) ────────────────────────────

class LoginAttemptTracker:
    """In-memory urinish hisoblagichi.
    Har bir 'kalit' (odatda Telegram ID) bo'yicha vaqt belgilarini saqlaydi.
    `LOCK_THRESHOLD` ta urinish `WINDOW_SECONDS` ichida bo'lsa — `LOCK_SECONDS`
    ga blokirovka qilamiz."""

    LOCK_THRESHOLD = 5     # 5 ta noto'g'ri urinish
    WINDOW_SECONDS = 15 * 60   # 15 daqiqa ichida
    LOCK_SECONDS   = 30 * 60   # 30 daqiqaga blok

    def __init__(self):
        self._attempts: Dict[str, deque] = {}
        self._locks: Dict[str, float] = {}

    @staticmethod
    def _key(k) -> str:
        return str(k)

    def is_locked(self, key) -> Tuple[bool, int]:
        """Qaytaradi (blokirovkalanganmi, qancha sekund qoldi)."""
        k = self._key(key)
        exp = self._locks.get(k, 0)
        now = time.time()
        if exp > now:
            return True, int(exp - now)
        if k in self._locks:
            del self._locks[k]
        return False, 0

    def record_failure(self, key) -> Tuple[int, bool]:
        """Noto'g'ri urinishni qayd qiladi. Qaytaradi:
        (oxirgi `WINDOW_SECONDS` ichidagi urinishlar soni,
         hozir blokirovkalandimi)."""
        k = self._key(key)
        now = time.time()
        dq = self._attempts.setdefault(k, deque())
        dq.append(now)
        # Eskirgan urinishlarni tozalaymiz
        cutoff = now - self.WINDOW_SECONDS
        while dq and dq[0] < cutoff:
            dq.popleft()
        count = len(dq)
        just_locked = False
        if count >= self.LOCK_THRESHOLD:
            self._locks[k] = now + self.LOCK_SECONDS
            just_locked = True
            dq.clear()
        return count, just_locked

    def clear(self, key) -> None:
        """Muvaffaqiyatli login bo'lganida — hisobni nolga."""
        k = self._key(key)
        self._attempts.pop(k, None)
        self._locks.pop(k, None)


# Yagona global tracker instansiyasi — barcha handlerlar shuni ishlatadi
attempt_tracker = LoginAttemptTracker()


# ─── Telegram WebApp initData HMAC tekshirgich ──────────────────────────────
# https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
# Frontend `tg.initData` ni botga yuborsa, biz uni Telegram bilan bog'liqligini
# (HMAC-SHA256, bot_token bilan) tekshira olamiz. Bu defense-in-depth — chunki
# `tg.sendData()` orqali kelgan xabar Telegram tomonidan allaqachon ishonchli
# (bot egasi bilan chatdagi message), lekin qo'shimcha tekshiruv zarar qilmaydi.

def verify_telegram_init_data(init_data: str, bot_token: str,
                                max_age_seconds: int = 86400) -> dict | None:
    """initData qatorni parsing qilib, HMAC bilan tekshiradi. Yaxshi bo'lsa —
    foydalanuvchi ma'lumotlarini dict qaytaradi, aks holda None."""
    if not init_data or not bot_token:
        return None
    try:
        from urllib.parse import parse_qsl
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = pairs.pop("hash", None)
        if not received_hash:
            return None
        # Data-check-string
        data_check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(pairs.items())
        )
        secret_key = hmac.new(b"WebAppData", bot_token.encode(),
                               hashlib.sha256).digest()
        calc_hash = hmac.new(secret_key, data_check_string.encode(),
                              hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc_hash, received_hash):
            return None
        # auth_date tekshiruvi (eskirib qolmagan)
        try:
            auth_date = int(pairs.get("auth_date", "0"))
            if auth_date and (time.time() - auth_date) > max_age_seconds:
                return None
        except ValueError:
            pass
        # 'user' qiymati JSON — parse qilamiz
        import json as _json
        user = _json.loads(pairs.get("user", "{}")) if pairs.get("user") else {}
        pairs["user"] = user
        return pairs
    except Exception:
        return None


def suggest_username(base: str, suffix_digits: int = 4) -> str:
    """Berilgan ism/telefon raqamiga moslab login takliflaydi.
    Misol: 'Abdulaziz' + 4 raqam → 'Abdulaziz1842'."""
    base = "".join(c for c in (base or "") if c.isalnum() or c in "._-")
    base = base[:MAX_LENGTH - suffix_digits] or "user"
    suffix = "".join(secrets.choice(string.digits) for _ in range(suffix_digits))
    out = f"{base}{suffix}"
    # Bu MAX_LENGTH ni o'tib ketishi mumkin emas
    return out[:MAX_LENGTH]
