"""Instagram Reels — mahsulot rasmidan avtomatik qisqa video.

MAQSAD: Instagram'dan odamlarni Mini App'ga olib kelish. Har bir tovar
uchun qo'lda video montaj qilib bo'lmaydi (6900 tovar), shuning uchun
video AYNAN o'sha daqiqada, odam tovarni rasmga olib ilovaga qo'shganda
avtomatik tayyorlanadi.

ISHGA TUSHISH SHARTI — faqat ODAM yuklagan rasm. YesPOS sinxroni, narx
yangilanishi yoki qoldiq o'zgarishi video yaratmaydi: zanjir `web_api`
dagi `_enrich_product` ichidan chaqiriladi, u esa faqat rasm yuklash
oqimida ishlaydi.

Video qanday tuziladi — HAR KADR alohida chiziladi (240 kadr):
  1. `rembg` mahsulotni fondan ajratadi — shundan keyin u brend foni
     ustida haqiqatan «suzadi», sakraydi va buriladi;
  2. Pillow har kadrda sahnani yig'adi: parallaks (fon mahsulotga teskari
     siljiydi), soya, mahsulot ustidan o'tuvchi yorug'lik chizig'i,
     matnlarning pastdan ko'tarilishi va narxning «muhr» bo'lib tushishi;
  3. kadrlar to'g'ridan-to'g'ri ffmpeg'ga uzatiladi va jimjit audio bilan
     MP4 (H.264) qilinadi.

Nega ffmpeg filtrlari emas: `zoompan` bilan faqat sekin yaqinlashish
chiqadi — u lentada e'tiborni ushlamaydi. Kadrma-kadr chizishda esa
harakat to'liq boshqariladi.

Nega matn ffmpeg'da emas, Pillow'da: `drawtext` filtri tovar nomidagi
qavs, apostrof va ikki nuqtani ekranlashni talab qiladi — 6900 xil nomda
bu albatta biror joyda sinadi. Pillow'da esa matn oddiy qator.

Joylash: Meta tokeni sozlangan bo'lsa Instagram Graph API orqali
avtomatik; sozlanmagan bo'lsa video tayyor matni bilan adminga
Telegram'ga keladi va u bir bosishda o'zi joylaydi.
"""

from __future__ import annotations

import os
import re
import math
import json
import time
import sys
import shutil
import asyncio
import logging
import sqlite3
import subprocess
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

# ─── Sozlamalar ─────────────────────────────────────────────────────────────

W, H = 1080, 1920                  # Instagram Reels o'lchami
DURATION = 15                      # soniya (ilgak → 3 sahna → solishtirish → narx)
FPS = 30

_DATA_DIR = os.path.abspath(os.environ.get("DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data"))
REELS_DIR = os.path.join(_DATA_DIR, "reels")

# Brend ranglari — kanal rasmlari bilan bir xil oila (imageproc.py)
BG_TOP = (24, 61, 41)
BG_BOTTOM = (13, 38, 26)
ACCENT = (116, 198, 157)
CARD_BG = (243, 251, 244)

# Instagram API manzili — "Instagram login" tokeni (IGA...) uchun
# graph.instagram.com, Facebook sahifasi tokeni (EAA...) uchun esa
# graph.facebook.com. config.IG_GRAPH_BASE orqali almashtiriladi.
_GRAPH_DEFAULT = "https://graph.instagram.com/v23.0"


def _cfg(name: str, default: str = "") -> str:
    try:
        from bot import config as _c
        return (getattr(_c, name, "") or default)
    except Exception:
        return os.getenv(name, default)


def _graph() -> str:
    return _cfg("IG_GRAPH_BASE", _GRAPH_DEFAULT).rstrip("/")


def is_available() -> bool:
    """ffmpeg bormi? Bo'lmasa butun mexanizm jim o'chadi."""
    return bool(shutil.which("ffmpeg"))


# ── Token: uzoq muddatli, lekin ~60 kunda tugaydi ──────────────────────────
# Instagram "long-lived" tokenini tugashidan oldin yangilab turish kerak.
# Yangi token .env ga emas, `data/ig_token.json` ga yoziladi (DATA_DIR kod
# daraxtidan tashqarida) — shunda deploy eski tokenni qaytarib qo'ymaydi.
_TOKEN_FILE = os.path.join(_DATA_DIR, "ig_token.json")
_REFRESH_AFTER = 30 * 86400      # 30 kundan eski bo'lsa yangilaymiz


def _token_file(uid: str = "") -> str:
    """Asosiy akkaunt — eski `ig_token.json`; qo'shimchalari `ig_token_<id>.json`."""
    if not uid or uid == _cfg("IG_USER_ID"):
        return _TOKEN_FILE
    return os.path.join(_DATA_DIR, f"ig_token_{uid}.json")


def _token_cache(uid: str = "") -> dict:
    try:
        with open(_token_file(uid), encoding="utf-8") as f:
            d = json.load(f)
        return d if d.get("token") else {}
    except Exception:
        return {}


def _token_save(token: str, uid: str = "") -> None:
    path = _token_file(uid)
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"token": token, "saved_at": int(time.time())}, f)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except Exception as e:
        logger.warning("🎬 Instagram tokenini saqlab bo'lmadi: %s", e)


def ig_token(uid: str = "", base: str = "") -> str:
    """Amaldagi token — keshdagisi .env dagisidan ustun."""
    return _token_cache(uid).get("token") or base or _cfg("IG_ACCESS_TOKEN")


def ig_accounts() -> list:
    """[(ig_user_id, token)] — rolik chiqadigan BARCHA Instagram profillar.

    Asosiysi: IG_USER_ID / IG_ACCESS_TOKEN. Qo'shimchasi (2026-10-23,
    @chinors_market): IG2_USER_ID / IG2_ACCESS_TOKEN. Har biri o'z tokenini
    alohida yangilaydi (`ig_token_<id>.json`)."""
    out = []
    uid = _cfg("IG_USER_ID")
    if uid and ig_token():
        out.append((uid, ig_token()))
    for n in (2, 3):
        u = (os.getenv(f"IG{n}_USER_ID") or "").strip()
        t = (os.getenv(f"IG{n}_ACCESS_TOKEN") or "").strip()
        if u and t:
            out.append((u, ig_token(u, t)))
    return out


