"""Userbot xabarlari navbatini bo'shatadi (qarz eslatmalari).

Nega alohida ishchi: xabar bittalab va tanaffus bilan ketishi kerak
(qarang `marketplace/tg_notify.py` — PEER_FLOOD xavfi). HTTP so'rov
ichida yuborsak, admin tugmani bosganda sahifa daqiqalab kutib
turardi va bir nechta so'rov bir vaqtda yuborib yuborardi.

SESSIYA QULFI: Telethon sessiya fayli bir vaqtda BITTA jarayonda
ochiladi. Cheklar sinxroni (`receipts_sync`) ham o'shani ishlatadi,
shuning uchun ikkalasi `userbot_lock()` orqali navbatma-navbat
ishlaydi — aks holda «database is locked» chiqib, sinxron ham,
xabar ham yo'qolardi.
"""

import asyncio
import logging
import os
import sys

logger = logging.getLogger(__name__)

INTERVAL = int(os.getenv("NOTIFY_WORKER_MINUTES", "5")) * 60
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Userbot sessiyasiga kirishni ketma-ket qiladi (cheklar sinxroni + xabar).
_LOCK = asyncio.Lock()


def userbot_lock() -> asyncio.Lock:
    return _LOCK


def _env() -> dict:
    from bot.receipts_sync import _env as _re

    return _re()


async def send_once(limit: int = 0) -> str:
    """Navbatdagi xabarlarni yuboradi. Qaytaradi: qisqa natija matni."""
    env = _env()
    if not env.get("TG_API_ID"):
        return "sozlanmagan"
    args = [sys.executable, "-m", "marketplace.tg_notify", "send"]
    if limit:
        args += ["--limit", str(limit)]
    async with _LOCK:
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=_REPO, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            # Har xabar orasida 25-70 soniya tanaffus bor, shuning uchun
            # 20 ta xabar ~25 daqiqagacha cho'zilishi mumkin.
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=2400)
        except asyncio.TimeoutError:
            proc.kill()
            return "vaqt tugadi"
    tail = (out or b"").decode(errors="ignore").strip().splitlines()[-2:]
    return ("OK " if proc.returncode == 0 else f"XATO({proc.returncode}) ") \
        + " | ".join(tail)


def pending_count() -> int:
    """Navbatda nechta xabar kutyapti (0 — xato bo'lsa ham)."""
    try:
        if _REPO not in sys.path:
            sys.path.insert(0, _REPO)
        from marketplace.db import MarketplaceDB
        from marketplace.tg_notify import status

        with MarketplaceDB() as db:
            return int(status(db).get("pending") or 0)
    except Exception:
        return 0


async def loop():
    if not _env().get("TG_API_ID"):
        logger.info("📨 Userbot xabarlari: marketplace/.env yo'q — o'chiq")
        return
    await asyncio.sleep(180)
    while True:
        try:
            if pending_count() > 0:
                res = await send_once()
                logger.info("📨 Userbot xabarlari: %s", res[:300])
        except Exception:
            logger.exception("📨 Userbot xabarlari")
        await asyncio.sleep(INTERVAL)
