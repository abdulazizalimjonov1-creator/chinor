"""
Baza zaxirasi (backup) — qo'lda va AVTOMATIK.

Nega avtomatik qayta yoqildi (2026-10-20): server endi do'kon egasining
Mac'ida ishlaydi. Oxirgi zaxira 2026-08 dan edi — disk buzilsa yoki
kompyuter yo'qolsa barcha savdo, qarz va mijozlar yo'qolardi. Endi har
kuni bir marta:
  • zaxira `data/backups/` ga yoziladi (oxirgi 30 tasi saqlanadi);
  • bir nusxa bosh adminga Telegram'da yuboriladi — kompyuterdan TASHQARIDA
    turadigan yagona nusxa shu.

Nusxa SQLite'ning o'z backup API'si bilan olinadi: baza ishlab turgan
paytda (WAL rejimida) faylni oddiy nusxalash yarim yozilgan holatni
olishi mumkin — tiklashda buzuq chiqadi. Backup API izchil holatni beradi.

Qo'lda: "📥 Eksport → 🗄️ Backup (.zip)" menyusi.
"""

import asyncio
import logging
import os
import sqlite3
import zipfile

from aiogram import Bot
from aiogram.types import FSInputFile

from bot.config import GLAVNIY_ADMIN_ID
from database.channel_db import DB_PATH, now_local

logger = logging.getLogger(__name__)

AUTO_HOUR, AUTO_MINUTE = 23, 30      # kun oxiri — savdo tugagach


def _backup_dir() -> str:
    d = os.path.join(os.path.dirname(DB_PATH), "backups")
    os.makedirs(d, exist_ok=True)
    return d


def make_backup() -> str:
    """Izchil nusxa → .zip. Yo'lni qaytaradi ('' — baza yo'q)."""
    if not os.path.exists(DB_PATH):
        return ""
    ts = now_local().strftime("%Y%m%d_%H%M%S")
    d = _backup_dir()
    snap = os.path.join(d, f".snap_{ts}.db")
    src = sqlite3.connect(DB_PATH, timeout=30)
    dst = sqlite3.connect(snap)
    try:
        src.backup(dst)           # ishlab turgan bazadan ham izchil nusxa
    finally:
        dst.close()
        src.close()
    # Tiklashda tekshiruv: nusxa haqiqatan ochiladimi
    chk = sqlite3.connect(snap)
    try:
        ok = chk.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        chk.close()
    if not ok:
        os.remove(snap)
        raise RuntimeError("zaxira nusxasi integrity_check'dan o'tmadi")
    zip_path = os.path.join(d, f"pos_backup_{ts}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(snap, arcname=os.path.basename(DB_PATH))
    os.remove(snap)
    return zip_path


async def send_backup(bot: Bot, auto: bool = False) -> bool:
    """Zaxira oladi va bosh adminga jo'natadi."""
    try:
        path = await asyncio.get_event_loop().run_in_executor(None, make_backup)
        if not path:
            return False
        size_kb = os.path.getsize(path) / 1024
        caption = (
            f"🗄️ <b>{'Kunlik avtomatik zaxira' if auto else 'Backup'}</b>\n"
            f"📅 {now_local().strftime('%Y-%m-%d %H:%M')}\n"
            f"💾 Hajmi: {size_kb:,.1f} KB\n"
            f"<i>Saqlab qo'ying — kompyuter buzilsa baza shundan tiklanadi.</i>"
        )
        await bot.send_document(GLAVNIY_ADMIN_ID, FSInputFile(path),
                                caption=caption, parse_mode="HTML",
                                disable_notification=auto)
        _cleanup_old_backups(keep=30)
        return True
    except Exception as e:
        logger.warning("🗄️ Zaxira xatosi: %s", e)
        return False


def _cleanup_old_backups(keep: int = 30):
    d = _backup_dir()
    files = [
        os.path.join(d, f) for f in os.listdir(d)
        if f.startswith("pos_backup_") and f.endswith(".zip")
    ]
    files.sort(key=os.path.getmtime, reverse=True)
    for old in files[keep:]:
        try:
            os.remove(old)
        except Exception:
            pass


def _last_backup_day() -> str:
    d = _backup_dir()
    names = sorted(f for f in os.listdir(d) if f.startswith("pos_backup_") and f.endswith(".zip"))
    return names[-1][11:19] if names else ""          # YYYYMMDD


async def auto_backup_loop(bot: Bot):
    """Har kuni AUTO_HOUR:AUTO_MINUTE da bitta zaxira. Kompyuter o'sha payt
    o'chiq bo'lsa — keyingi ishga tushishda o'sha kun uchun darrov olinadi."""
    await asyncio.sleep(90)
    while True:
        try:
            now = now_local()
            today = now.strftime("%Y%m%d")
            due = (now.hour, now.minute) >= (AUTO_HOUR, AUTO_MINUTE)
            import datetime as _dt
            yesterday = (now - _dt.timedelta(days=1)).strftime("%Y%m%d")
            last = _last_backup_day()
            # Kecha kechqurun kompyuter o'chiq bo'lgan — ertalab darrov olamiz
            missed = last < yesterday
            if last != today and (due or missed):
                ok = await send_backup(bot, auto=True)
                logger.info("🗄️ Avtomatik zaxira: %s", "OK" if ok else "XATO")
        except Exception:
            logger.exception("🗄️ Avtomatik zaxira sikli")
        await asyncio.sleep(300)