def refresh_ig_token(uid: str = "", base: str = "") -> bool:
    """Token 30 kundan eski bo'lsa yangilaydi. True = yangilandi."""
    cur = ig_token(uid, base)
    if not cur:
        return False
    c = _token_cache(uid)
    if c.get("token") and (time.time() - int(c.get("saved_at") or 0)) < _REFRESH_AFTER:
        return False
    try:
        r = _get("https://graph.instagram.com/refresh_access_token"
                 "?grant_type=ig_refresh_token&access_token=" + cur)
        new = r.get("access_token")
        if not new:
            logger.warning("🎬 Instagram token yangilanmadi: %s", r)
            return False
        _token_save(new, uid)
        logger.info("🎬 Instagram tokeni yangilandi%s (%s kun)",
                    f" ({uid})" if uid else "", r.get("expires_in", 0) // 86400)
        return True
    except Exception as e:
        logger.warning("🎬 Instagram token yangilashda xato: %s", e)
        return False


def ig_configured() -> bool:
    return bool(_cfg("IG_USER_ID") and ig_token()
                and _cfg("PUBLIC_BASE_URL"))


# ─── Navbat (SQLite) ────────────────────────────────────────────────────────
# Video tayyorlash 20-40 soniya oladi va bir yadroli serverda bir vaqtda
# faqat bittasi ishlashi kerak — shuning uchun navbat.

def _db_path() -> str:
    from database._helpers import DB_PATH
    return DB_PATH


def _conn():
    c = sqlite3.connect(_db_path(), timeout=20)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS reels_queue (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id  INTEGER NOT NULL,
        image_path  TEXT NOT NULL,
        video_path  TEXT DEFAULT '',
        caption     TEXT DEFAULT '',
        status      TEXT DEFAULT 'pending',
        ig_media_id TEXT DEFAULT '',
        error       TEXT DEFAULT '',
        created_at  TEXT DEFAULT '',
        done_at     TEXT DEFAULT ''
    )""")
    # Eski bazaga yangi ustunlar (bir marta qo'shiladi)
    for col, decl in (("attempts", "INTEGER DEFAULT 0"),
                      ("next_try", "TEXT DEFAULT ''"),
                      ("msg_id", "INTEGER DEFAULT 0")):
        try:
            c.execute(f"ALTER TABLE reels_queue ADD COLUMN {col} {decl}")
        except sqlite3.OperationalError:
            pass
    c.commit()
    return c


# KUNIGA NECHTA ROLIK. Har tovarga video yasash — kanalni bir xil
# roliklar bilan to'ldirish va sifatni pasaytirish demak (6900 tovar).
# Kuniga bir-ikkitasi, lekin ARZIYDIGAN tovarga: yangi kelgan yoki
# haqiqatan sotilayotgan. Qolgan tovarlar rasm posti bilan chiqadi.
DAILY_LIMIT = int(os.getenv("REELS_DAILY_LIMIT", "2") or 2)
NEW_DAYS = 3          # shuncha kun ichida qo'shilgan tovar «yangi»
SOLD_DAYS = 30        # shuncha kun ichidagi sotuvlar sanaladi
SOLD_MIN = 2          # kamida shuncha marta sotilgan bo'lsa «ketyapti»


def _today_count(c) -> int:
    """Bugun nechta rolik «o'rin egalladi».

    Yiqilgan (`failed`) va o'tkazib yuborilgan (`skipped`) urinishlar
    SANALMAYDI: ular kanalga hech narsa chiqarmagan, lekin ilgari kunlik
    chegarani to'ldirib, qolgan tovarlarni roliksiz qoldirardi."""
    r = c.execute("SELECT COUNT(*) FROM reels_queue "
                  "WHERE created_at >= date('now') "
                  "AND status NOT IN ('failed','skipped')").fetchone()
    return int((r or [0])[0] or 0)


def worth_reel(product_id: int, c) -> str:
    """Bu tovarga rolik arziydimi. Arzisa sabab, arzimasa '' qaytadi."""
    row = c.execute(
        "SELECT qty, sell_price, created_at FROM products WHERE id=?",
        (product_id,)).fetchone()
    if not row:
        return ""
    qty, price, created = row[0] or 0, row[1] or 0, row[2] or ""
    if qty <= 0 or price <= 0:
        return ""                 # yo'q tovarni yoki narxsizni reklama qilmaymiz
    try:
        r = c.execute("SELECT 1 FROM products WHERE id=? "
                      "AND created_at > datetime('now', ?)",
                      (product_id, "-%d days" % NEW_DAYS)).fetchone()
        if r:
            return "yangi tovar"
    except Exception:
        pass
    try:
        r = c.execute(
            "SELECT COUNT(*) FROM sales WHERE created_at > datetime('now', ?) "
            "AND is_return=0 AND items LIKE ?",
            ("-%d days" % SOLD_DAYS,
             '%"product_id": ' + str(int(product_id)) + ',%')
        ).fetchone()
        if int((r or [0])[0] or 0) >= SOLD_MIN:
            return "sotilayotgan tovar"
    except Exception as e:
        logger.info("🎬 Sotuv tarixi o'qilmadi (#%s): %s", product_id, e)
    return ""


def enqueue(product_id: int, image_path: str) -> int:
    """Navbatga qo'shadi — AGAR bugungi limit to'lmagan va tovar rolikka
    arzisa. Bir tovar uchun kunda bir marta (takror rasm yuklansa bir xil
    rolik ikki marta chiqmasin)."""
    if not product_id or not image_path:
        return 0
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    c = _conn()
    try:
        row = c.execute(
            "SELECT id FROM reels_queue WHERE product_id=? "
            "AND created_at > datetime('now','-1 day')", (product_id,)
        ).fetchone()
        if row:
            return 0
        used = _today_count(c)
        if used >= DAILY_LIMIT:
            logger.info("🎬 Bugungi rolik chegarasi to'ldi (%d/%d) — #%s "
                        "rasm posti bilan chiqadi", used, DAILY_LIMIT, product_id)
            return 0
        why = worth_reel(product_id, c)
        if not why:
            logger.info("🎬 #%s rolikka olinmadi (yangi ham, sotilayotgan "
                        "ham emas) — rasm posti", product_id)
            return 0
        logger.info("🎬 #%s rolik navbatiga qo'shildi (%s, bugun %d/%d)",
                    product_id, why, used + 1, DAILY_LIMIT)
        cur = c.execute(
            "INSERT INTO reels_queue(product_id, image_path, created_at) "
            "VALUES(?,?,?)", (product_id, image_path, now))
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def enqueue_manual(product_id: int, image_path: str) -> int:
    """Egasi O'ZI tanlaganda navbatga qo'shadi.

    `enqueue` dan farqi: `worth_reel` darvozasi va kunlik chegara
    QO'LLANMAYDI — ular avtomatik tanlash uchun. Odam qaysi tovarni
    reklama qilishni o'zi biladi, tizim unga to'sqinlik qilmasligi kerak.
    Faqat takror navbatga tushib qolmasligi tekshiriladi."""
    if not product_id or not image_path:
        return 0
    c = _conn()
    try:
        row = c.execute(
            "SELECT id FROM reels_queue WHERE product_id=? AND status='pending'",
            (product_id,)).fetchone()
        if row:
            return 0
        cur = c.execute(
            "INSERT INTO reels_queue(product_id, image_path, created_at) "
            "VALUES(?,?,?)",
            (product_id, image_path, time.strftime("%Y-%m-%d %H:%M:%S")))
        c.commit()
        logger.info("🎯 #%s qo'lda navbatga qo'shildi", product_id)
        return cur.lastrowid
    finally:
        c.close()


async def offer_after_upload(db, product: dict, image_path: str) -> None:
    """Rasm yuklangach — adminga «rolik chiqaraymi?» degan TUGMA yuboradi.

    Ilgari rolik har rasm yuklanganda O'ZI yasalardi. Bu ikki jihatdan
    noqulay edi: har bir mayda tovarga ham rolik ketardi va u kanalga
    egasi ko'rmasdan chiqib qolardi. Endi qaror egasiniki — bir tugma.
    """
    bot = getattr(db, "_bot", None)
    if not bot or not is_available():
        return
    if not _flag_on(db, "reels_enabled", "1"):
        return
    pid = int(product.get("id") or 0)
    # Yaqinda rolik chiqqan yoki navbatda bo'lsa — takror so'ramaymiz
    if has_pending(pid):
        return
    name = (product.get("display_name") or product.get("name") or "").strip()
    price = float(product.get("sell_price", 0) or 0)
    text = (f"📸 <b>{name}</b> kanalga chiqdi.\n"
            f"💰 {price:,.0f} so'm\n\n"
            f"Shu tovarga reklama roligi kerakmi?").replace(",", " ")
    try:
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🎬 Rolik chiqarish",
                                 callback_data=f"reelmake_{pid}"),
            InlineKeyboardButton(text="⏭ Kerak emas",
                                 callback_data=f"reelskip_{pid}"),
        ]])
    except Exception:
        kb = None
    from bot.config import GLAVNIY_ADMIN_ID
    targets, sent = [GLAVNIY_ADMIN_ID], set()
    try:
        targets += [a.get("telegram_id") for a in await db.get_all_admins()]
    except Exception:
        pass
    for tg in targets:
        if not tg or tg in sent:
            continue
        sent.add(tg)
        try:
            await bot.send_message(tg, text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass


def mark_declined(product_id: int) -> None:
    """«Kerak emas» — shu tovar bir oy davomida qayta taklif qilinmaydi."""
    c = _conn()
    try:
        c.execute(
            "INSERT INTO reels_queue(product_id, image_path, status, "
            "error, created_at) VALUES(?,'','declined','egasi rad etdi',?)",
            (product_id, time.strftime("%Y-%m-%d %H:%M:%S")))
        c.commit()
    finally:
        c.close()


def _set(rid: int, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    c = _conn()
    try:
        c.execute(f"UPDATE reels_queue SET {cols} WHERE id=?",
                  (*fields.values(), rid))
        c.commit()
    finally:
        c.close()


def pending(limit: int = 5) -> list:
    c = _conn()
    try:
        rows = c.execute(
            "SELECT * FROM reels_queue WHERE status='pending' "
            # next_try MAHALLIY vaqtda yoziladi (time.localtime) — 'now' esa UTC;
            # 'localtime'siz qoldirilgan rolik 5 soat ortiqcha kutardi.
            "AND (next_try IS NULL OR next_try='' OR next_try <= datetime('now','localtime')) "
            "ORDER BY id LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def candidates(limit: int = 5, offer_days: int = 30) -> list:
    """Reklamaga ARZIYDIGAN tovarlarni topadi va ball bo'yicha saralaydi.

    Nima uchun kerak: tizim o'zi tanlaganda (`worth_reel`) faqat ikkita
    shart bor edi — «yangi» yoki «sotilayotgan». Do'kon egasi esa qaysi
    tovarni reklama qilishni o'zi biladi. Bu funksiya unga TAYYOR
    ro'yxat beradi: nima uchun arziydi, qancha qoldiq, qancha foyda.

    Yaqinda taklif qilingan yoki rolik chiqqan tovarlar ro'yxatga
    tushmaydi — bir xil tovar qayta-qayta taklif qilinmasin.
    """
    c = _conn()
    try:
        seen = {r[0] for r in c.execute(
            "SELECT DISTINCT product_id FROM reels_queue "
            "WHERE created_at > datetime('now', ?)",
            ("-%d days" % offer_days,)).fetchall()}
        rows = c.execute(
            "SELECT p.id, p.name, p.display_name, p.qty, p.unit, p.sell_price, "
            "       p.cost_price, p.description, p.usage_tags, p.created_at, "
            "       p.image_url, p.image_orig_url, c.name AS cat "
            "FROM products p LEFT JOIN categories c ON c.id = p.category_id "
            "WHERE p.is_active=1 AND p.qty > 0 AND p.sell_price > 0 "
            "  AND (p.image_orig_url != '' OR p.image_url != '')"
        ).fetchall()

        out = []
        for r in rows:
            pid = r["id"]
            if pid in seen:
                continue
            score, why = 0, []
            # Yangi tovar — odamlar ko'rmagan
            try:
                isnew = c.execute(
                    "SELECT 1 FROM products WHERE id=? AND created_at > "
                    "datetime('now','-14 days')", (pid,)).fetchone()
            except Exception:
                isnew = None
            if isnew:
                score += 4
                why.append("yangi")
            # Sotilayotgan — talab bor, reklama kuchaytiradi
            try:
                sold = int((c.execute(
                    "SELECT COUNT(*) FROM sales WHERE created_at > "
                    "datetime('now','-30 days') AND is_return=0 AND items LIKE ?",
                    ('%"product_id": ' + str(int(pid)) + ',%',)
                ).fetchone() or [0])[0] or 0)
            except Exception:
                sold = 0
            if sold:
                score += min(sold * 2, 6)
                why.append("%d marta sotilgan" % sold)
            # Tavsifi va teglari bor — AI yaxshi matn yozadi
            if (r["description"] or "").strip():
                score += 2
            if (r["usage_tags"] or "").strip():
                score += 1
                why.append((r["usage_tags"] or "").split(",")[0].strip())
            # Foyda ulushi — reklama qilishga arziydi
            cost = float(r["cost_price"] or 0)
            price = float(r["sell_price"] or 0)
            if cost > 0 and price > cost:
                marja = (price - cost) / price * 100
                if marja >= 25:
                    score += 2
                    why.append("foyda %.0f%%" % marja)
            # Zaxira yetarli — reklamadan keyin tugab qolmasin
            if float(r["qty"] or 0) >= 5:
                score += 1
            if score < 3:
                continue
            out.append({
                "id": pid,
                "name": (r["display_name"] or r["name"] or "").strip(),
                "qty": float(r["qty"] or 0), "unit": r["unit"] or "dona",
                "price": price, "cat": r["cat"] or "",
                "image": r["image_url"] or r["image_orig_url"] or "",
                "score": score, "why": ", ".join(why[:3]) or "katalogda bor",
            })
        out.sort(key=lambda x: -x["score"])
        return out[:limit]
    finally:
        c.close()


def rotate_files(keep: int = 20, days: int = 14) -> int:
    """Eski rolik va sahna fayllarini o'chiradi.

    Fayllar `/api/reel/` orqali tashqariga beriladi, shuning uchun bir
    necha kun turishi kerak — lekin abadiy emas: kuniga 2 ta rolik ~4 MB,
    ya'ni yiliga 1.5 GB. Oxirgi `keep` ta fayl va `days` kun ichidagilar
    qoladi, qolgani o'chadi."""
    try:
        names = [f for f in os.listdir(REELS_DIR)
                 if f.endswith(".mp4") or f.endswith(".png")]
    except OSError:
        return 0
    paths = []
    for n in names:
        fp = os.path.join(REELS_DIR, n)
        try:
            paths.append((os.path.getmtime(fp), fp))
        except OSError:
            pass
    paths.sort(reverse=True)                 # yangisi birinchi
    cutoff = time.time() - days * 86400
    removed = 0
    for i, (mt, fp) in enumerate(paths):
        if i < keep or mt > cutoff:
            continue
        try:
            os.remove(fp)
            removed += 1
        except OSError:
            pass
    if removed:
        logger.info("🎬 %d ta eski rolik fayli o'chirildi", removed)
    return removed


def has_pending(product_id: int, minutes: int = 30) -> bool:
    """Shu tovar uchun rolik hali kelayaptimi yoki allaqachon chiqqanmi?

    Kanalga rasm posti chiqarish-chiqarmaslik shunga qarab hal qilinadi.

    DIQQAT — ilgari bu funksiya faqat «oxirgi 30 daqiqada yaratilgan»
    qatorlarni sanardi va shu sabab kanalda BIR TOVAR IKKI MARTA chiqib
    qolardi (avval video, keyin rasm posti). Sababi: rolik matn yoki rasm
    tanish yiqilganda 15-30 daqiqaga suriladi (uch martagacha, ya'ni
    bir soatdan oshishi mumkin), 30 daqiqalik oyna esa allaqachon
    yopilgan bo'lardi va navbatdagi «Saqlash» rasm postini yuborardi.

    Endi vaqtga emas, HOLATGA qaraymiz:
      • pending — hali chiqmagan (kechiktirilgan bo'lsa ham) → kutamiz;
      • channel/posted — chiqib bo'lgan → sutka ichida takrorlamaymiz;
      • failed/skipped/review — kanalga chiqmaydi → rasm posti kerak.
    """
    c = _conn()
    try:
        r = c.execute(
            "SELECT 1 FROM reels_queue WHERE product_id=? AND ("
            "  status='pending'"
            "  OR (status IN ('channel','posted') "
            "      AND created_at > datetime('now','-1 day'))"
            ") LIMIT 1", (product_id,)).fetchone()
        return bool(r)
    except Exception:
        return False
    finally:
        c.close()


def stats() -> dict:
    c = _conn()
    try:
        out = {}
        for r in c.execute("SELECT status, COUNT(*) n FROM reels_queue "
                           "GROUP BY status").fetchall():
            out[r["status"]] = r["n"]
        return out
    finally:
        c.close()


# ─── Yordamchilar (matn, gradient) ──────────────────────────────────────────

def _font(size: int, bold: bool = True):
    from bot.imageproc import _font as _f
    return _f(size, bold)


def _gradient(w: int, h: int, top, bottom):
    from PIL import Image
    base = Image.new("RGB", (1, h))
    for y in range(h):
        k = y / max(1, h - 1)
        base.putpixel((0, y), tuple(
            int(top[i] + (bottom[i] - top[i]) * k) for i in range(3)))
    return base.resize((w, h), Image.BILINEAR)


def _wrap(draw, text: str, font, max_w: int, max_lines: int = 2) -> list:
    words, lines, cur = (text or "").split(), [], ""
    for w_ in words:
        t = (cur + " " + w_).strip()
        if draw.textlength(t, font=font) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w_
            if len(lines) == max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines and draw.textlength(lines[-1], font=font) > max_w:
        while lines[-1] and draw.textlength(lines[-1] + "…", font=font) > max_w:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    return lines


# ─── Reklama matni (AI) ─────────────────────────────────────────────────────
# Video faqat chiroyli ko'rinishi yetarli emas — ko'rgan odam TOVAR NIMA
# EKANINI va nimaga kerakligini bilishi kerak. Shu sabab har rolik uchun
# qisqa ssenariy yoziladi: ilgak (hook) + uchta foyda.

_AD_SYS = (
    "Sen — qisqa video reklama (Reels) ssenariysini yozadigan "
    "kopiraytersan. Buyurtmachi: Toshkent, Sergeli tumanidagi «Chinor» "
    "qurilish va uy-ro'zg'or do'koni.\n\n"
    "MAQSAD: odam lentani aylantirib ketayotganda TO'XTAB qolsin, "
    "tovarni tushunsin va do'konga buyurtma bersin. Rolik 13 soniya, "
    "ovozsiz ham tushunarli bo'lishi kerak.\n\n"
    "KIM KO'RADI: mahalliy xaridorlar — ustalar, ta'mir qilayotgan "
    "oila boshliqlari, uy bekalari. Ular pulni sanaydi, quruq maqtovga "
    "ishonmaydi, lekin ANIQ foydani darrov tushunadi.\n\n"
    "TIL: sof o'zbek tili, LOTIN yozuvida, oddiy kundalik so'zlar. "
    "Turkcha harflar (ş, ç, ğ, ı, ü, ö) va kirill ISHLATMA. "
    "Rasmiy yoki kitobiy uslub emas — do'kondagi sotuvchi qanday "
    "gapirsa, shunday yoz."
)


_FACTS_PROMPT = (
    "Rasmda qanday tovar bor? Quyidagilarni AYT:\n"
    "1) Bu aniq nima (turi, materiali, o'lchami ko'rinsa);\n"
    "2) Nima uchun ishlatiladi — qanday ishni bajaradi;\n"
    "3) Ishlatgandan keyin qanday natija bo'ladi.\n"
    "3-4 gap, sof o'zbek tilida, lotin yozuvida. Maqtov va reklama "
    "so'zlari kerak emas — faqat aniq ma'lumot."
)


def product_facts(product: dict, image_path: str, cat: str) -> str:
    """Tovar HAQIQATDA nima ekanini rasmdan o'rganadi.

    Nima uchun kerak: ilgari reklama matni faqat NOM va BO'LIM asosida
    yozilardi. Nom qisqartma bo'lsa («AERAZOL Lak», «ACC Velux») yoki
    bo'lim xato bo'lsa, model taxmin qilib bema'ni gap yozardi — lakka
    «Sirt yumshaydi» deb. Rasmni ko'rgan model esa bu purkagich lak
    ekanini biladi. Odam Gemini chatiga rasm tashlaganda ham shu bo'ladi.
    """
    if not image_path or not os.path.exists(image_path):
        return ""
    name = (product.get("display_name") or product.get("name") or "").strip()
    try:
        from bot import ai_engine
        out = ai_engine.describe(
            image_path,
            _FACTS_PROMPT + (f"\n\nTovar nomi: {name}" if name else "")
            + (f"\nBo'lim: {cat}" if cat else ""),
            product_name=name,
            validate=lambda t: len(t.strip()) > 40 and "\n1)" not in t[:5],
            allow_text_fallback=False)
        out = " ".join((out or "").split())
        return out[:700]
    except Exception as e:
        logger.info("🎬 Tovar rasmdan o'rganilmadi: %s", e)
        return ""


def _ad_prompt(name: str, cat: str, tags: list, desc: str,
               facts: str = "") -> str:
    """Ssenariy so'rovi.

    Nima uchun bunchalik uzun: qisqa so'rovga («shu tovarga reklama yoz»)
    model tovarni TAXMIN qilib javob berardi va bema'ni gap chiqardi.
    Bu yerda unga vazifa, auditoriya, psixologik ilgak turlari va aniq
    namunalar beriladi — odam Gemini bilan suhbatda qanday tushuntirsa,
    shunday.
    """
    return (
        "== TOVAR HAQIDA ==\n"
        f"Nomi: {name}\n"
        f"Bo'lim: {cat or '—'}\n"
        f"Qayerda ishlatiladi: {', '.join(tags) if tags else '—'}\n"
        f"Do'kon tavsifi: {desc[:300] or '—'}\n"
        + (f"RASMDAN KO'RINGANI (eng ishonchli manba): {facts}\n" if facts else "")
        + "\n== VAZIFA ==\n"
        "Shu tovar uchun 13 soniyalik Reels ssenariysining MATNINI yoz:\n"
        "bitta ILGAK (hook) va uchta FOYDA.\n\n"
        "== ILGAK QANDAY BO'LADI ==\n"
        "Ilgak — birinchi 1.5 soniyada ekranda chiqadigan katta yozuv. "
        "Uning yagona vazifasi — odamni to'xtatish. Quyidagi usullardan "
        "SHU TOVARGA eng mosini tanla:\n"
        "  • Charchoqqa murojaat: «Qiyin ketadigan yog'dan charchadingizmi?»\n"
        "  • Muammoni tan oldirish: «Kran tomchilayaptimi?»\n"
        "  • Tanish achchiq holat: «Bo'yoq yana qatlam-qatlam tushdimi?»\n"
        "  • Kutilmagan foyda: «Bir qopqoq — butun kir»\n"
        "  • Vaqt tejash: «5 daqiqada tayyor»\n\n"
        "ILGAK QOIDALARI:\n"
        "  • Odamga TO'G'RIDAN murojaat qil («siz», «-dingizmi»).\n"
        "  • To'liq, ommabop jumla bo'lsin. Sheva, qisqartma va g'aliz "
        "so'z ISHLATMA («Idishlar chiriydimi?» — yomon, hech kim "
        "bunday demaydi).\n"
        "  • 34 belgigacha — ekranga ikki qatorda sig'sin.\n"
        "  • Tovar nomini takrorlama — u pastda alohida chiqadi.\n\n"
        "== FOYDA QATORLARI QANDAY BO'LADI ==\n"
        "  • Har biri — KO'RINADIGAN natija: nima o'zgaradi, nima yaxshi "
        "bo'ladi. «Sifatli», «ishonchli», «zamonaviy» kabi quruq sifatlar "
        "TAQIQLANADI — ular hech narsa demaydi.\n"
        "  • Iloji bo'lsa aniq: «2 daqiqada quriydi», «yog'ni eritadi».\n"
        "  • Bilmagan narsangni YOZMA. Faqat yuqoridagi ma'lumotdan "
        "kelib chiqadigan gapni yoz.\n"
        "  • Har biri 30 belgidan oshmasin — ekranda 1.9 soniya turadi, "
        "odam o'qib ulgurishi kerak.\n"
        "  • Fe'l bilan tugasin: «ketadi», «quriydi», «saqlaydi».\n\n"
        "== NAMUNA (kir yumshatgich) ==\n"
        "HOOK: Kiyimlar qattiq bo'lib qoldimi?\n"
        "1: Mato yumshaydi\n"
        "2: Hidi uzoq turadi\n"
        "3: Dazmol oson yuradi\n\n"
        "== NAMUNA (yog'och arra) ==\n"
        "HOOK: Arra taxtada tishlab qolyaptimi?\n"
        "1: Tekis kesadi\n"
        "2: Kuch talab qilmaydi\n"
        "3: Tishi tez o'tmaydi\n\n"
        "== JAVOB SHAKLI ==\n"
        "Faqat shu to'rt qator, boshqa hech narsa yozma:\n"
        "HOOK: <...>\n1: <...>\n2: <...>\n3: <...>"
    )


_TURKISH_CH = "şŞğĞıİçÇöÖüÜ"


def _ad_ok(text: str) -> bool:
    """Javob shakli va tili to'g'rimi (kuchsiz modellar aljiydi)."""
    t = (text or "").strip()
    if len(t) < 25 or len(t) > 600:
        return False
    if any(ch in t for ch in _TURKISH_CH):
        return False
    if sum(1 for ch in t if "Ѐ" <= ch <= "ӿ") > 3:      # kirill
        return False
    low = t.lower()
    if "hook:" not in low:
        return False
    return sum(1 for n in ("1:", "2:", "3:") if n in t) >= 2


def _parse_ad(raw: str) -> dict:
    hook, bullets = "", []
    for line in (raw or "").splitlines():
        line = line.strip().strip("*").strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("hook:"):
            hook = line.split(":", 1)[1].strip()
            if len(hook) > 34:      # so'z o'rtasidan kesmaymiz
                hook = hook[:34].rsplit(" ", 1)[0]
        elif line[:2] in ("1:", "2:", "3:") or line[:3] in ("1. ", "2. ", "3. "):
            b = line.split(":", 1)[-1] if ":" in line[:3] else line[2:]
            b = b.strip()
            # So'z o'rtasidan kesish yomon ko'rinadi («…paydo bo'lad»),
            # shuning uchun oxirgi to'liq so'zgacha qisqartiramiz.
            if len(b) > 30:
                b = b[:30].rsplit(" ", 1)[0]
            if b:
                bullets.append(b)
    return {"hook": hook, "bullets": bullets[:3]}


# O'zbekchada foyda iborasi deyarli doim fe'l bilan tugaydi:
# «yumshaydi», «ishlaydi», «o'rnatiladi», «ketadi». Bema'ni javoblar esa
# ot bilan tugaydi: «Matolarning sirpanishlari», «Tozalashlarning hidi».
_VERB_END = ("adi", "aydi", "ydi", "iladi", "uladi", "ladi", "nadi",
             "tadi", "radi", "maydi", "aymiz", "amiz")


# Reklamada ishlatilmaydigan shakllar: istak/buyruq mayli («bo'lay»,
# «qilsin»), bo'lishsizlik so'roqlari. Bittasi chiqsa — matn shubhali.
_BAD_END = ("lay", "may", "sin", "sun", "aylik", "amiz?", "gin", "gil")


def _bullets_sane(bullets: list) -> bool:
    """HAR BIR qator haqiqiy foyda iborasimi.

    Ilgari «uchtadan ikkitasi yetadi» edi va kanalga «O'yini sifatli
    bo'lay» kabi ma'nosiz qator chiqib ketdi (2026-08, AERAZOL Lak).
    Endi bitta qator ham shubhali bo'lsa — butun matn rad etiladi."""
    if len(bullets) < 2:
        return False
    for b in bullets:
        w = (b or "").strip().lower().rstrip("!.,?")
        if len(w) < 10 or " " not in w:
            return False
        # Takrorlangan o'zak — model «gapirib ketgan» belgisi:
        # «Uzok, uzoq vaqtdan saqlaydi» (2026-08).
        words = [x.strip(",.;:") for x in w.split()]
        stems = [x[:4] for x in words if len(x) >= 4]
        if len(stems) != len(set(stems)):
            return False
        if w.endswith(_BAD_END):
            return False
        if not w.endswith(_VERB_END):
            return False
    return True


# Kichik modellar (8B va past) o'zbekcha reklama matnini UDDALAY OLMAYDI:
# grammatikasi to'g'ri, lekin ma'nosi tovarga aloqasiz chiqadi — granit
# kazan uchun «Mikser zanglanmaydi» deb yozdi (2026-08). Shakl tekshiruvi
# buni ushlamaydi, shuning uchun bunday modellarning javobi umuman qabul
# qilinmaydi: rolik keyinga suriladi va kuchli model bo'shaganda yoziladi.
_WEAK_MODEL = ("mini", "tiny", "small", "instant", "lite", "nano")


def _model_trusted(tag: str) -> bool:
    """Model reklama matni yozishga yetarlicha kuchlimi.

    DIQQAT — bu yerda oddiy `in` tekshiruvi TURGAN va u «ge-MINI-ni» ham
    kuchsiz deb belgilardi: 2026-08 da Groq modellari o'lgach zanjirda
    yagona ishlaydigan model Gemini qolgan edi, natijada HAR BIR rolik
    zaxira matn bilan chiqardi («Oshxona uchun mos»). Endi nom BO'LAKLARGA
    bo'lib solishtiriladi va o'lcham (7b/8b) alohida qaraladi.
    """
    t = (tag or "").lower()
    if not t:
        return False
    for part in re.split(r"[^a-z0-9.]+", t.split(":", 1)[-1]):
        if part in _WEAK_MODEL:
            return False
        m = re.fullmatch(r"(\d+(?:\.\d+)?)b", part)
        if m and float(m.group(1)) < 20:      # 20 milliarddan kichik model
            return False
    return True


_CHECK_SYS = (
    "Sen o'zbek tili muharrirsan. Reklama matnidagi xatolarni topib "
    "tuzatasan. Faqat sof o'zbek tili, lotin yozuvi."
)


def _recheck(name: str, facts: str, hook: str, bullets: list) -> dict:
    """Yozilgan matnni AI ning O'ZIGA qayta ko'rsatib tekshirtiradi.

    Nima uchun kerak: model bir o'qishda «Sirt yumshaydi» (bo'yoq uchun)
    yoki «bo'ytasiz» kabi mavjud bo'lmagan so'zni yozib yuboradi, lekin
    o'sha matnni QAYTA o'qib chiqishga qo'yilsa — xatoni o'zi ko'radi.
    Bu ikkinchi qarash tekin va sifatni sezilarli ko'taradi.
    """
    try:
        from bot import ai_engine
        req = (
            f"Tovar: {name}\n"
            f"Tovar haqida aniq ma'lumot: {facts[:400] or '—'}\n\n"
            "Quyidagi reklama matnini tekshir:\n"
            f"HOOK: {hook}\n"
            + "".join(f"{i+1}: {b}\n" for i, b in enumerate(bullets))
            + "\nHar bir qatorni shu mezonlar bo'yicha tekshir:\n"
            "  1) O'zbek tilida TO'G'RI yozilganmi? Mavjud bo'lmagan so'z "
            "bormi («bo'ytasiz», «qirilibdi» kabi)?\n"
            "  1a) HOOK odamlar kundalik hayotda AYTADIGAN gapmi? "
            "Narsaga mos fe'l tanlanganmi? (qozon «chirimaydi» — "
            "qorayadi yoki kuyadi; kiyim «buzilmaydi» — qattiqlashadi)\n"
            "  2) Ma'nosi shu tovarga TO'G'RI kelaymi? (masalan bo'yoq "
            "sirtni yumshatmaydi — qattiq va yaltiroq qiladi)\n"
            "  3) Odam o'qiganda darrov tushunadimi?\n\n"
            "Xato qatorlarni TUZAT, to'g'rilarini o'zgartirmasdan qoldir. "
            "Javobni aynan shu shaklda ber, boshqa hech narsa yozma:\n"
            "HOOK: <...>\n1: <...>\n2: <...>\n3: <...>"
        )
        raw = ai_engine.chat(req, system=_CHECK_SYS, validate=_ad_ok)
        got = _parse_ad(raw)
        if got["hook"] and len(got["bullets"]) >= 2 and _bullets_sane(got["bullets"]):
            return got
    except Exception as e:
        logger.info("🎬 Matn qayta tekshirilmadi: %s", e)
    return {}


def ad_script(product: dict, cat: str, tags: list, facts: str = "") -> dict:
    """Rolik ssenariysi. AI ishlamasa — tovar ma'lumotidan yig'iladi,
    ya'ni video baribir "bu nima" degan savolga javob beradi."""
    name = (product.get("display_name") or product.get("name") or "").strip()
    desc = (product.get("description") or "").strip()
    # Ikki urinish: birinchisi chalkashsa, qattiqroq ko'rsatma bilan yana
    # so'raymiz — zanjir odatda boshqa modelga tushadi va to'g'ri javob
    # beradi. Faqat ikkalasi ham yiqilsa zaxira matnga o'tamiz.
    for attempt in (1, 2):
        try:
            from bot import ai_engine
            extra = ("" if attempt == 1 else
                     "\n\nDIQQAT: oldingi javob noto'g'ri edi. Har bir foyda "
                     "ANIQ shu tovar nima qilishini aytsin va FE'L bilan "
                     "tugasin (masalan «tez quriydi», «zangdan saqlaydi»).")
            # Do'kon profili — AI kimga va nima uchun yozayotganini
            # bilishi uchun (bir joydan, `shop_profile` dan).
            try:
                from bot import shop_profile
                from database.channel_db import db as _db
                shop_text = shop_profile.text_sync(_db)
            except Exception:
                shop_text = ""
            raw = ai_engine.chat(
                _ad_prompt(name, cat, tags, desc, facts) + extra,
                system=_AD_SYS + ("\n\n" + shop_text if shop_text else ""),
                validate=_ad_ok)
            # Javobni QAYSI model yozgani — zanjir holatidan o'qiymiz
            model = ""
            try:
                model = ai_engine._load_state().get("last_good_text", "")
            except Exception:
                pass
            got = _parse_ad(raw)
            if got["hook"] and len(got["bullets"]) >= 2:
                if not _model_trusted(model):
                    logger.info("🎬 Matn kuchsiz modeldan (%s) — qabul "
                                "qilinmadi", model)
                elif _bullets_sane(got["bullets"]):
                    # Ikkinchi qarash: model o'z matnini qayta o'qib,
                    # ma'no va til xatolarini tuzatadi.
                    fixed = _recheck(name, facts, got["hook"], got["bullets"])
                    if fixed:
                        if (fixed["hook"] != got["hook"]
                                or fixed["bullets"] != got["bullets"]):
                            logger.info("🎬 Matn tuzatildi: %s → %s",
                                        got["bullets"], fixed["bullets"])
                        got = fixed
                    got["source"] = "ai"
                    got["model"] = model
                    return got
                # Model chalkashgan (2026-08: «AERAZOL Lak» uchun
                # «Matolarning sirpanishlari» chiqqan) — bunday matn
                # kanalga chiqmasin.
                logger.info("🎬 AI matni ishonarsiz (%s-urinish): %s",
                            attempt, got["bullets"])
        except Exception as e:
            logger.info("🎬 AI ssenariy yozmadi (%s-urinish): %s", attempt, e)

    # Zaxira: AI yo'q yoki chalkashgan bo'lsa ham matn mazmunli qolsin
    hook = (cat or name)[:34]
    bullets = []
    for t in (tags or [])[:2]:
        bullets.append(f"{t[:18]} uchun mos")
    # Tavsifning o'zbekcha qatoridan foyda iboralarini ajratamiz —
    # bu matn tovar haqida yozilgan, ya'ni «uchun mos» dan mazmunliroq.
    for line in desc.splitlines():
        if "🇺🇿" not in line:
            continue
        part = line.replace("🇺🇿", "").strip(" -–—")
        for sep in (" - ", " — ", ":"):
            if sep in part:
                part = part.split(sep, 1)[1]
                break
        added = 0
        for chunk in part.replace(";", ",").replace("!", ",").split(","):
            chunk = " ".join(chunk.split()).strip(" .")
            if 10 <= len(chunk) <= 26:
                bullets.insert(0, chunk)
                added += 1
            elif len(chunk) > 26 and not added:
                # Butun ibora uzun bo'lsa — oxirgi to'liq so'zgacha kesamiz
                # («Har qanday sirt uchun sifatli bo'yoqlar» → «Har qanday
                # sirt uchun»). Bu «uchun mos» dan ancha mazmunli.
                bullets.insert(0, chunk[:26].rsplit(" ", 1)[0])
                added += 1
            if added >= 2:
                break
        break
    bullets.append("Yetkazib beramiz")
    return {"hook": hook[:34], "bullets": bullets[:3], "source": "fallback"}


# ─── Sahna rasmlari (tovar ISH USTIDA) ──────────────────────────────────────
# Matn bilan "yumshoq qiladi" deb aytish yetarli emas — buni KO'RSATISH
# kerak. Tovarning o'zi bitta rasmda qotib turadi, shuning uchun uning
# ishlatilish muhitini AI chizadi (Cloudflare Workers AI, flux-1-schnell:
# bitta rasm ~2 soniya, kalit allaqachon `.env` da).
#
# DIQQAT: sahnada TOVARNING O'ZI chizilmaydi — faqat muhit (kir yuvilgan
# choyshab, yangi bo'yalgan devor, ustaxona). Aks holda AI boshqa idish/
# yorliq chizib qo'yadi va bu mijozni chalg'itadi.

_SCENE_SYS = (
    "You are a director of short product ads. You write prompts for a "
    "text-to-image model. Your shots must make the viewer FEEL the "
    "result of using the product, so they want to buy it."
)


def _scene_prompt_req(name: str, cat: str, tags: list, facts: str) -> str:
    """Sahna so'rovi.

    Model nima uchun rasm chizayotganini bilishi kerak: bu — sotuvchi
    rolikning o'rtasi. Odam tovarni ISH USTIDA ko'rib, «menga ham
    kerak» deb o'ylashi kerak. Shu sabab so'rovda maqsad, ketma-ketlik
    va qat'iy taqiqlar bor.
    """
    return (
        "== CONTEXT ==\n"
        "These images are the middle of a 13-second product ad for a "
        "hardware and household store in Tashkent. Between the shots the "
        "real product photo is shown. The viewer must understand what "
        "the product does and want the same result at home.\n\n"
        "== PRODUCT ==\n"
        f"Name: {name}\nCategory: {cat or '-'}\n"
        f"Used for: {', '.join(tags) if tags else '-'}\n"
        f"What it actually is: {facts[:400] or '-'}\n\n"
        "== TASK ==\n"
        "Write 3 image prompts — a BEFORE / DURING / AFTER story. The "
        "first and the third are later shown SIDE BY SIDE, so they must "
        "be the same scene, same angle, same framing — only the state "
        "differs:\n"
        "  1) BEFORE: the problem, at its worst — dirty, dull, broken, "
        "worn out. No hands yet.\n"
        "  2) DURING: hands doing the work, half done, the change "
        "already visible in the frame.\n"
        "  3) AFTER: exactly the same view as (1), but fixed — clean, "
        "shiny, new. Satisfying.\n\n"
        "== SHOT RULES ==\n"
        "  • Close-up, human hands doing the work, real motion.\n"
        "  • Ordinary Uzbek home or building site, natural daylight.\n"
        "  • Photorealistic, shallow depth of field, no studio look.\n"
        "  • Each prompt 12-25 words, ENGLISH ONLY — never leave Uzbek "
        "or Russian words in it (the image model does not know them; "
        "\u00abdirty qozon\u00bb gives a random picture).\n"
        "  • Name the object plainly: cooking pot, wall, tap, plank.\n\n"
        "== FORBIDDEN (breaks the ad) ==\n"
        "  • Any bottle, can, box, jar, tube or packaging — the real "
        "product is shown separately; a drawn one looks like a "
        "competitor's brand.\n"
        "  • Any finished device, fixture, tool or appliance of the SAME "
        "KIND as the product (e.g. do not draw a lamp/light fixture for "
        "a lighting product, a faucet for a tap, a drill for a power "
        "tool) — the real product photo is pasted on top of this scene "
        "afterwards, so a different-looking one drawn underneath reads "
        "as two different products in one shot. Show only hands, "
        "surfaces, materials and the RESULT of the action (a glow of "
        "light spilling onto a wall, running water, a clean surface) — "
        "never the object performing it.\n"
        "  • Any text, label, logo or watermark in the image. Never "
        "write that rule INTO the prompt («no text», «no logo», "
        "«unbranded»): the image model has no negation and such words "
        "make it draw lettering. Describe surfaces as plain and smooth "
        "instead.\n"
        "  • Faces, people looking at camera.\n"
        "  • The product name or brand name anywhere in the prompt.\n"
        "  • Drifting to unrelated subjects (food, plants, animals) "
        "unless the product is truly for them.\n\n"
        "== ANSWER FORMAT ==\n"
        "Exactly three lines, nothing else:\n"
        "1: <prompt>\n2: <prompt>\n3: <prompt>"
    )


# Rasm modeliga BORADIGAN matnda "no text", "without labels" kabi iboralar
# qolmasligi kerak — modelda inkor yo'q, u bu so'zlarni chizishga buyruq deb
# oladi. Matn yozuvchi model ba'zan ularni tavsifga qo'shib yuboradi, shuning
# uchun jo'natishdan oldin bo'lakma-bo'lak tozalanadi.
_NEG_WORDS = ("text", "logo", "label", "brand", "watermark", "sign",
              "signage", "letter", "typograph", "writing", "word",
              "caption", "packaging")


def _strip_negations(prompt: str) -> str:
    out = []
    for part in prompt.split(","):
        low = part.strip().lower()
        if low.startswith(("no ", "not ", "without ", "avoid ")) \
                or any(w in low for w in _NEG_WORDS):
            continue
        if part.strip():
            out.append(part.strip())
    return ", ".join(out)


def scene_prompts(product: dict, cat: str, tags: list, facts: str = "") -> list:
    """Uchta sahna uchun ingliz tilidagi tavsif (rasm modeli uchun).
    AI bo'lmasa — bo'lim bo'yicha oldindan yozilgan zaxira."""
    name = (product.get("display_name") or product.get("name") or "").strip()
    desc = (product.get("description") or "").strip()
    try:
        from bot import ai_engine
        raw = ai_engine.chat(_scene_prompt_req(name, cat, tags,
                                              (facts or desc)),
                             system=_SCENE_SYS,
                             validate=lambda t: "1:" in t and "2:" in t and "3:" in t)
        out = []
        for line in (raw or "").splitlines():
            line = line.strip()
            if line[:2] in ("1:", "2:", "3:"):
                p = line.split(":", 1)[1].strip()
                # Tovar nomi tavsifga tushib qolsa — olib tashlaymiz, aks
                # holda rasm modeli uni HARF bo'lib rasmga yozib qo'yadi
                # («Hands pouring ACC Velux…» shunday chiqqan edi).
                for w in name.split():
                    if len(w) > 2:
                        p = p.replace(w, "").replace(w.lower(), "")
                p = _strip_negations(" ".join(p.replace(" ,", ",").split()))
                # Juda qisqa tavsif ("Dirty pot, dull.") umumiy rasm
                # beradi — kamida bir necha so'z bo'lsin.
                if 25 <= len(p) <= 220 and len(p.split()) >= 5:
                    out.append(p)
        if len(out) >= 2:
            return out[:3]
    except Exception as e:
        logger.info("🎬 Sahna tavsifi yozilmadi (%s) — zaxira", e)
    low = f"{cat} {' '.join(tags or [])}".lower()
    if any(w in low for w in ("kir", "tozal", "yuv")):
        base = ["close-up of a stained dirty shirt on a table, grey and dull, daylight",
                "hands rubbing soapy foam into the stained fabric, water running, close-up",
                "the same shirt on the same table, now bright white and clean, daylight"]
    elif any(w in low for w in ("bo'yoq", "boyoq", "lak", "devor")):
        base = ["close-up of an old scuffed dull wall with peeling paint, daylight",
                "hand rolling fresh paint onto that wall, half painted, natural light",
                "the same wall fully painted, smooth and even, bright sunlit room"]
    elif any(w in low for w in ("santex", "quvur", "kran", "suv")):
        base = ["close-up of a rusty leaking pipe joint under a sink, water dripping, daylight",
                "hands wrapping sealing tape around a pipe thread, tools nearby, close-up",
                "the same pipe joint dry and tightly sealed, clean bright bathroom"]
    elif any(w in low for w in ("elektr", "asbob", "qurilish", "mahkam")):
        base = ["close-up of a bare unfinished wooden plank on a cluttered workbench, daylight",
                "builder hands measuring and marking the board with a pencil, bright workshop",
                "finished wooden shelf mounted on a tidy wall, warm daylight"]
    else:
        base = ["close-up hands using a household item at home, natural light, motion",
                "work in progress on a kitchen table, hands busy, warm daylight",
                "tidy bright uzbek home interior, warm daylight through the window"]
    return base


# Sahna rasmida BEGONA YOZUV bo'lmasligi kerak. Rasm modeli asbob korpusiga
# yoki qurilma yorlig'iga o'zicha "harf"lar chizadi — ular hech qaysi tilda
# so'z emas ("UCEN", "100W IP6S"), va rolikni ko'rgan odam buni bizning
# yozuvimiz deb o'ylaydi. Ikki qatlamli himoya:
#   1) so'rovda INKOR ishlatilmaydi (pastdagi izohga qarang);
#   2) chizilgan rasm ko'ruvchi model bilan tekshiriladi — yozuv topilsa
#      sahna qaytadan chiziladi, baribir chiqsa — sahnasiz ketaveradi.

# «no text, no logo» kabi INKORLAR flux-1-schnell'da ISHLAMAYDI: modelda
# negative prompt yo'q, matn butunligicha tavsif deb o'qiladi va "text",
# "label", "logo", "brand" so'zlari aynan yozuv chizilishini KUCHAYTIRADI.
# Shuning uchun endi faqat IJOBIY tavsif beriladi: yozuvsiz sirtni
# "silliq, bo'yalgan, yaltiroq" deb ta'riflash kerak.
_SCENE_STYLE = (", photorealistic, natural daylight, shallow depth of field, "
                "smooth clean unmarked surfaces, plain painted metal and "
                "plastic, blank matte finish")

# Ikkinchi urinishda sahna soddalashtiriladi — yozuv ko'pincha murakkab
# qurilma korpusida paydo bo'ladi.
_SCENE_RETRY = (", extreme close-up on the hands and the material, very "
                "blurred simple background, bare surfaces")

_TEXT_GATE = ("Rasmda o'qishga uriniladigan HARF, SO'Z, RAQAM, yorliq yoki "
              "logotip bormi? Faqat bitta so'z bilan javob ber: HA yoki YOQ.")


def _scene_has_text(path: str) -> bool:
    """Sahnada begona yozuv bormi. Tekshirib bo'lmasa False (sahna qoladi).

    Ko'ruvchi model chaqiriladi — bir sahnaga bitta qisqa so'rov. Model
    yiqilsa yoki javob tushunarsiz bo'lsa sahna YO'QOTILMAYDI: yozuvli
    sahna yomon, sahnasiz rolik esa undan ham zerikarli.
    """
    try:
        from bot import ai_engine
        out = ai_engine.describe(path, _TEXT_GATE,
                                 validate=lambda t: len(t.strip()) <= 80,
                                 allow_text_fallback=False)
    except Exception as e:
        logger.info("🎬 Sahna yozuvga tekshirilmadi: %s", e)
        return False
    # Model o'zbekcha «Ha», ba'zan inglizcha «Yes» yoki ruscha «Да» deydi
    ans = "".join(ch for ch in (out or "").lower() if ch.isalpha())[:6]
    return ans.startswith(("ha", "yes", "да"))


def _gen_scene_once(prompt: str, out_path: str, timeout: int) -> bool:
    """Bitta rasm chizadi (Cloudflare Workers AI).

    DIQQAT (sinab ko'rilgan va RAD ETILGAN g'oya): flux-1-schnell har doim
    1024x1024 KVADRAT qaytaradi, rolik esa 1080x1920 TIK — shu sabab sahna
    "cover" bilan kadrni to'ldirishga ~2 barobar kattalashtirilib, fon
    xira chiqadi. Buni SDXL'ga (kengroq width/height qabul qiladi)
    o'tkazib to'g'irlamoqchi bo'ldik, lekin production'ga chiqarishdan
    OLDIN sinovda SDXL portret o'lchamda (masalan 768x1344) qop-qora
    rasm qaytardi, 896x1152'da esa 2 daqiqada 408 timeout berdi — bitta
    sahna uchun shuncha kutish (3 sahna = 5-6 daqiqa) amaliy emas.
    Xulosa: flux-1-schnell'da qoldik, xiralik esa PASTDA (`self.scenes`,
    __init__) unsharp-mask bilan yumshatiladi — chin qaror emas, lekin
    xavfsiz va tekshirilgan.
    """
    acc = os.getenv("CF_ACCOUNT_ID", "").strip()
    key = (os.getenv("CF_API_TOKEN", "") or "").split(",")[0].strip()
    if not acc or not key:
        return False
    body = json.dumps({"prompt": prompt[:1900]}).encode()
    url = (f"https://api.cloudflare.com/client/v4/accounts/{acc}"
           f"/ai/run/@cf/black-forest-labs/flux-1-schnell")
    try:
        req = urllib.request.Request(
            url, data=body,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = r.headers.get("Content-Type", "")
            raw = r.read()
        if "json" in ctype:
            import base64 as _b64
            img = (json.loads(raw).get("result") or {}).get("image")
            if not img:
                return False
            raw = _b64.b64decode(img)
        with open(out_path, "wb") as f:
            f.write(raw)
        return os.path.getsize(out_path) > 5000
    except Exception as e:
        logger.info("🎬 Sahna rasmi chizilmadi: %s", e)
        return False


def gen_scene(prompt: str, out_path: str, timeout: int = 120,
              attempts: int = 2) -> bool:
    """Sahna rasmi — yozuvsiz. Chiqmasa False (rolik shu sahnasiz ketadi)."""
    for i in range(max(1, attempts)):
        req = prompt + _SCENE_STYLE + (_SCENE_RETRY if i else "")
        if not _gen_scene_once(req, out_path, timeout):
            return False
        if not _scene_has_text(out_path):
            return True
        logger.info("🎬 Sahnada begona yozuv — qayta chiziladi (%d/%d)",
                    i + 1, max(1, attempts))
    try:
        os.remove(out_path)
    except OSError:
        pass
    return False


# ─── Animatsiya: kadrma-kadr chizish (Pillow → ffmpeg) ──────────────────────
# Rolik REKLAMA bo'lishi kerak, ya'ni ko'rgan odam tovar nima ekanini va
# nimaga kerakligini bilib olsin. Shuning uchun 5 sahna:
#   0.0-1.8  ilgak: muammo savoli, mahsulot pastdan chiqadi
#   1.8-3.4  tanishtirish: nomi + bu nima ekani
#   3.4-8.6  uchta foyda — navbat bilan yonadi
#   8.6-12.0 narx «muhr» bo'lib tushadi + chaqiruv paneli


def _ease_out_back(x: float, s: float = 1.9) -> float:
    x = max(0.0, min(1.0, x))
    return 1 + (s + 1) * (x - 1) ** 3 + s * (x - 1) ** 2


def _ease_out(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def _seg(t: float, start: float, dur: float) -> float:
    """[start, start+dur] oralig'ini 0..1 ga aylantiradi."""
    if dur <= 0:
        return 1.0
    return max(0.0, min(1.0, (t - start) / dur))


def _strip_emoji(s: str) -> str:
    """Emoji va boshqa chizilmaydigan belgilarni olib tashlaydi.

    Serverda emoji shrifti YO'Q (faqat DejaVu/Noto Mono) — emoji qoldirilsa
    ekranda bo'sh kvadrat bo'lib chiqadi. Uning o'rniga belgi chiziladi.
    """
    out = []
    for ch in (s or ""):
        o = ord(ch)
        if o > 0x2100 or o in (0xFE0F, 0x200D):
            continue
        out.append(ch)
    return " ".join("".join(out).split())


_ICON_WORDS = (
    ("truck", ("yetkaz", "dostavka", "olib bor")),
    ("drop", ("qopqoq", "tejam", "suv", "namlik", "yuv")),
    ("leaf", ("hid", "xushbo'y", "tabiiy", "toza havo")),
    ("cloud", ("yumshoq", "yengil", "mayin", "yumshat")),
    ("shield", ("himoya", "chidamli", "mustahkam", "kafolat", "uzoq")),
    ("bolt", ("tez", "kuchli", "quvvat", "elektr")),
)


def _icon_kind(text: str) -> str:
    low = (text or "").lower()
    for kind, words in _ICON_WORDS:
        if any(w in low for w in words):
            return kind
    return "check"


def _icon(kind: str, size: int):
    """Belgini O'ZIMIZ chizamiz — shriftga bog'liq emas, har serverda bir xil."""
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = (16, 92, 58, 255)
    s = size
    d.ellipse([0, 0, s - 1, s - 1], fill=(116, 198, 157, 255))
    if kind == "check":
        d.line([(s * .27, s * .52), (s * .44, s * .69), (s * .75, s * .32)],
               fill=c, width=max(3, int(s * .09)))
    elif kind == "drop":
        d.polygon([(s * .5, s * .22), (s * .72, s * .58), (s * .5, s * .78),
                   (s * .28, s * .58)], fill=c)
    elif kind == "leaf":
        d.pieslice([s * .22, s * .22, s * .78, s * .78], 200, 20, fill=c)
        d.line([(s * .3, s * .7), (s * .7, s * .3)], fill=(116, 198, 157, 255),
               width=max(2, int(s * .05)))
    elif kind == "cloud":
        d.ellipse([s * .18, s * .42, s * .52, s * .70], fill=c)
        d.ellipse([s * .38, s * .32, s * .78, s * .70], fill=c)
        d.rectangle([s * .30, s * .56, s * .70, s * .70], fill=c)
    elif kind == "shield":
        d.polygon([(s * .5, s * .20), (s * .76, s * .32), (s * .72, s * .62),
                   (s * .5, s * .80), (s * .28, s * .62), (s * .24, s * .32)],
                  fill=c)
    elif kind == "bolt":
        d.polygon([(s * .56, s * .18), (s * .32, s * .55), (s * .48, s * .55),
                   (s * .42, s * .82), (s * .70, s * .44), (s * .53, s * .44)],
                  fill=c)
    elif kind == "truck":
        d.rectangle([s * .20, s * .40, s * .55, s * .62], fill=c)
        d.polygon([(s * .55, s * .46), (s * .72, s * .46), (s * .80, s * .62),
                   (s * .55, s * .62)], fill=c)
        d.ellipse([s * .26, s * .58, s * .40, s * .72], fill=c)
        d.ellipse([s * .60, s * .58, s * .74, s * .72], fill=c)
    return im


def _text_layer(size, draw_fn):
    """Kichik shaffof qatlam — matn bir marta chiziladi, keyin har kadrda
    faqat ko'chiriladi (har kadrda qayta chizish sekin bo'lardi)."""
    from PIL import Image, ImageDraw
    lay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(lay), lay)
    return lay


def _alpha(img, k: float):
    """Qatlamni shaffoflashtiradi (0..1)."""
    if k >= 0.999:
        return img
    out = img.copy()
    out.putalpha(out.getchannel("A").point(lambda v: int(v * max(0.0, k))))
    return out


def _cutout_png(src: str, dst: str, timeout: int = 240) -> bool:
    """Fonni ALOHIDA jarayonda kesadi (model 300+ MB oladi, jarayon tugashi
    bilan xotira qaytadi — server 1 GB). Natija: shaffof PNG."""
    # Rasmni AVVAL kichraytiramiz. Model baribir kichik o'lchamda ishlaydi,
    # lekin to'liq o'lchamdagi rasm va oraliq tenzorlar xotirani 400+ MB ga
    # olib chiqadi — swapsiz 1 GB li serverda bu SIGKILL degani (kod=-9).
    code = (
        "import sys;from PIL import Image;from bot import imageproc;"
        "im=Image.open(sys.argv[1]).convert('RGB');"
        "im.thumbnail((900,900), Image.LANCZOS);"
        "c=imageproc.cutout(im);"
        "sys.exit(0 if (c and c.save(sys.argv[2],'PNG') is None) else 1)"
    )
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    env.setdefault("CUTOUT_MODEL", "u2netp")
    # Bitta yadroli serverda ko'p oqim foyda bermaydi, lekin har oqim o'z
    # buferini oladi — xotira behuda sarflanadi.
    env.setdefault("OMP_NUM_THREADS", "1")
    env.setdefault("ORT_NUM_THREADS", "1")
    try:
        r = subprocess.run([sys.executable, "-c", code, src, dst],
                           cwd=root, env=env, capture_output=True, timeout=timeout)
    except Exception as e:
        logger.warning("🎬 Fon kesish jarayoni ishlamadi: %s", e)
        return False
    if r.returncode == 0 and os.path.exists(dst):
        return True
    # Nega yiqilganini AYTAMIZ: ilgari bu joy jim `False` qaytarardi va
    # videoda mahsulot nega kesilmagani umuman bilinmasdi (-9 = xotira
    # tugab o'ldirilgan, 1 = model maskaga ishonmadi).
    logger.warning("🎬 Fon kesilmadi (kod=%s) %s", r.returncode,
                   (r.stderr or b"").decode()[-200:])
    return False


class _Assets:
    """Bir marta tayyorlanadigan qatlamlar — keyin 360 kadrga ishlatiladi."""

    def __init__(self, product, image_path, price_txt, cat, script=None,
                 scene_paths=None):
        from PIL import Image, ImageDraw, ImageFilter
        self.Image, self.ImageFilter, self.ImageDraw = Image, ImageFilter, ImageDraw

        # Ko'rgazma sahnalari (tovar ish ustida) — 1080x1920 ga kesiladi
        self.scenes = []
        for sp in (scene_paths or []):
            try:
                sc = Image.open(sp).convert("RGB")
                # "cover": kadrni to'la qoplaydi, nisbat buzilmaydi
                k = max(W / sc.width, H / sc.height) * 1.14   # yaqinlashishga zapas
                sc = sc.resize((int(sc.width * k), int(sc.height * k)), Image.LANCZOS)
                # AI sahna manbasi (flux-1-schnell) 1024x1024 KVADRAT —
                # tik kadrni to'ldirish uchun ~2x kattalashtirish fonni
                # doim xira/yuvilgan qilib qo'yardi. Haqiqiy piksel
                # qo'shib bo'lmaydi (manba shuncha), lekin unsharp-mask
                # chekkalarni kuchaytirib, ko'zga aniqroq ko'rsatadi.
                sc = sc.filter(
                    ImageFilter.UnsharpMask(radius=3, percent=140, threshold=2))
                self.scenes.append(sc)
            except Exception as e:
                logger.info("🎬 Sahna ochilmadi: %s", e)

        # 1) AVVAL fonni kesamiz — model uchun xotira eng ko'p shu paytda
        # bo'sh (qolgan qatlamlar hali chizilmagan).
        obj = None
        cut_path = image_path + ".cut.png"
        if _cutout_png(image_path, cut_path):
            try:
                obj = Image.open(cut_path).convert("RGBA")
            except Exception:
                obj = None
            finally:
                try:
                    os.remove(cut_path)
                except OSError:
                    pass

        # Fon: gradient + markazda yumshoq yorug'lik. Parallaks uchun
        # kadrdan kattaroq chiziladi va har kadrda boshqa joyidan kesiladi.
        self.pad = 60
        bg = _gradient(W + self.pad * 2, H + self.pad * 2, BG_TOP, BG_BOTTOM)
        glow = Image.new("L", (bg.width, bg.height), 0)
        gd = ImageDraw.Draw(glow)
        r = int(W * 0.62)
        gd.ellipse([bg.width // 2 - r, int(H * 0.30) - r,
                    bg.width // 2 + r, int(H * 0.30) + r], fill=110)
        glow = glow.filter(ImageFilter.GaussianBlur(int(W * 0.16)))
        bg.paste(Image.new("RGB", bg.size, (46, 106, 79)), (0, 0), glow)
        self.bg = bg.convert("RGB")

        # Mahsulot: foni kesilgan bo'lsa fon ustida "suzadi", aks holda
        # yumaloq burchakli kartochka ichida ko'rsatiladi.
        self.cut = obj is not None
        if obj is None:
            src = Image.open(image_path).convert("RGB")
            side = max(src.size)
            plate = Image.new("RGBA", (side, side), CARD_BG + (255,))
            plate.paste(src, ((side - src.width) // 2, (side - src.height) // 2))
            mask = Image.new("L", (side, side), 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                [0, 0, side - 1, side - 1], radius=int(side * 0.07), fill=255)
            plate.putalpha(mask)
            obj = plate
        box = int(W * 0.66)
        obj.thumbnail((box, box), Image.LANCZOS)
        self.obj = obj

        # Soya — mahsulot bilan birga qimirlaydi, chuqurlik hissi beradi
        sh_w, sh_h = obj.width, int(obj.height * 0.26)
        blur_r = max(2, int(sh_h * 0.4))
        # Blur radiusi ellips BALANDLIGIGA nisbiy bo'lishi kerak — video
        # kengligiga (W) nisbiy sobit radius yassi ellipsni tekis
        # to'rtburchak "plato"ga aylantirib qo'yardi (yumshoq oval o'rniga).
        # MUHIM: ellips avval canvas CHETIGA tegib chizilardi — blur canvas
        # tashqarisiga «cho'zila olmaydi», shu sababli chap/o'ng qirralar
        # hech qachon nolga so'nmay, yakuniy videoda yumshoq oval o'rniga
        # to'g'ri burchakli xira "quti" ko'rinardi. Endi ellips atrofida
        # blur radiusiga teng bo'sh joy (padding) qoldiriladi — shu joyda
        # qirralar to'liq so'nadi.
        pad = blur_r
        sh = Image.new("L", (sh_w + pad * 2, sh_h + pad * 2), 0)
        ImageDraw.Draw(sh).ellipse(
            [pad, pad, pad + sh_w - 1, pad + sh_h - 1], fill=150)
        self.shadow = sh.filter(ImageFilter.GaussianBlur(blur_r))

        # Yorug'lik chizig'i — mahsulot ustidan o'tadi (mahsulot shakli bo'ylab)
        sw = Image.new("L", (obj.width * 2, obj.height), 0)
        swd = ImageDraw.Draw(sw)
        for i in range(sw.width):
            k = abs(i - sw.width * 0.5) / (sw.width * 0.14)
            swd.line([(i, 0), (i, sw.height)], fill=int(190 * max(0.0, 1 - k)))
        self.sweep = sw.rotate(18, expand=True, resample=Image.BILINEAR)

        # ── Matn qatlamlari ───────────────────────────────────────────────
        sc = script or {}
        self.head = _text_layer((W, int(H * 0.16)), lambda d, im: self._head(d, im))

        hook = _strip_emoji(sc.get("hook", "")) or (cat or "")
        self.hook_lay = _text_layer(
            (W, int(H * 0.20)), lambda d, im: self._hook(d, hook))

        name = _strip_emoji(product.get("display_name") or product.get("name") or "")
        self.name_lay = _text_layer(
            (W, int(H * 0.13)), lambda d, im: self._name(d, name))
        self.sub_lay = None
        if cat:
            self.sub_lay = _text_layer(
                (W, int(H * 0.05)), lambda d, im: self._chip(d, cat))

        self.bullets = []
        for b in (sc.get("bullets") or [])[:3]:
            txt = _strip_emoji(b)
            if txt:
                self.bullets.append(_text_layer(
                    (W, 120), lambda d, im, x=txt: self._bullet(d, im, x)))

        # Sahna ustida ko'rinadigan KATTA foyda matni (tabletka emas —
        # butun ekran bo'ylab, chunki fon rasm bo'ladi)
        self.big = []
        for b in (sc.get("bullets") or [])[:3]:
            txt = _strip_emoji(b)
            if txt:
                self.big.append(_text_layer(
                    (W, int(H * 0.16)), lambda d, im, x=txt: self._big(d, im, x)))

        self.price_lay = _text_layer(
            (int(W * 0.9), int(H * 0.09)), lambda d, im: d.text(
                (0, 0), price_txt, font=_font(int(W * 0.095)), fill=ACCENT + (255,)))
        self.cta_lay = _text_layer(
            (W, int(H * 0.09)), lambda d, im: self._cta(d))
        self.scrim = self._scrim()
        self._cache = {}

    # ── qatlam chizuvchilari ──────────────────────────────────────────────
    def _head(self, d, im):
        try:
            from bot.imageproc import _logo
            lg = _logo(int(W * 0.115))
            if lg is not None:
                im.paste(lg, (int(W * 0.07), 10), lg)
        except Exception:
            pass
        d.text((int(W * 0.225), 16), "CHINOR", font=_font(int(W * 0.055)),
               fill=(255, 255, 255, 255))
        d.text((int(W * 0.227), int(W * 0.080)),
               "Sergeli · qurilish va uy-ro'zg'or",
               font=_font(int(W * 0.025), bold=False), fill=ACCENT + (230,))

    def _hook(self, d, hook):
        f = _font(int(W * 0.100))
        y = 0
        lines = _wrap(d, hook, f, int(W * 0.86), 2)
        for i, line in enumerate(lines):
            d.text((int(W * 0.07), y), line, font=f,
                   fill=(ACCENT + (255,)) if i == len(lines) - 1 and len(lines) > 1
                        else (255, 255, 255, 255))
            y += int(W * 0.115)

    def _name(self, d, name):
        f = _font(int(W * 0.070))
        y = 0
        for line in _wrap(d, name, f, int(W * 0.86), 2):
            d.text((int(W * 0.07), y), line, font=f, fill=(255, 255, 255, 255))
            y += int(W * 0.082)

    def _chip(self, d, cat):
        f = _font(int(W * 0.031))
        txt = cat.upper()
        tw = d.textlength(txt, font=f)
        d.rounded_rectangle([int(W * 0.07), 0, int(W * 0.07) + tw + 34, 50],
                            radius=25, fill=ACCENT + (255,))
        d.text((int(W * 0.07) + 17, 9), txt, font=f, fill=(9, 40, 25, 255))

    def _bullet(self, d, im, txt):
        """Foyda qatori: chizilgan belgi + oq tabletka ichida matn."""
        f = _font(int(W * 0.050))
        icon = _icon(_icon_kind(txt), 74)
        tw = d.textlength(txt, font=f)
        x0 = int(W * 0.07)
        w = 74 + 22 + tw + 52
        d.rounded_rectangle([x0, 0, x0 + w, 104], radius=52,
                            fill=(255, 255, 255, 248))
        im.paste(icon, (x0 + 16, 15), icon)
        d.text((x0 + 16 + 74 + 20, 26), txt, font=f, fill=(14, 52, 33, 255))

    def _big(self, d, im, txt):
        """Sahna ustidagi foyda: chizilgan belgi + ikki qatorgacha matn."""
        f = _font(int(W * 0.072))
        icon = _icon(_icon_kind(txt), 92)
        im.paste(icon, (int(W * 0.07), 4), icon)
        x = int(W * 0.07) + 92 + 24
        y = 0
        for line in _wrap(d, txt, f, W - x - int(W * 0.07), 2):
            d.text((x, y), line, font=f, fill=(255, 255, 255, 255))
            y += int(W * 0.085)

    def _cta(self, d):
        d.rounded_rectangle([int(W * 0.07), 0, int(W * 0.93), 108],
                            radius=54, fill=(255, 255, 255, 252))
        f = _font(int(W * 0.038))
        # Kanalda post ostida «Sotib olish» tugmasi turadi — chaqiruv
        # aynan shunga ishora qilsin, mavhum «havola» emas.
        # DIQQAT: matnda EMOJI bo'lmasin — shriftda emoji yo'q, ekranda
        # bo'sh kvadrat bo'lib chiqadi. Strelkani o'zimiz chizamiz.
        txt = "«Sotib olish» tugmasini bosing"
        tw = d.textlength(txt, font=f)
        x0 = (W - tw) / 2 + 26
        d.text((x0, 34), txt, font=f, fill=(16, 60, 38, 255))
        # pastga qaragan uchburchak
        ax, ay, r = x0 - 42, 54, 15
        d.polygon([(ax - r, ay - r + 4), (ax + r, ay - r + 4), (ax, ay + r)],
                  fill=(16, 92, 58, 255))

    def _scrim(self):
        """Pastki qismni qoraytiradi — sahna FOTOSURAT bo'lgani uchun oq/och
        fonlarda ustidagi oq matn (`_big`) ko'rinmay qolishi mumkin edi:
        avvalgi egri chiziq tepada deyarli shaffof (0%) edi va faqat eng
        pastki chetda to'liq qorayardi, `_big` matni esa ko'pincha shu
        "shaffof" mintaqaga to'g'ri kelardi. Endi tepada ham minimal
        qoraytirish bor va pastga tezroq kuchayadi — fon qanday bo'lishidan
        qat'i nazar matn o'qiladi."""
        from PIL import Image, ImageDraw
        h = int(H * 0.52)
        sc = Image.new("RGBA", (W, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(sc)
        for i in range(h):
            k = i / h
            a = int(40 + 215 * (k ** 1.2))
            d.line([(0, i), (W, i)], fill=(6, 20, 13, min(255, a)))
        return sc

    # ── kadr uchun mahsulot nusxasi (kesh bilan) ──────────────────────────
    def variant(self, scale: float, angle: float):
        """Miqyos va burilish bo'yicha tayyor nusxa. Qiymatlar yaxlitlanadi —
        ketma-ket kadrlar ko'pincha bir xil nusxani qayta ishlatadi."""
        key = (round(scale, 2), round(angle, 1))
        got = self._cache.get(key)
        if got is not None:
            return got
        im = self.obj
        w = max(8, int(im.width * key[0]))
        h = max(8, int(im.height * key[0]))
        v = im.resize((w, h), self.Image.BICUBIC)
        if abs(key[1]) > 0.05:
            v = v.rotate(key[1], expand=True, resample=self.Image.BILINEAR)
        # DIQQAT: kesh CHEGARASI kichik bo'lishi shart — har nusxa ~3 MB,
        # 1 GB li serverda ko'p saqlansa xotira tugaydi.
        if len(self._cache) >= 24:
            self._cache.clear()
        self._cache[key] = v
        return v


# Sahna chegaralari (soniya). Ko'rgazma sahnalari bo'lsa — tovar oynasi
# ikki marta uziladi va o'sha payt ekranda tovar ISHLATILAYOTGAN muhit
# ko'rinadi. Sahna yo'q bo'lsa (AI yiqilsa) rolik eski tartibda ketaveradi.
T_HOOK, T_NAME = 0.0, 1.7
T_SC1 = 3.3                        # ishlatish sahnalari shu yerdan boshlanadi
SC_DUR = 1.9                       # har sahna qancha turadi (tez kesim)
T_CMP = 9.0                        # «OLDIN → KEYIN» solishtirish
CMP_DUR = 2.2
T_BACK = T_CMP + CMP_DUR           # tovarga qaytish
T_PRICE = 12.0                     # narx va chaqiruv


def _scene_frame(a, img, k: float, big_lay, k_txt: float, drift: int = 0):
    """Ishlatish sahnasi: rasm yaqinlashadi va sal suriladi, MAHSULOTNING
    O'ZI shu sahna ichiga qo'yiladi — shunda «bu narsa aynan shu yerda
    ishlatiladi» degani ko'rinib turadi, matn bilan aytilmaydi."""
    Image = a.Image
    # Push-in + gorizontal siljish: statik rasm ham "tirik" ko'rinadi
    zoom = 1.0 + 0.13 * k
    cw, ch = int(W / zoom), int(H / zoom)
    x = max(0, min(img.width - cw, (img.width - cw) // 2 + drift + int(60 * (k - .5))))
    y = max(0, (img.height - ch) // 2)
    fr = img.crop((x, y, x + cw, y + ch)).resize((W, H), Image.BILINEAR)
    fr = fr.convert("RGBA")

    # Mahsulot — sahnaning pastki o'ng burchagida, soyasi bilan
    v = a.variant(0.46, math.sin(k * 3.0) * 1.8)
    px = W - v.width - int(W * 0.05)
    py = int(H * 0.63) - v.height // 2 + int(8 * math.sin(k * 4.0))
    sh = a.shadow.resize((max(8, int(v.width * 0.8)),
                          max(6, int(a.shadow.height * 0.46))), Image.BILINEAR)
    # Fotosurat fonida (haqiqiy sahna) sun'iy soya tekis rangli fondagidan
    # ko'ra ko'proq ko'zga tashlanadi — fon o'zida allaqachon soya-kontrast
    # bor, shuning uchun bu yerda yengilroq qilamiz.
    sh = sh.point(lambda p: int(p * 0.3))
    fr.paste(Image.new("RGBA", sh.size, (4, 18, 11, 255)),
             (px + v.width // 2 - sh.width // 2, py + v.height - sh.height // 2), sh)
    # Kirib kelishi: o'ngdan sirg'alib chiqadi
    slide = int((1 - _ease_out(min(1.0, k * 3.2))) * W * 0.35)
    fr.alpha_composite(v, (px + slide, py))

    fr.alpha_composite(a.scrim, (0, H - a.scrim.height))
    fr.alpha_composite(_alpha(a.head, 1.0), (0, int(H * 0.048)))
    if big_lay is not None and k_txt > 0:
        e = _ease_out(k_txt)
        fr.alpha_composite(_alpha(big_lay, e),
                           (0, int(H * 0.775) + int(70 * (1 - e))))
    return fr


def _compare_frame(a, k: float):
    """«OLDIN → KEYIN»: bitta sahna, ikki holat, o'rtasida suriladigan
    chegara. Reklama uchun eng ishonarli usul — odam natijani o'z ko'zi
    bilan solishtiradi, matnga ishonishi shart emas."""
    Image = a.Image
    before, after = a.scenes[0], a.scenes[-1]

    # Sekin yaqinlashish — chegara surilib bo'lgach ham kadr QOTIB
    # qolmasin (avval oxirgi ~0.6 soniya harakatsiz turardi).
    zoom = 1.04 + 0.05 * k

    def _fit(img):
        cw, ch = int(W / zoom), int(H / zoom)
        x = max(0, (img.width - cw) // 2)
        y = max(0, (img.height - ch) // 2)
        return img.crop((x, y, x + cw, y + ch)).resize((W, H), Image.BILINEAR)

    fr = _fit(before).convert("RGBA")
    # Chegara chapdan o'ngga suriladi: avval «oldin», keyin «keyin» ochiladi
    x = int(W * _ease_out(min(1.0, k * 1.12)))
    if x > 2:
        fr.paste(_fit(after).crop((0, 0, x, H)), (0, 0))
    d = a.ImageDraw.Draw(fr)
    d.rectangle([x - 3, 0, x + 3, H], fill=(255, 255, 255, 235))

    # Yorliqlar
    f = _font(int(W * 0.042))
    for txt, cx, col in (("OLDIN", int(W * 0.74), (255, 255, 255, 245)),
                         ("KEYIN", int(W * 0.26), (255, 255, 255, 245))):
        tw = d.textlength(txt, font=f)
        bx = int(cx - tw / 2)
        d.rounded_rectangle([bx - 22, int(H * 0.12), bx + tw + 22, int(H * 0.12) + 62],
                            radius=31, fill=(9, 40, 25, 210))
        d.text((bx, int(H * 0.12) + 12), txt, font=f, fill=col)
    fr.alpha_composite(_alpha(a.head, 1.0), (0, int(H * 0.048)))
    return fr


def _compose(a: "_Assets", t: float):
    """Bitta kadr."""
    import math
    Image = a.Image
    has_scenes = len(a.scenes) >= 1

    # ── Ko'rgazma sahnalari ──────────────────────────────────────────────
    # «OLDIN → KEYIN» faqat uchala sahna ham chizilgan bo'lsa ma'noli:
    # aks holda «oldin» va «jarayon» solishtirilib, farq ko'rinmaydi.
    if has_scenes and len(a.scenes) >= 3 and T_CMP <= t < T_BACK:
        return _compare_frame(a, _seg(t, T_CMP, CMP_DUR)).convert("RGB")

    _sc_end = T_CMP if len(a.scenes) >= 3 else T_BACK
    if has_scenes and T_SC1 <= t < _sc_end:
        n = len(a.scenes)
        # Sahna blokini NECHTA sahna bo'lsa shuncha bo'lakka teng bo'lamiz.
        # Ilgari har sahnaga qat'iy SC_DUR (1.9s) berilardi: sahna 2 ta
        # bo'lsa blok 3.3→11.2 (7.9s) edi, ikkinchi sahna 7.1s da tugab
        # qolgan 4 soniya QOTIB turardi — rolik shu yerda «to'xtab»
        # ko'rinardi. Endi harakat blok oxirigacha davom etadi.
        sc_dur = max(0.8, (_sc_end - T_SC1) / n)
        idx = min(n - 1, int((t - T_SC1) // sc_dur))
        idx = max(0, idx)
        start = T_SC1 + idx * sc_dur
        k = _seg(t, start, sc_dur)
        big = a.big[idx] if idx < len(a.big) else None
        return _scene_frame(a, a.scenes[idx], k, big,
                            _seg(t, start + 0.15, 0.35),
                            drift=(-70 if idx % 2 else 70)).convert("RGB")

    # ── Tovar sahnalari ──────────────────────────────────────────────────
    bx = int(a.pad + math.sin(t * 0.9) * 16)
    by = int(a.pad + math.sin(t * 0.6 + 1.2) * 12)
    frame = a.bg.crop((bx, by, bx + W, by + H)).convert("RGBA")

    intro = _ease_out_back(_seg(t, 0.05, 1.1))
    up = _ease_out(_seg(t, T_NAME, 0.7))
    cy = int(H * (0.46 - 0.10 * up))
    # Oxirgi ko'paytuvchi — juda sekin, TO'XTAMAYDIGAN yaqinlashish.
    # Busiz tovar «suzish»i ba'zi joyda ko'zga ilinmay, kadr qotgandek
    # tuyulardi (kirish va nom sahnalarida ~0.6 soniyalik «to'xtash»).
    scale = (0.78 + 0.22 * intro) * (1 + 0.10 * up) * (1 + 0.007 * t)
    if t > T_PRICE:
        scale *= 1 + 0.05 * _ease_out(_seg(t, T_PRICE, 1.2))
    angle = math.sin(t * 1.15) * 2.4 * min(1.0, _seg(t, 0.6, 0.8))
    float_y = math.sin(t * 1.6) * 13
    drop_y = (1 - intro) * H * 0.40
    # Ko'rgazmadan qaytganda tovar yana sakrab kiradi
    if has_scenes and t >= T_BACK:
        back = _ease_out_back(_seg(t, T_BACK, 0.5))
        scale *= 0.9 + 0.1 * back
        drop_y += (1 - back) * H * 0.12

    v = a.variant(scale, angle)
    ox = W // 2 - v.width // 2 + int(math.sin(t * 0.9) * 20)
    oy = cy - v.height // 2 + int(float_y + drop_y)

    sh_w = int(v.width * 0.84)
    if sh_w > 8:
        sh = a.shadow.resize((sh_w, max(8, int(a.shadow.height * scale))),
                             Image.BILINEAR)
        sh = sh.point(lambda p: int(p * 0.6))
        black = Image.new("RGBA", sh.size, (4, 18, 11, 255))
        frame.paste(black, (W // 2 - sh.width // 2,
                            cy + v.height // 2 - int(sh.height * 0.35)), sh)
    frame.alpha_composite(v, (ox, oy))

    # Yorug'lik chizig'i — tanishtirish sahnasida
    if T_NAME <= t <= T_NAME + 1.2:
        k = _seg(t, T_NAME, 1.2)
        sw = a.sweep.resize((int(v.width * 2), v.height), Image.BILINEAR)
        band = Image.new("L", (v.width, v.height), 0)
        band.paste(sw, (int(-v.width + k * v.width * 2.4), 0))
        band = Image.fromarray(_mul(band, v.getchannel("A")))
        frame.paste(Image.new("RGBA", (v.width, v.height), (255, 255, 255, 255)),
                    (ox, oy), band)

    frame.alpha_composite(_alpha(a.head, _ease_out(_seg(t, 0.25, 0.6))),
                          (0, int(H * 0.048)))
    frame.alpha_composite(a.scrim, (0, H - a.scrim.height))

    # ILGAK — savol
    if t < T_NAME + 0.5:
        k_in = _ease_out(_seg(t, 0.5, 0.6))
        k_out = _seg(t, T_NAME, 0.45)
        y = int(H * 0.70) + int(70 * (1 - k_in)) - int(70 * k_out)
        frame.alpha_composite(_alpha(a.hook_lay, k_in * (1 - k_out)), (0, y))

    # TANISHTIRISH — bo'lim + nom (narx sahnasida ham qoladi)
    # Ilgak T_NAME+0.45'da butunlay so'nadi (yuqoridagi k_out) — nom shundan
    # KEYIN kira boshlaydi, aks holda ikkalasi bir balandlikda ustma-ust
    # tushib, o'qib bo'lmaydigan matn hosil qiladi.
    if t >= T_NAME:
        k = _ease_out(_seg(t, T_NAME + 0.45, 0.5))
        if a.sub_lay is not None:
            frame.alpha_composite(_alpha(a.sub_lay, k), (0, int(H * 0.660)))
        frame.alpha_composite(_alpha(a.name_lay, k),
                              (0, int(H * 0.705) + int(50 * (1 - k))))

    # UCHINCHI FOYDA — ko'rgazmadan keyin, tovar bilan birga
    if has_scenes and len(a.big) > 2 and T_BACK <= t < T_PRICE + 0.1:
        k = _ease_out(_seg(t, T_BACK + 0.2, 0.45)) * (1 - _seg(t, T_PRICE, 0.3))
        if k > 0:
            frame.alpha_composite(_alpha(a.big[2], k), (0, int(H * 0.800)))
    # Sahna yo'q bo'lsa — uchala foyda eski tartibda ro'yxat bo'lib chiqadi
    if not has_scenes:
        for i, lay in enumerate(a.bullets):
            k = _ease_out(_seg(t, T_SC1 + i * 1.7, 0.45))
            if k <= 0:
                continue
            fade = 1 - _seg(t, T_PRICE, 0.4)
            frame.alpha_composite(_alpha(lay, k * fade),
                                  (0, int(H * 0.815) + i * 118))

    # NARX «muhr» + chaqiruv
    k_pr = _seg(t, T_PRICE + 0.1, 0.5)
    if k_pr > 0:
        pl = a.price_lay
        if k_pr < 1.0:
            # «Muhr»: kattadan kichrayib tushadi
            ps = 1.8 - 0.8 * min(1.0, _ease_out_back(k_pr, 2.4))
            op = min(1.0, k_pr * 2.2)
        else:
            # Tushib bo'lgach sekin «nafas oladi» — ko'z narxga qaytadi
            ps = 1.0 + 0.035 * (1 + math.sin((t - T_PRICE) * 3.4)) / 2
            op = 1.0
        pw, ph = max(8, int(pl.width * ps)), max(8, int(pl.height * ps))
        pv = pl.resize((pw, ph), Image.BILINEAR)
        frame.alpha_composite(_alpha(pv, op),
                              (int(W * 0.07) - (pw - pl.width) // 2,
                               int(H * 0.800) - (ph - pl.height) // 2))

    k_cta = _seg(t, T_PRICE + 0.6, 0.5)
    if k_cta > 0:
        e = _ease_out_back(k_cta)
        frame.alpha_composite(_alpha(a.cta_lay, min(1.0, k_cta * 2)),
                              (0, int(H * 0.893) + int(150 * (1 - e))))

    return frame.convert("RGB")


def _mul(a_img, b_img):
    """Ikki L-kanalni ko'paytirish (numpy imageproc'da allaqachon bor)."""
    import numpy as np
    return (np.asarray(a_img, dtype=np.uint16)
            * np.asarray(b_img, dtype=np.uint16) // 255).astype("uint8")


# ─── Diktor ovozi ──────────────────────────────────────────────────────────
# Rolikni ovozsiz ko'rgan odam matnni o'qishga ulgurmaydi — lentada barmoq
# 2 soniyada ketadi. Gap eshitilsa, ko'rish vaqti sezilarli uzayadi.
# Har gap EKRANDAGI matn bilan bir vaqtda aytiladi (quyidagi jadval).

def _voice_text(script: dict, price_txt: str) -> tuple:
    """Diktor o'qiydigan IKKI bo'lak: (ilgak+foydalar, narx+chaqiruv).

    Nega bitta gapga bitta so'rov EMAS: Gemini TTS bepul darajasida
    daqiqasiga bir necha so'rovga ruxsat beradi, besh gap yuborilsa
    yarmi «429» bilan qaytadi (2026-08-24 da shunday bo'ldi). Ikki
    bo'lak — kvota uchun ham xavfsiz, tasvirga ham mos tushadi.
    """
    def _sentences(parts):
        out = []
        for x in parts:
            x = " ".join((x or "").split()).strip()
            if not x:
                continue
            if x[-1] not in ".!?":       # «Kran tomchilayaptimi?.» bo'lmasin
                x += "."
            out.append(x)
        return " ".join(out)

    hook = ((script or {}).get("hook") or "").strip()
    bullets = [b for b in ((script or {}).get("bullets") or []) if b]
    main = _sentences([hook] + bullets[:3])
    tail = _sentences(["Narxi " + price_txt, "Chinor do'konida"]) if price_txt else ""
    return main, tail


def build_voice(script: dict, price_txt: str, n_scenes: int,
                out_wav: str, tmp_prefix: str) -> bool:
    """Diktor yo'li (DURATION soniyalik WAV). Chiqmasa False."""
    try:
        from bot import tts
    except Exception:
        return False
    if not tts.available():
        return False

    main, tail = _voice_text(script, price_txt)
    plan = []                      # (rejalashtirilgan vaqt, matn)
    if main:
        plan.append((0.55, main))
    if tail:
        plan.append((T_PRICE + 0.30, tail))

    made = []
    for i, (at, txt) in enumerate(plan):
        part = "%s.v%d.wav" % (tmp_prefix, i)
        if i:
            time.sleep(2)          # so'rovlar orasida nafas (429 bo'lmasin)
        if tts.speak(txt, part):
            made.append((at, part, tts.duration(part)))
    if not made:
        return False

    # Joylash: bo'lak o'z vaqtida boshlanadi, sig'masa tezlashtiriladi
    # (1.35 barobargacha), keyingisi esa oldingisi tugagach kiradi.
    placed, prev_end = [], 0.0
    for at, part, dur in made:
        if dur <= 0:
            continue
        start = max(at, prev_end + 0.15)
        room = DURATION - 0.15 - start
        if room <= 0.4:
            continue
        tempo = min(1.35, dur / room) if dur > room else 1.0
        eff = dur / tempo
        if eff > room:
            continue
        placed.append((start, part, tempo))
        prev_end = start + eff
    if not placed:
        return False

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for _, part, _t in placed:
        cmd += ["-i", part]
    chains, labels = [], []
    for i, (at, _part, tempo) in enumerate(placed):
        ms = max(0, int(at * 1000))
        speed = ("atempo=%.3f," % tempo) if tempo > 1.001 else ""
        chains.append("[%d:a]%saresample=44100,adelay=%d|%d[v%d]"
                      % (i, speed, ms, ms, i))
        labels.append("[v%d]" % i)
    fc = (";".join(chains) + ";" + "".join(labels)
          + "amix=inputs=%d:normalize=0:duration=longest,"
            "apad,atrim=0:%s[a]" % (len(placed), DURATION))
    cmd += ["-filter_complex", fc, "-map", "[a]", "-ac", "1",
            "-ar", "44100", out_wav]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=180)
        ok = r.returncode == 0 and os.path.exists(out_wav)
        if not ok:
            logger.info("🔊 Diktor yig'ilmadi: %s", (r.stderr or b"")[-200:])
    except Exception as e:
        logger.info("🔊 Diktor yig'ilmadi: %s", e)
        ok = False
    for _, part, _t in made:
        try:
            os.remove(part)
        except OSError:
            pass
    logger.info("🔊 Diktor: %d bo'lak", len(placed))
    return bool(ok)


def render(product: dict, image_path: str, price_txt: str, cat: str,
           out_path: str, script: dict = None, scene_paths: list = None,
           voice_wav: str = "") -> str:
    """Rasmdan REKLAMA rolikini yasaydi.
    Qaytaradi: xato matni ('' = muvaffaqiyat)."""
    if not is_available():
        return "ffmpeg o'rnatilmagan"
    if not os.path.exists(image_path):
        return f"rasm topilmadi: {image_path}"
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    try:
        assets = _Assets(product, image_path, price_txt, cat, script, scene_paths)
    except Exception as e:
        return f"qatlamlar tayyorlanmadi: {e}"

    # ── Ovoz ──────────────────────────────────────────────────────────────
    # Musiqa va effektlar sintez qilinadi (mualliflik huquqi toza). Yasab
    # bo'lmasa — jimjit yo'l qo'yiladi: ko'p platforma, Instagram ham,
    # tovushsiz faylni rad etadi.
    wav = out_path + ".wav"
    cuts = [T_SC1 + i * SC_DUR for i in range(3)] + [T_CMP, T_BACK, T_PRICE]
    try:
        from bot import reels_audio
        has_audio = reels_audio.build(wav, DURATION, cuts=cuts,
                                      stamp=T_PRICE + 0.15)
    except Exception as e:
        logger.info("🎬 Ovoz yasalmadi: %s", e)
        has_audio = False
    # Diktor: TAYYOR yo'l chaqiruvchidan keladi (`_process` uni yasaydi).
    # Nega shu yerda yasalmaydi: `render` alohida jarayonda ishlaydi va
    # uning jurnali ko'rinmaydi — TTS kvota bilan yiqilsa rolik jimgina
    # ovozsiz chiqib ketardi va buni hech kim bilmasdi.
    voice = voice_wav or ""
    has_voice = bool(voice and os.path.exists(voice))
    if voice and not has_voice:
        logger.info("🔊 Diktor fayli topilmadi: %s", voice)
    if has_voice and has_audio:
        mixed = out_path + ".mix.wav"
        mix_cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                   "-i", wav, "-i", voice, "-filter_complex",
                   "[0:a]volume=0.22[m];[m][1:a]amix=inputs=2:normalize=0:"
                   "duration=first[a]", "-map", "[a]", "-ar", "44100", mixed]
        try:
            if subprocess.run(mix_cmd, capture_output=True,
                              timeout=180).returncode == 0:
                wav = mixed
            else:
                logger.info("🔊 Aralashtirilmadi — faqat diktor")
                wav = voice
        except Exception as e:
            logger.info("🔊 Aralashtirilmadi (%s) — faqat diktor", e)
            wav = voice
        has_audio = True
    elif has_voice:
        wav = voice
        has_audio = True

    audio_in = (["-i", wav] if has_audio
                else ["-f", "lavfi", "-t", str(DURATION),
                      "-i", "anullsrc=r=44100:cl=stereo"])

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
        "-r", str(FPS), "-i", "-",
        *audio_in,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-shortest",
        "-movflags", "+faststart", out_path,
    ]
    total = int(DURATION * FPS)
    proc = None
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE)
        for i in range(total):
            frame = _compose(assets, i / FPS)
            if frame.size != (W, H):     # himoya: kadr o'lchami qat'iy
                frame = frame.resize((W, H))
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
        err = proc.stderr.read().decode()[-400:]
        if proc.wait(timeout=300) != 0:
            return f"ffmpeg: {err}"
    except Exception as e:
        # Quvur uzilsa sabab ffmpeg'ning O'Z xatosida bo'ladi — uni
        # aytmasak, jurnalda faqat "Broken pipe" qoladi.
        detail = ""
        if proc is not None:
            try:
                detail = proc.stderr.read().decode()[-400:]
            except Exception:
                pass
        return f"video yozilmadi: {e}{(' | ffmpeg: ' + detail) if detail else ''}"
    finally:
        for tmp in (out_path + ".wav", out_path + ".mix.wav"):
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
    if not os.path.exists(out_path) or os.path.getsize(out_path) < 10000:
        return "video bo'sh chiqdi"
    return ""


def render_isolated(product: dict, image_path: str, price_txt: str, cat: str,
                    out_path: str, script: dict = None, scene_paths: list = None,
                    timeout: int = 900, voice_wav: str = "") -> str:
    """`render` ni ALOHIDA jarayonda bajaradi.

    Nega shart: 240 kadrni chizish o'nlab megabayt oladi va 1 GB li serverda
    xotira chegarasiga yaqinlashadi. Agar bu bot jarayonining o'zida
    bo'lsa, tizim OOM paytida BOTNI o'ldiradi — do'kon ishlamay qoladi.
    Alohida jarayon o'lsa, faqat shu video yiqiladi va navbat davom etadi.
    """
    payload = json.dumps({
        "product": {k: product.get(k) for k in
                    ("id", "name", "display_name", "sell_price", "unit")},
        "image": image_path, "price": price_txt, "cat": cat, "out": out_path,
        "script": script or {},
        "scenes": scene_paths or [],
        "voice": voice_wav or "",
    })
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        p = subprocess.run([sys.executable, "-m", "bot.reels", "--render", payload],
                           cwd=root, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "video juda uzoq tayyorlandi"
    except Exception as e:
        return f"jarayon ishga tushmadi: {e}"
    out = (p.stdout or b"").decode().strip().splitlines()
    if p.returncode != 0:
        tail = (p.stderr or b"").decode()[-200:]
        # -9 = OOM bilan o'ldirilgan
        return f"render jarayoni yiqildi (kod={p.returncode}) {tail}"
    try:
        return json.loads(out[-1]).get("error", "")
    except Exception:
        return "render javobi tushunarsiz"


def _cli(argv=None) -> int:
    """`python -m bot.reels --render '<json>'` — alohida jarayon uchun."""
    argv = list(argv if argv is not None else sys.argv[1:])
    if len(argv) >= 2 and argv[0] == "--render":
        d = json.loads(argv[1])
        err = render(d["product"], d["image"], d["price"], d["cat"], d["out"],
                     d.get("script"), d.get("scenes"), d.get("voice") or "")
        print(json.dumps({"error": err}))
        return 0
    print(json.dumps({"error": "noma'lum buyruq"}))
    return 2


# ─── Matn (caption) ─────────────────────────────────────────────────────────

_HASHTAGS = ["#chinor", "#sergeli", "#toshkent", "#qurilishmollari",
             "#stroymarket", "#ustalaruchun"]


def _slug(s: str) -> str:
    keep = "abcdefghijklmnopqrstuvwxyz0123456789"
    s = (s or "").lower().replace("'", "").replace("`", "")
    out = "".join(ch if ch in keep else "_" for ch in s)
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_")


def build_caption(product: dict, price_txt: str, cat: str, bot_username: str,
                  tags: list = None) -> str:
    """Instagram matni. Tavsif allaqachon ikki tilli (uz+ru) bo'lsa ishlatiladi."""
    name = (product.get("display_name") or product.get("name") or "").strip()
    desc = (product.get("description") or "").strip()
    parts = [f"🌳 {name}", f"💰 {price_txt}"]
    if desc:
        parts.append("")
        parts.append(desc[:400])
    parts.append("")
    parts.append("🛒 Buyurtma va butun katalog — profildagi havolada:")
    if bot_username:
        parts.append(f"t.me/{bot_username}")
    parts.append("📍 Sergeli, Chinor bekati yoni · har kuni 8:00–22:00")
    parts.append("")
    tag_list = list(_HASHTAGS)
    if cat:
        tag_list.insert(1, "#" + _slug(cat))
    for t in (tags or [])[:3]:
        s = _slug(t)
        if s and ("#" + s) not in tag_list:
            tag_list.append("#" + s)
    parts.append(" ".join(tag_list))
    return "\n".join(parts)


# ─── Instagram Graph API ────────────────────────────────────────────────────

def _post(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode())


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read().decode())


def publish_instagram(video_url: str, caption: str) -> tuple:
    """Reels'ni BARCHA ulangan profillarga joylaydi (`ig_accounts`).

    Qaytaradi: (birinchi muvaffaqiyatli media_id, xatolar). Bitta profil
    yiqilsa qolganlari baribir joylanadi. Instagram videoni O'ZI yuklab
    oladi, shuning uchun `video_url` ochiq HTTPS manzil bo'lishi shart."""
    accounts = ig_accounts()
    if not accounts:
        return "", "Instagram sozlanmagan"
    first, errs = "", []
    for i, (ig_id, base) in enumerate(accounts):
        refresh_ig_token("" if i == 0 else ig_id, "" if i == 0 else base)
        token = ig_token() if i == 0 else ig_token(ig_id, base)
        mid, err = _publish_one(ig_id, token, video_url, caption)
        if mid:
            logger.info("🎬 Instagram (%s): %s", ig_id, mid)
            first = first or mid
        else:
            errs.append(f"{ig_id}: {err}")
            logger.warning("🎬 Instagram (%s) joylanmadi: %s", ig_id, err)
    return first, "; ".join(errs)


def _publish_one(ig_id: str, token: str, video_url: str, caption: str) -> tuple:
    try:
        cr = _post(f"{_graph()}/{ig_id}/media", {
            "media_type": "REELS", "video_url": video_url,
            "caption": caption[:2200], "share_to_feed": "true",
            "access_token": token,
        })
        cid = cr.get("id")
        if not cid:
            return "", f"yaratilmadi: {cr}"
        # Instagram videoni qayta ishlaydi — tayyor bo'lguncha kutamiz
        for _ in range(30):
            time.sleep(5)
            st = _get(f"{_graph()}/{cid}?fields=status_code&access_token={token}")
            code = st.get("status_code", "")
            if code == "FINISHED":
                break
            if code == "ERROR":
                return "", f"Instagram qayta ishlay olmadi: {st}"
        else:
            return "", "Instagram juda uzoq qayta ishladi"
        pub = _post(f"{_graph()}/{ig_id}/media_publish",
                    {"creation_id": cid, "access_token": token})
        mid = pub.get("id")
        return (mid, "") if mid else ("", f"joylanmadi: {pub}")
    except Exception as e:
        return "", f"Instagram xatosi: {e}"


# ─── Navbat ishchisi ────────────────────────────────────────────────────────

async def _process(row: dict, db) -> None:
    rid, pid = row["id"], row["product_id"]
    p = await db.get_product_any(pid)
    if not p:
        _set(rid, status="skipped", error="tovar topilmadi")
        return
    cat = ""
    try:
        if p.get("category_id"):
            c = await db.get_category(int(p["category_id"]))
            cat = (c or {}).get("name", "")
    except Exception:
        pass
    tags = [t.strip() for t in (p.get("usage_tags") or "").split(",") if t.strip()]
    price = float(p.get("sell_price", 0) or 0)
    price_txt = f"{price:,.0f} so'm".replace(",", " ")

    os.makedirs(REELS_DIR, exist_ok=True)
    out = os.path.join(REELS_DIR, f"reel_{pid}_{int(time.time())}.mp4")

    # Fon kesish uchun ASL rasm afzal: brendlangan nusxa allaqachon och
    # yashil fon ustiga qo'yilgan va model uni tovarning bir qismi deb
    # o'ylab, chetlarini noto'g'ri kesadi.
    src = row["image_path"]
    orig = (p.get("image_orig_url") or "").strip()
    if orig:
        cand = os.path.join(os.path.dirname(src), os.path.basename(orig))
        if os.path.exists(cand):
            src = cand

    # Reklama ssenariysi (ilgak + foydalar) — AI bloklovchi, shuning uchun
    # ishchi oqimda chaqiriladi.
    loop = asyncio.get_running_loop()
    # 1-bosqich: tovar RASMDAN o'rganiladi (bu nima, nima qiladi, natijasi)
    facts = await loop.run_in_executor(
        None, lambda: product_facts(p, src, cat))
    if facts:
        logger.info("🎬 #%s tovar haqida: %s", pid, facts[:160])

    # TANISH YIQILSA — ROLIK YASALMAYDI.
    # Sababi amalda ko'rindi: rasm tanilmaganda matn modeli tovarni faqat
    # NOMIDAN taxmin qiladi va butunlay boshqa narsa haqida reklama yozadi
    # («0 shina 10 tali» → «Elektr rozetkasi o'chib qoldimi?», sahnalarda
    # zanglagan quvur). Bunday rolik kanalda abadiy qoladi, shuning uchun
    # keyinga suramiz: vision zanjiri odatda bir necha daqiqada tiklanadi.
    tries0 = int(row.get("attempts") or 0)
    if not facts:
        if tries0 < 3:
            _set(rid, attempts=tries0 + 1,
                 next_try=time.strftime("%Y-%m-%d %H:%M:%S",
                                        time.localtime(time.time() + 900)),
                 error="rasm tanilmadi (urinish %d/3)" % (tries0 + 1))
            logger.info("🎬 #%s keyinga surildi — rasm tanilmadi "
                        "(urinish %d/3)", pid, tries0 + 1)
            return
        # Uch marta ham tanilmadi — rolik o'rniga oddiy rasm posti.
        _set(rid, status="skipped", error="rasm tanilmadi (3/3)")
        logger.warning("🎬 #%s rolik yasalmadi — rasm tanilmadi, rasm posti "
                       "bilan chiqadi", pid)
        try:
            from bot.web_api import _post_product_to_channel
            await _post_product_to_channel(db, p, pid)
        except Exception as e:
            logger.warning("📢 Zaxira rasm posti yuborilmadi (#%s): %s", pid, e)
        return

    # 2-bosqich: shu ma'lumot asosida reklama matni
    script = await loop.run_in_executor(
        None, lambda: ad_script(p, cat, tags, facts))
    logger.info("🎬 #%s ssenariy (%s): %s | %s", pid, script.get("source"),
                script.get("hook"), " · ".join(script.get("bullets") or []))

    # AI matn yozolmadi (odatda kuchli modellar limitda). Rolikni ZAIF matn
    # bilan chiqarib yubormaymiz — kanalda u abadiy qoladi. Yarim soatdan
    # keyin qayta urinamiz; uch marta ham chiqmasa, bor matn bilan ketadi.
    tries = int(row.get("attempts") or 0)
    if script.get("source") == "fallback" and tries < 3:
        _set(rid, attempts=tries + 1,
             next_try=time.strftime("%Y-%m-%d %H:%M:%S",
                                    time.localtime(time.time() + 1800)),
             error="AI matni kutilmoqda (urinish %d/3)" % (tries + 1))
        logger.info("🎬 #%s roligi keyinga surildi — AI matni zaif "
                    "(urinish %d/3)", pid, tries + 1)
        return
    # Ko'rgazma sahnalari — tovar ISHLATILAYOTGAN muhit (AI chizadi).
    # Yiqilsa rolik ularsiz, matnli ro'yxat bilan yasaladi.
    scenes = []
    try:
        prompts = await loop.run_in_executor(
            None, lambda: scene_prompts(p, cat, tags, facts))
        # Uchala sahna ham kerak: «OLDIN → KEYIN» solishtiruvi faqat 3 ta
        # sahna bo'lganda ishlaydi (aks holda rolikning o'rtasida bo'sh
        # joy qoladi). Yozuv sababli biror sahna rad etilsa, qolganlari
        # blokni o'zaro teng bo'lib oladi.
        for i, pr in enumerate(prompts[:3]):
            sp = os.path.join(REELS_DIR, f"scene_{pid}_{i}.png")
            ok = await loop.run_in_executor(None, lambda pr=pr, sp=sp: gen_scene(pr, sp))
            if ok:
                scenes.append(sp)
        logger.info("🎬 #%s sahnalar: %s ta", pid, len(scenes))
    except Exception as e:
        logger.info("🎬 Sahnalar yasalmadi (#%s): %s", pid, e)

    # Diktor ovozi — ASOSIY jarayonda yasaladi (jurnal ko'rinsin, kvota
    # xatosi yashirinib qolmasin). Chiqmasa rolik faqat musiqa bilan ketadi.
    voice = out + ".voice.wav"
    try:
        ok_voice = await loop.run_in_executor(
            None, lambda: build_voice(script, price_txt, len(scenes),
                                      voice, out))
    except Exception as e:
        logger.info("🔊 Diktor yasalmadi (#%s): %s", pid, e)
        ok_voice = False
    if not ok_voice:
        voice = ""
        logger.info("🔊 #%s rolik OVOZSIZ (diktor chiqmadi)", pid)

    err = await loop.run_in_executor(
        None, lambda: render_isolated(p, src, price_txt, cat, out, script,
                                      scenes, voice_wav=voice))
    if voice:
        try:
            os.remove(voice)
        except OSError:
            pass
    for sp in scenes:                 # sahnalar vaqtinchalik — diskda qolmasin
        try:
            os.remove(sp)
        except OSError:
            pass
    if err:
        _set(rid, status="failed", error=err[:400])
        logger.warning("🎬 Reels tayyorlanmadi (#%s): %s", pid, err)
        # Rolik yiqildi — tovar kanalda E'LONSIZ qolmasin: eski rasm
        # posti shu yerda yuboriladi (rasm posti aynan rolik chiqishi
        # kutilgani uchun o'tkazib yuborilgan edi).
        try:
            from bot.web_api import _post_product_to_channel
            await _post_product_to_channel(db, p, pid)
            logger.info("📢 Rolik o'rniga rasm posti yuborildi (#%s)", pid)
        except Exception as e2:
            logger.warning("📢 Zaxira rasm posti ham yuborilmadi (#%s): %s", pid, e2)
        return

    caption = build_caption(p, price_txt, cat,
                            getattr(db, "_bot_username", "") or "", tags)
    _set(rid, video_path=out, caption=caption)

    # ── 1) Telegram kanal — HOZIRGI ASOSIY manzil ─────────────────────────
    # Instagram'ga avtomatik joylash ataylab O'CHIRIQ turibdi (`reels_auto`).
    # Avval rolik kanalda ishlaydi, uslub va matn barqarorlashgach
    # Instagram yoqiladi — o'shanda bu yerda hech narsa o'zgarmaydi.
    # `reels_channel=0` — rolik faqat Instagram'ga (kanalga tushmaydi)
    posted = (await _post_to_channel(db, p, out, cat)
              if _flag_on(db, "reels_channel", "1") else False)
    if isinstance(posted, int) and posted > 1:
        _set(rid, msg_id=posted)

    # ── 2) Instagram (faqat `reels_auto` yoqilgan va token bo'lsa) ────────
    if ig_configured() and _flag_on(db, "reels_auto", "0"):
        url = _cfg("PUBLIC_BASE_URL").rstrip("/") + "/api/reel/" + os.path.basename(out)
        mid, perr = await loop.run_in_executor(
            None, lambda: publish_instagram(url, caption))
        if mid:
            _set(rid, status="posted", ig_media_id=mid,
                 done_at=time.strftime("%Y-%m-%d %H:%M:%S"))
            logger.info("🎬 Instagram'ga joylandi: #%s → %s", pid, mid)
            return
        _set(rid, error=(perr or "")[:400])
        logger.warning("🎬 Instagram joylanmadi (#%s): %s", pid, perr)

    if posted:
        _set(rid, status="channel", done_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        return
    # Kanalga ham tushmadi (kanal o'chirilgan yoki xato) — adminga yuboramiz
    await _send_for_approval(db, p, out, caption)
    _set(rid, status="review", done_at=time.strftime("%Y-%m-%d %H:%M:%S"))


async def _drop_photo_post(db, pid: int) -> None:
    """Tovarning kanaldagi rasm postini o'chiradi (rolik uning o'rnini oladi)."""
    import json as _json
    bot = getattr(db, "_bot", None)
    if not bot or not pid:
        return
    from bot.config import CHANNEL_ID
    try:
        p = await db.get_product_any(pid)
    except Exception:
        return
    if not p:
        return
    ids = []
    try:
        ids = _json.loads(p.get("channel_msg_ids") or "[]") or []
    except (ValueError, TypeError):
        ids = []
    if not ids and p.get("channel_msg_id"):
        ids = [p["channel_msg_id"]]
    for mid in ids:
        try:
            await bot.delete_message(CHANNEL_ID, mid)
        except Exception as e:
            # 48 soatdan eski post o'chmaydi — bu xato emas, shunchaki
            # eski post kanalda qoladi.
            logger.info("🎬 Eski rasm posti o'chmadi (#%s, %s): %s", pid, mid, e)
    if ids:
        try:
            await db.update_product(pid, channel_msg_id=0, channel_msg_ids="")
        except Exception:
            pass


async def _post_to_channel(db, product: dict, video_path: str, cat: str) -> bool:
    """Rolikni do'kon kanaliga chiqaradi — rasm posti bilan bir xil matn va
    «Sotib olish» tugmasi bilan. Video posti rasm postidan KEYIN ketadi:
    kanalda avval tovar kartochkasi, keyin uning roligi turadi."""
    bot = getattr(db, "_bot", None)
    if not bot or not db.is_channel_enabled():
        return False
    from bot.config import CHANNEL_ID
    if not CHANNEL_ID:
        return False
    # Izoh rasm postidagi bilan AYNAN BIR XIL bo'lsin — narx, qayerda
    # ishlatiladi, qoldiq, shtrix-kod, sotib olish havolalari, aloqa va
    # hashtaglar. Ilgari bu yerda qisqartirilgan o'z matnimiz turardi va
    # kanalda rolik postining tagi rasm postinikidan kambag'al chiqardi.
    pid = int(product.get("id") or 0)
    try:
        from database._formatters import _fmt_product
        pp = dict(product)
        if cat and not pp.get("category_name"):
            pp["category_name"] = cat
        buy_url = (f"https://t.me/{db._bot_username}?start=buy_{pid}"
                   if getattr(db, "_bot_username", "") else "")
        cap = _fmt_product(pp, ai_desc=(product.get("description") or ""),
                           buy_url=buy_url)
    except Exception as e:
        logger.warning("🎬 Izoh yig'ilmadi (#%s): %s — qisqa matn", pid, e)
        name = (product.get("display_name") or product.get("name") or "").strip()
        price = float(product.get("sell_price", 0) or 0)
        cap = f"🎬 <b>{name}</b>\n💰 {price:,.0f} so'm".replace(",", " ")
    try:
        from aiogram.types import FSInputFile
        markup = db._buy_markup(pid)
        msg = await bot.send_video(CHANNEL_ID, FSInputFile(video_path),
                                   caption=cap[:1024], parse_mode="HTML",
                                   reply_markup=markup, supports_streaming=True)
        # Xabar raqamini saqlaymiz — keyin postni o'chirish yoki almashtirish
        # kerak bo'lsa uni qidirib yurmaymiz (birinchi safar kanal sahifasini
        # o'qib topishga to'g'ri kelgan edi).
        logger.info("🎬 Kanalga rolik chiqdi: #%s (xabar %s)",
                    product.get("id"), getattr(msg, "message_id", 0))

        # Shu tovarning ESKI RASM POSTINI o'chiramiz — kanalda bitta tovar
        # ikki marta (rasm + video) turmasin. Rasm posti rolik chiqishidan
        # bir necha daqiqa oldin ketgan, ya'ni Telegram'ning 48 soatlik
        # o'chirish chegarasiga bemalol sig'adi.
        await _drop_photo_post(db, pid)
        return getattr(msg, "message_id", 0) or True
    except Exception as e:
        logger.warning("🎬 Kanalga rolik chiqmadi (#%s): %s",
                       product.get("id"), e)
        return False


def _flag_on(db, key: str, default: str = "0") -> bool:
    try:
        return str(db.get_setting(key, default)).strip().lower() in ("1", "true", "on", "yes")
    except Exception:
        return False


async def _send_for_approval(db, product: dict, video_path: str, caption: str):
    """Videoni matni bilan adminga yuboradi — u bir bosishda Instagram'ga
    joylaydi. Token sozlanmagan do'kon ham bugundan foydalana boshlaydi."""
    bot = getattr(db, "_bot", None)
    if not bot:
        return
    from bot.config import GLAVNIY_ADMIN_ID
    try:
        from aiogram.types import FSInputFile
        await bot.send_video(
            GLAVNIY_ADMIN_ID, FSInputFile(video_path),
            caption=f"🎬 <b>Instagram uchun tayyor</b> — #{product.get('id')}\n"
                    f"Videoni saqlang va matnni nusxalab joylang 👇",
            parse_mode="HTML")
        await bot.send_message(GLAVNIY_ADMIN_ID, caption)
    except Exception as e:
        logger.warning("🎬 Adminga yuborilmadi: %s", e)


# ─── Kunlik avtomatik tanlov (Instagram) ─────────────────────────────────────
# Har kuni AUTO_HOURS soatlarida bittadan tovar tanlanib, rolik navbatiga
# qo'yiladi (2026-10-22: kuniga 2 ta, egasining so'rovi). Tanlov HAQIQIY
# savdodan — YesPOS cheklari (`api_recommend`), eski kassa `sales` emas.
# Yoqish: sozlama `reels_autopost=1`; joylash `reels_auto=1` (Instagram),
# `reels_channel=0` bo'lsa rolik Telegram kanalga tushmaydi.
AUTO_HOURS = [int(h) for h in os.getenv("REELS_AUTO_HOURS", "11,18").split(",")
              if h.strip().isdigit()]
AUTO_GAP_DAYS = int(os.getenv("REELS_AUTO_GAP_DAYS", "45") or 45)


def auto_pick(db) -> int:
    """Rolikka eng arziydigan tovarni tanlab navbatga qo'yadi (rid, 0 — yo'q).

    Shartlar: faol, qoldig'i bor, narxi bor, rasmi diskda, oxirgi
    AUTO_GAP_DAYS kunda rolik chiqmagan. Ball: YesPOS sotuv reytingi,
    qaytarishlar minus, tavsif/teglar plyus, ozgina tasodif (har kuni bir
    xil tovarlar navbat kutib turmasin)."""
    import random
    from bot import api_recommend
    from bot.web_api import UPLOAD_DIR
    try:
        rank, _co, returns = api_recommend._recent_sales_sync(db)
    except Exception as e:
        logger.info("🎯 Savdo reytingi o'qilmadi: %s", e)
        rank, returns = {}, {}
    c = _conn()
    try:
        seen = {r[0] for r in c.execute(
            "SELECT DISTINCT product_id FROM reels_queue "
            "WHERE created_at > datetime('now', ?)", ("-%d days" % AUTO_GAP_DAYS,))}
        rows = c.execute(
            "SELECT id, image_url, image_orig_url, qty, description, usage_tags "
            "FROM products WHERE is_active=1 AND qty > 0 AND sell_price > 0 "
            "AND (image_orig_url != '' OR image_url != '')").fetchall()
    finally:
        c.close()
    top = max([float(v) for v in rank.values()] or [1.0]) or 1.0
    best, best_score, best_img = 0, -1e9, ""
    for r in rows:
        pid = r["id"]
        if pid in seen:
            continue
        name = os.path.basename(((r["image_orig_url"] or r["image_url"]) or "").split("?")[0])
        img = os.path.join(UPLOAD_DIR, name) if name else ""
        if not img or not os.path.isfile(img):
            continue
        score = 5.0 * float(rank.get(pid, 0)) / top
        score -= 2.0 * min(float(returns.get(pid, 0)), 3)
        if (r["description"] or "").strip():
            score += 0.5
        if (r["usage_tags"] or "").strip():
            score += 0.3
        if float(r["qty"] or 0) >= 3:
            score += 0.3
        score += random.random() * 1.5
        if score > best_score:
            best, best_score, best_img = pid, score, img
    if not best:
        return 0
    rid = enqueue_manual(best, best_img)
    logger.info("🎯 Avto-rolik: #%s tanlandi (ball %.2f)", best, best_score)
    return rid


async def autopick_loop(interval: int = 300):
    """Har kuni AUTO_HOURS da bittadan tovarni rolik navbatiga qo'yadi."""
    from database.channel_db import db as _db
    await asyncio.sleep(120)
    while True:
        try:
            if _flag_on(_db, "reels_autopost", "0") and is_available():
                now = time.localtime()
                day = time.strftime("%Y-%m-%d", now)
                for h in AUTO_HOURS:
                    key = "reels_autopick_%d" % h
                    if now.tm_hour >= h and _db.get_setting(key, "") != day:
                        _db.set_setting(key, day)
                        await asyncio.get_running_loop().run_in_executor(
                            None, auto_pick, _db)
        except Exception as e:
            logger.warning("🎯 Avto-rolik tanlovida xato: %s", e)
        await asyncio.sleep(interval)


async def worker_loop(interval: int = 45):
    """Fonda navbatni bo'shatadi. Bir vaqtda BITTA video — server bir yadroli."""
    from database.channel_db import db as _db
    if not is_available():
        logger.info("🎬 Reels o'chiq: ffmpeg o'rnatilmagan")
        return
    logger.info("🎬 Reels ishchisi ishga tushdi (Instagram avto: %s)",
                "ha" if ig_configured() else "yo'q — tasdiqlash rejimi")
    while True:
        try:
            if _flag_on(_db, "reels_enabled", "1"):
                for row in pending(limit=3):
                    # HAR QATOR alohida himoyalanadi. Ilgari `try` butun
                    # sikl ustida edi: bitta qator kutilmagan xato bersa,
                    # orqasidagilar ham o'tkazib yuborilardi — va o'sha
                    # qator navbatda doim birinchi bo'lgani uchun butun
                    # navbatni abadiy to'sib qo'yardi.
                    try:
                        await _process(row, _db)
                    except Exception as e:
                        logger.warning("🎬 #%s ishlanmadi: %s",
                                       row.get("product_id"), e)
                        _set(row["id"], status="failed",
                             error=("kutilmagan xato: %s" % e)[:400])
                    rotate_files()      # disk cheksiz o'smasin
        except Exception as e:
            logger.warning("🎬 Reels ishchisida xato: %s", e)
        await asyncio.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(_cli())
