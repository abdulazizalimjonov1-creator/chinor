"""YesPOS cheklarini davriy tortish — userbot (`marketplace.tg_receipts`).

Do'konning asosiy kassasi YesPOS: har bir savdo cheki do'kon egasining
Telegramiga @yespos_notify_bot orqali keladi. Userbot shu chatni O'QIYDI
(boshqa chatlarga tegmaydi, hech narsa yozmaydi) va cheklarni
`data/marketplace.db` ga yozadi. Tavsiyalar ("Ko'p sotilayotgan", "birga
olinadigan"), AI maslahatchidagi "sotildi/qaytarildi" va videolar shu
haqiqiy savdodan foydalanadi.

Alohida JARAYONDA ishga tushiriladi: Telethon sessiya fayli bilan ishlaydi,
bot jarayoniga yuk tushmasin va xato botni yiqitmasin. Kalitlar
`marketplace/.env` da (git'ga tushmaydi).
"""

import asyncio
import logging
import os
import sys

logger = logging.getLogger(__name__)

INTERVAL = int(os.getenv("RECEIPTS_SYNC_MINUTES", "30")) * 60
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _env() -> dict:
    env = dict(os.environ)
    path = os.path.join(_REPO, "marketplace", ".env")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        return {}
    return env


async def sync_once() -> str:
    env = _env()
    if not env.get("TG_API_ID") or not env.get("YESPOS_BOT"):
        return "sozlanmagan"
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "marketplace.tg_receipts", "sync",
        cwd=_REPO, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
    except asyncio.TimeoutError:
        proc.kill()
        return "vaqt tugadi"
    tail = (out or b"").decode(errors="ignore").strip().splitlines()[-3:]
    if proc.returncode == 0:
        # Yangi chek qatorlarini katalogga bog'laymiz — bog'lanmagan qator
        # buyurtma bashoratida «sotilmagan» bo'lib ko'rinadi (2026-10-20:
        # cheklar katalogdan oldin yuklangani uchun 117 ming qatordan 8 tasi
        # bog'langan edi, bashorat 45 pozitsiya ko'rsatardi).
        rl = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "marketplace.receipts", "relink",
            cwd=_REPO, env=env, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL)
        try:
            await asyncio.wait_for(rl.wait(), timeout=300)
        except asyncio.TimeoutError:
            rl.kill()
    return ("OK " if proc.returncode == 0 else f"XATO({proc.returncode}) ") + " | ".join(tail)


async def loop():
    if not _env().get("TG_API_ID"):
        logger.info("🧾 YesPOS cheklari: marketplace/.env yo'q — sinxron o'chiq")
        return
    await asyncio.sleep(120)
    while True:
        try:
            res = await sync_once()
            logger.info("🧾 YesPOS cheklari sinxroni: %s", res[:300])
        except Exception:
            logger.exception("🧾 YesPOS cheklari sinxroni")
        await asyncio.sleep(INTERVAL)
