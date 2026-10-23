"""Rasm yuklangandan keyingi ish TUGAMAY QOLSA — qayta bajariladi.

MUAMMO. Tovarga rasm yuklanganda bo'lim aniqlash, teglar, rasmni qayta
chizish va KANALGA E'LON — hammasi fon vazifasi sifatida ketadi
(`asyncio.ensure_future`). Server o'sha payt o'chsa yoki qayta ishga
tushsa, vazifa yarim yo'lda uziladi va QAYTA URINILMAYDI: tovar
bo'limsiz qoladi, kanalga ham chiqmaydi. Egasi buni faqat keyin, qo'lda
tekshirganda bilardi (2026-10-23 da aynan shunday bo'ldi — server
o'chib qolgan).

YECHIM. Ish boshlanishidan OLDIN tovar shu navbatga yoziladi, muvaffaqiyatli
tugagach o'chiriladi. Bot ishga tushganda va keyin davriy ravishda
navbatda qolganlar qayta bajariladi.

NEGA ALOHIDA NAVBAT, «bo'limsiz tovarlarni top» emas: katalogda 7000 dan
ortiq tovar bor va ularning aksariyati YesPOS sinxronidan kelgan —
bo'limsiz va kanalga chiqmagan. Ularni «tugallanmagan» deb hisoblasak,
kanalga minglab post ketib qolardi. Navbatga esa faqat ODAM rasm
yuklagan tovar tushadi.
"""

import asyncio
import logging
import sqlite3
import time

logger = logging.getLogger(__name__)

INTERVAL = int(60 * 10)        # har 10 daqiqada tekshiradi
STALE = 180                    # shuncha soniyadan keyin «uzilib qolgan» deyiladi
MAX_TRIES = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS enrich_queue (
    product_id INTEGER PRIMARY KEY,
    image_path TEXT DEFAULT '',
    tries      INTEGER DEFAULT 0,
    started_at REAL DEFAULT 0,
    error      TEXT DEFAULT ''
);
"""


def _conn():
    from database._helpers import DB_PATH

    c = sqlite3.connect(DB_PATH, timeout=20)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


def mark_start(pid: int, image_path: str = "") -> None:
    """Ish boshlandi — tugamasa shu yozuv qoladi."""
    try:
        c = _conn()
        try:
            c.execute(
                "INSERT INTO enrich_queue (product_id, image_path, tries, started_at) "
                "VALUES (?,?,0,?) "
                "ON CONFLICT(product_id) DO UPDATE SET "
                "  image_path=excluded.image_path, started_at=excluded.started_at",
                (int(pid), image_path or "", time.time()))
            c.commit()
        finally:
            c.close()
    except Exception as e:
        logger.info("🧩 Navbatga yozilmadi (#%s): %s", pid, e)


def mark_done(pid: int) -> None:
    """Ish tugadi — navbatdan chiqariladi."""
    try:
        c = _conn()
        try:
            c.execute("DELETE FROM enrich_queue WHERE product_id = ?", (int(pid),))
            c.commit()
        finally:
            c.close()
    except Exception as e:
        logger.info("🧩 Navbatdan o'chirilmadi (#%s): %s", pid, e)


def pending(stale: int = STALE) -> list:
    """Uzilib qolgan ishlar (boshlanganiga `stale` soniyadan ko'p bo'lgan)."""
    try:
        c = _conn()
        try:
            return [dict(r) for r in c.execute(
                "SELECT * FROM enrich_queue WHERE started_at < ? AND tries < ? "
                "ORDER BY started_at LIMIT 10",
                (time.time() - stale, MAX_TRIES))]
        finally:
            c.close()
    except Exception as e:
        logger.info("🧩 Navbat o'qilmadi: %s", e)
        return []


def _bump(pid: int, err: str = "") -> None:
    try:
        c = _conn()
        try:
            c.execute(
                "UPDATE enrich_queue SET tries = tries + 1, started_at = ?, "
                "error = ? WHERE product_id = ?",
                (time.time(), (err or "")[:200], int(pid)))
            c.commit()
        finally:
            c.close()
    except Exception:
        pass


async def recover_once() -> int:
    """Uzilib qolganlarni qayta bajaradi. Qaytaradi: nechtasi ishlandi."""
    rows = pending()
    if not rows:
        return 0
    from bot.web_api import _enrich_product

    n = 0
    for r in rows:
        pid = int(r["product_id"])
        logger.info("🧩 Tugamay qolgan ish qayta bajarilmoqda: #%s "
                    "(urinish %s)", pid, int(r["tries"]) + 1)
        _bump(pid)
        try:
            await _enrich_product(pid, r["image_path"] or "")
            mark_done(pid)
            n += 1
        except Exception as e:
            logger.warning("🧩 #%s qayta bajarilmadi: %s", pid, e)
            _bump(pid, str(e))
        await asyncio.sleep(2)
    return n


async def loop():
    # Ishga tushishda biroz kutamiz: baza va kanal ulanishi tayyor bo'lsin.
    await asyncio.sleep(90)
    while True:
        try:
            n = await recover_once()
            if n:
                logger.info("🧩 %s ta tovar qayta boyitildi", n)
        except Exception:
            logger.exception("🧩 Qayta boyitish")
        await asyncio.sleep(INTERVAL)
