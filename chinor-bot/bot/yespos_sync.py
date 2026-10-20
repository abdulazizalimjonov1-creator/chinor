"""YesPOS → Chinor: tovar, qoldiq va narxni doim sinxron ushlab turish.

YesPOS — do'konning haqiqiy kassasi va haqiqat manbai. Har `INTERVAL`
daqiqada:
  1. `marketplace.sync stock` — YesPOS marketplace API'dan joriy qoldiq
     va narx (katalog kuniga bir marta: `marketplace.sync all`);
  2. `marketplace.pos_bridge --apply` — Chinor bazasiga yozadi: qoldiq,
     narx, yangi tovarlar (id = YesPOS sku). Tannarx/optomga tegilmaydi.

HIMOYA: pos_bridge bir xil id ostida butunlay boshqa nomli tovarni
("ID to'qnashuvi") ko'rsa, YOZMAYDI (chiqish kodi 3). Bunda bosh adminga
kuniga bir marta xabar boradi — qo'lda tekshirib `--force` bilan yoziladi.

Alohida jarayonda ishlaydi (API sekin — qoldiq 1–2 daqiqa), bot yiqilmaydi.
Kalit: `marketplace/.env` → YESPOS_API_KEY.
"""

import asyncio
import logging
import os
import sys
import time

logger = logging.getLogger(__name__)

INTERVAL = int(os.getenv("YESPOS_SYNC_MINUTES", "15")) * 60
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_last_alert_day = ""
_last_catalog_day = ""


def _env() -> dict:
    from bot.receipts_sync import _env as env
    return env()


async def _run(*args, timeout: int = 600) -> tuple:
    env = _env()
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", *args, cwd=_REPO, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, "vaqt tugadi"
    return proc.returncode, (out or b"").decode(errors="ignore")


async def sync_once(bot=None) -> str:
    global _last_alert_day, _last_catalog_day
    env = _env()
    if not env.get("YESPOS_API_KEY"):
        return "YESPOS_API_KEY yo'q"
    today = time.strftime("%Y-%m-%d")
    what = "all" if _last_catalog_day != today else "stock"
    rc, out = await _run("marketplace.sync", what)
    if rc != 0:
        return f"YesPOS API xatosi ({rc}): {out.strip().splitlines()[-1:] if out.strip() else ''}"
    if what == "all":
        _last_catalog_day = today
        # Katalog yangilandi (yangi tovar/nom) — cheklarni qayta bog'laymiz
        await _run("marketplace.receipts", "relink", timeout=300)
    rc, out = await _run("marketplace.pos_bridge", "--apply")
    if rc == 3:
        if bot and _last_alert_day != today:
            _last_alert_day = today
            from bot.config import GLAVNIY_ADMIN_ID
            lines = [l for l in out.splitlines() if "≠" in l][:10]
            try:
                await bot.send_message(
                    GLAVNIY_ADMIN_ID,
                    "⚠️ YesPOS sinxroni TO'XTADI — ID to'qnashuvi:\n"
                    + "\n".join(lines)
                    + "\n\nBir xil raqam ostida YesPOS'da boshqa tovar. Tekshirib, "
                      "tasdiqlang (qo'lda: pos_bridge --apply --force).")
            except Exception:
                pass
        return "to'qnashuv — yozilmadi"
    if rc != 0:
        return f"bridge xatosi ({rc})"
    summary = [l.strip() for l in out.splitlines()
               if any(k in l for k in ("Qoldiq o'zgaradi", "Narx o'zgaradi", "YANGI qo'shiladi"))]
    return "OK " + " | ".join(summary)


async def loop(bot=None):
    if not _env().get("YESPOS_API_KEY"):
        logger.info("🔄 YesPOS sinxroni: YESPOS_API_KEY yo'q — o'chiq")
        return
    await asyncio.sleep(60)
    while True:
        try:
            res = await sync_once(bot)
            logger.info("🔄 YesPOS tovar/qoldiq sinxroni: %s", res[:300])
            try:   # tavsiya indeksi yangi qoldiqni darhol ko'rsin
                from bot import api_recommend
                api_recommend._cache["at"] = 0
            except Exception:
                pass
        except Exception:
            logger.exception("🔄 YesPOS sinxroni")
        await asyncio.sleep(INTERVAL)
