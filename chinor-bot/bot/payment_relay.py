"""To'lov-link relay — kassa QR to'lovi uchun.

Ish tartibi:
  1. Kassa "QR to'lov" bosadi → serverga `/api/desktop/payment/begin` keladi →
     `begin()` joriy server vaqtini (`since`) qaytaradi.
  2. Kassa egaga xabar yuboradi (`/api/desktop/payment/notify` → `send_message`).
  3. Ega (PAYMENT_OWNER_IDS) to'lov linkini ALOHIDA to'lov botiga yuboradi.
  4. Shu modulning fon-polleri (`poll_loop`) to'lov botini doimiy kuzatadi va
     egadan kelgan URL'ni server vaqti bilan birga eslab qoladi.
  5. Kassa `/api/desktop/payment/poll?since=...` so'raydi → `get_link(since)`
     `since` dan KEYIN qabul qilingan oxirgi linkni qaytaradi.

Vaqt taqqoslash SERVER soatida bo'ladi (Telegram soati bilan emas) — shuning
uchun begin/poll orasida skew yo'q. Asosiy botdan ALOHIDA token ishlatiladi,
shuning uchun getUpdates to'qnashmaydi.
"""
import asyncio
import logging
import re
import time

import aiohttp

from .config import PAYMENT_BOT_TOKEN, PAYMENT_OWNER_IDS

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

# Fon-poller eslab qoladigan oxirgi link (server vaqti bilan).
_state = {
    "link": "",
    "recv_ts": 0.0,   # server time.time() — link qabul qilingan payt
    "from_id": 0,
}
_last_update_id = 0


def is_enabled() -> bool:
    return bool(PAYMENT_BOT_TOKEN)


def _extract_link(text: str) -> str:
    m = _URL_RE.search(text or "")
    return m.group(0) if m else ""


def begin() -> float:
    """Yangi to'lov uchun "shu vaqtdan keyin" belgisi — joriy server vaqti."""
    return time.time()


def get_link(since: float) -> str | None:
    """`since` (server vaqti) dan keyin qabul qilingan link bo'lsa qaytaradi."""
    try:
        since = float(since or 0)
    except (TypeError, ValueError):
        since = 0.0
    if _state["link"] and _state["recv_ts"] >= since:
        return _state["link"]
    return None


async def send_message(text: str) -> bool:
    """Egaga (PAYMENT_OWNER_IDS) to'lov boti orqali xabar yuboradi."""
    if not PAYMENT_BOT_TOKEN or not text:
        return False
    url = f"https://api.telegram.org/bot{PAYMENT_BOT_TOKEN}/sendMessage"
    ok_any = False
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for chat_id in PAYMENT_OWNER_IDS:
                try:
                    async with session.post(url, json={"chat_id": chat_id, "text": text}) as r:
                        if r.status == 200:
                            ok_any = True
                except Exception as e:
                    logger.warning(f"[payment] send_message {chat_id} xato: {e}")
    except Exception as e:
        logger.warning(f"[payment] send_message xato: {e}")
    return ok_any


async def _get_updates(session: aiohttp.ClientSession, offset: int, timeout_s: int) -> list:
    url = f"https://api.telegram.org/bot{PAYMENT_BOT_TOKEN}/getUpdates"
    params = {"offset": offset, "timeout": timeout_s, "allowed_updates": '["message"]'}
    async with session.get(url, params=params) as r:
        data = await r.json()
    if not data.get("ok"):
        return []
    return data.get("result") or []


async def poll_loop():
    """Fon vazifasi: to'lov botini doimiy kuzatadi (long-poll getUpdates)."""
    global _last_update_id
    if not PAYMENT_BOT_TOKEN:
        logger.info("[payment] PAYMENT_BOT_TOKEN yo'q — QR to'lov relay o'chiq")
        return
    owners = set(PAYMENT_OWNER_IDS)
    logger.info(f"[payment] to'lov-link relay yoqildi (egalar: {sorted(owners)})")

    baseline_done = False
    # aiohttp sessiyasi: long-poll uchun umumiy timeout uzunroq.
    timeout = aiohttp.ClientTimeout(total=40)
    while True:
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                while True:
                    # Ishga tushishda eski (backlog) xabarlarni link sifatida
                    # qabul qilmaymiz — faqat update_id ni oldinga suramiz.
                    long_timeout = 0 if not baseline_done else 25
                    updates = await _get_updates(session, _last_update_id + 1, long_timeout)
                    if updates:
                        for upd in updates:
                            uid = upd.get("update_id", 0)
                            if uid > _last_update_id:
                                _last_update_id = uid
                            if not baseline_done:
                                continue  # backlog — e'tiborsiz
                            msg = upd.get("message") or {}
                            frm = (msg.get("from") or {}).get("id")
                            text = msg.get("text") or ""
                            if frm in owners and text:
                                link = _extract_link(text)
                                if link:
                                    _state["link"] = link
                                    _state["recv_ts"] = time.time()
                                    _state["from_id"] = frm
                                    logger.info(f"[payment] link qabul qilindi (from={frm})")
                    elif not baseline_done:
                        # Backlog tugadi — endi yangi xabarlarni tinglaymiz.
                        baseline_done = True
        except asyncio.CancelledError:
            logger.info("[payment] relay to'xtatildi")
            raise
        except Exception as e:
            logger.warning(f"[payment] poll xato: {e} — 5s kutiladi")
            await asyncio.sleep(5)
