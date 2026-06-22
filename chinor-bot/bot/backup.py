"""
Qo'lda backup yordamchi funksiyalari.

Avtomatik backup o'chirilgan. Backupni qo'lda
"📥 Eksport → 🗄️ Backup (.zip)" menyusidan olish mumkin.
"""

import os
import zipfile
from aiogram import Bot
from aiogram.types import FSInputFile

from bot.config import GLAVNIY_ADMIN_ID
from database.channel_db import DB_PATH, now_local


def _backup_dir() -> str:
    d = os.path.join(os.path.dirname(DB_PATH), "backups")
    os.makedirs(d, exist_ok=True)
    return d


def make_backup() -> str:
    """pos.db ni .zip ga qadoqlaydi va yo'lni qaytaradi."""
    if not os.path.exists(DB_PATH):
        return ""
    ts = now_local().strftime("%Y%m%d_%H%M%S")
    zip_path = os.path.join(_backup_dir(), f"pos_backup_{ts}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(DB_PATH, arcname=os.path.basename(DB_PATH))
        # WAL fayllarini ham qo'shamiz (agar bor bo'lsa)
        for ext in ("-wal", "-shm"):
            extra = DB_PATH + ext
            if os.path.exists(extra):
                zf.write(extra, arcname=os.path.basename(extra))
    return zip_path


async def send_backup(bot: Bot) -> bool:
    """Bosh adminga backup zip jo'natadi (qo'lda chaqiriladi)."""
    try:
        path = make_backup()
        if not path:
            return False
        size_kb = os.path.getsize(path) / 1024
        caption = (
            f"🗄️ <b>Backup</b>\n"
            f"📅 {now_local().strftime('%Y-%m-%d %H:%M')}\n"
            f"💾 Hajmi: {size_kb:,.1f} KB"
        )
        await bot.send_document(
            GLAVNIY_ADMIN_ID,
            FSInputFile(path),
            caption=caption,
            parse_mode="HTML"
        )
        # Eski zip larni saqlab qolamiz, faqat 30 tadan ko'pini o'chiramiz
        _cleanup_old_backups(keep=30)
        return True
    except Exception as e:
        print(f"[Backup xato] {e}")
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
