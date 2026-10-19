"""HTTPS API qatlami — Mini App login uchun aiohttp serveri.

Bu modul bot bilan birgalikda (`main.py`) ishga tushadi va Mini App'ga
quyidagi endpoint'larni taqdim etadi:

  POST /api/login    — login+password+initData → {ok, role, user}
  GET  /api/health   — server tirikmi tekshirish

Xavfsizlik:
  • Telegram `initData` HMAC-SHA256 imzo bilan tekshiriladi (sender'ning
    haqiqiy Telegram foydalanuvchi ekanligini Telegram tasdiqlaydi).
  • Parol PBKDF2-SHA256 bilan tekshiriladi.
  • Rate limit (`attempt_tracker`) — bot ichidagi va API'dagi urinishlar
    birgalikda hisoblanadi.
  • Strict telegram_id binding — hisob boshqa TG ID ga biriktirilgan
    bo'lsa, API kirishni rad qiladi.
  • CORS — faqat sozlangan origin (Netlify) ga ruxsat.
"""

from __future__ import annotations

import os
import time
import asyncio
import mimetypes
import json as _json
import sqlite3
import logging
import secrets
from urllib.parse import urlparse
from aiohttp import web

from bot.auth import (
    verify_login, attempt_tracker, verify_telegram_init_data,
    LoginAttemptTracker,
)
from bot.config import (BOT_TOKEN, CORS_ALLOW_ORIGIN, GLAVNIY_ADMIN_ID,
                        CHANNEL_ID, CONTACT_PHONE_1, CONTACT_PHONE_2,
                        SHOP_LOCATION)
from bot import payment_relay

logger = logging.getLogger(__name__)

ROUTES = web.RouteTableDef()


# Mahsulot rasmiga AI yozadigan IKKI TILLI (o'zbek + rus) jozibali tavsif.
# Faqat tovar haqida — narx/do'kon/raqam yozmaydi (ular footerda fiksir).
# Ko'rsatma QISQA: uzun, raqamlangan ko'rsatmani kuchsiz modellar javob deb
# qaytarib yuboradi (2026-08-05: kanalga aynan ko'rsatmaning o'zi chiqib ketdi).
_IMG_PROMPT = (
    "Sotuvdagi tovar rasmi. Ikki qator yoz:\n"
    "🇺🇿 <o'zbekcha bitta qisqa reklama jumlasi>\n"
    "🇷🇺 <shuning ruschasi>\n"
    "O'zbekcha qator SOF O'ZBEK TILIDA, LOTIN yozuvida bo'lsin. "
    "Turkcha so'z va harflar (ş, ç, ğ, ı, ü, ö) ISHLATMA, kirill yozma.\n"
    "Narx, do'kon nomi, telefon yozma. Boshqa hech narsa qo'shma."
)

# Ko'rsatmaning o'zi qaytib kelganini bildiruvchi so'zlar.
_ECHO_WORDS = ("qator yoz", "1-qator", "2-qator", "yozma", "qo'shma",
               "shuning ruschasi", "reklama jumlasi", "sotuvchi uslubidagi",
               "qisqa tavsif", "emoji bilan", "bilan boshla —")


def _desc_sane(text: str) -> bool:
    """Umumiy tekshiruv: bu matn tavsifga o'xshaydimi (til muhim emas)."""
    t = (text or "").strip()
    if len(t) < 12 or len(t) > 700:
        return False
    low = t.lower()
    if any(w in low for w in _ECHO_WORDS):
        return False
    if "<" in t and ">" in t:          # to'ldirilmagan shablon: <...>
        return False
    return True


# O'zbek lotin alifbosida YO'Q harflar — ular turkcha/nemischa matn belgisi.
_TURKISH_CH = "şŞğĞıİçÇöÖüÜ"
# Turkchada bor, o'zbekchada boshqacha yoziladigan aniq so'zlar.
_TURKISH_WORDS = (" ürün", " için", " ile ", " çok", " daha ", "mükemmel",
                  "kalite", "kullanım", "satın", "fiyat", "yüksek", "güçlü",
                  " ve ", "temizleyici", "temizlik", " sizin ", " bir ürün")


def _uzbek_line_ok(line: str) -> bool:
    """O'zbekcha qator haqiqatan o'zbekcha (lotin) va toza yozilganmi.

    Modellar o'zbekchani ba'zan TURKCHA aralashtirib yozadi ("peçetkalar",
    "ürün için") yoki KIRILLDA qaytaradi ("Мистер Пропернинг…"). Kanal
    lotin yozuvida, shuning uchun bunday javob rad etiladi va zanjir
    keyingi modelga o'tadi.
    """
    if any(ch in line for ch in _TURKISH_CH):
        return False
    low = " " + line.lower() + " "
    if any(w in low for w in _TURKISH_WORDS):
        return False
    # Kirill harflari o'zbekcha qatorda bo'lmasligi kerak (ruscha qator alohida)
    cyr = sum(1 for ch in line if "Ѐ" <= ch <= "ӿ")
    return cyr <= 2


def _desc_ok(text: str) -> bool:
    """Kanalga TAYYOR tavsifmi: ikki tilli (🇺🇿 + 🇷🇺), toza o'zbekcha, aks-sado emas.

    Nima uchun bayroq talab qilinadi: kuchsiz model (llava) ko'rsatmani
    tushunmay INGLIZCHA bitta jumla qaytaradi — 2026-08-05 da kanalga aynan
    shunday chiqdi. Bayroq bo'lmasa zanjir keyingi modelga o'tadi, hech biri
    uddasidan chiqmasa esa tavsif matnli model tomonidan qayta yoziladi.
    """
    if not _desc_sane(text):
        return False
    if "🇺🇿" not in text or "🇷🇺" not in text:
        return False
    uz = next((ln for ln in text.splitlines() if "🇺🇿" in ln), "")
    ru = next((ln for ln in text.splitlines() if "🇷🇺" in ln), "")
    if not _uzbek_line_ok(uz):
        return False
    # Ruscha qatorda kirill bo'lishi shart — aks holda tarjima qilinmagan.
    return sum(1 for ch in ru if "Ѐ" <= ch <= "ӿ") >= 5


# Model javob oldiga qo'yib yuboradigan yorliqlar ("O'zbekcha tavsif: …").
_LABELS = _re_labels = (
    r"o['‘’`]?zbekcha(\s+tavsif)?", r"uzbekcha(\s+tavsif)?", r"uzbek",
    r"ruscha(\s+tavsif)?", r"russian", r"русское\s+описание", r"на\s+русском",
    r"tavsif", r"описание", r"\d\s*[-–—.)]?\s*qator",
)


def _clean_desc(text: str) -> str:
    """Tavsifni tozalaydi: yorliqlar, qator raqamlari, qo'shtirnoq, bo'sh joy."""
    import re as _re
    label_re = _re.compile(r"^\s*(?:" + "|".join(_LABELS) + r")\s*[:：-]\s*",
                           _re.I)
    out = []
    for line in (text or "").strip().splitlines():
        line = line.strip()
        # Bayroq yorliqdan oldin turishi mumkin: "🇺🇿 O'zbekcha tavsif: ..."
        flag = ""
        if line[:2] in ("🇺", "🇷") or line.startswith(("🇺🇿", "🇷🇺")):
            flag = line[:2] if not line.startswith(("🇺🇿", "🇷🇺")) else line[:2]
        for pref in ("🇺🇿", "🇷🇺"):
            if line.startswith(pref):
                flag, line = pref, line[len(pref):].strip()
                break
        line = label_re.sub("", line)
        line = _re.sub(r"^\s*[-*•]\s*", "", line).strip(' "\'`')
        if line:
            out.append((flag + " " + line).strip())
    return "\n".join(out[:2])          # ko'pi bilan ikki qator (uz + ru)


async def _gemini_describe_image(fpath: str, product_name: str = "") -> str:
    """Rasmdan tavsif — ko'p provayderli zanjir (bot/ai_engine.py).

    Nomi tarixiy sababdan "gemini" bo'lib qolgan; aslida zanjirdagi istalgan
    provayderni ishlatadi va biri yiqilsa keyingisiga o'tadi. Birortasi
    ishlamasa '' qaytaradi — post baribir ketaveradi (tavsifsiz).
    """
    from bot import ai_engine as ai_vision
    loop = asyncio.get_event_loop()
    try:
        # 1) Rasm modeli to'g'ridan-to'g'ri ikki tilli tavsif yozsin.
        # allow_text_fallback=False — RASMNI ko'rmagan model tavsif yozmasin.
        raw = await loop.run_in_executor(
            None, lambda: ai_vision.describe(fpath, _IMG_PROMPT, product_name,
                                             validate=_desc_ok,
                                             allow_text_fallback=False)
        )
        desc = _clean_desc(raw)
        if _desc_ok(desc):
            return desc

        # 2) Uddasidan chiqmadi (kuchsiz model ingliz tilida bitta jumla
        #    yozadi). Undan RASMDA NIMA borligini so'raymiz, so'ng MATNLI
        #    model uni o'zbekcha+ruscha reklama jumlasiga aylantiradi.
        seen = await loop.run_in_executor(
            None, lambda: ai_vision.describe(
                fpath, "Describe this product in one short sentence.",
                product_name, validate=_desc_sane,
                allow_text_fallback=False))
        seen = _clean_desc(seen)
        if not _desc_sane(seen):
            return ""
        rewrite = (
            f"Tovar: {product_name or '—'}\n"
            f"Tavsif: {seen}\n\n"
            "Shu tovar uchun do'kon kanaliga ikki qator yoz:\n"
            "🇺🇿 <o'zbekcha bitta qisqa reklama jumlasi>\n"
            "🇷🇺 <shuning ruschasi>\n"
            "Narx, do'kon nomi, telefon yozma."
        )
        out = await loop.run_in_executor(
            None, lambda: ai_vision.chat(rewrite, validate=_desc_ok))
        out = _clean_desc(out)
        if _desc_ok(out):
            logger.info("🤖 Tavsif matnli model tomonidan qayta yozildi")
            return out
        return ""
    except Exception as e:
        logger.warning(f"🤖 AI tavsif xatosi: {e}")
        return ""


async def _post_product_to_channel(db, p: dict, pid: int,
                                   silent: bool = False):
    """Mahsulot saqlanganidan keyin kanalga post yuboradi.
    Rasm bo'lmasa — yuklamaydi. AI bilan qisqa tavsif yozadi.

    `silent=True` — obunachilarga ovozli bildirishnoma bormaydi. To'plamli
    qayta e'lon qilishda (79 ta post) shu rejim ishlatiladi: kanal yangilanadi,
    lekin odamlarning telefoni tinmay chirillamaydi."""
    try:
        if not db.is_channel_enabled():
            return
        bot = getattr(db, "_bot", None)
        if not bot or not CHANNEL_ID:
            return

        # Rasmni topamiz (image_url — local fayl)
        image_url = p.get("image_url", "") or ""
        if not image_url:
            return  # Rasm yo'q — kanalga yubormaymiz

        fpath = os.path.join(
            UPLOAD_DIR,
            image_url.lstrip("/").replace("api/img/", "").lstrip("/")
        )
        if not os.path.exists(fpath):
            logger.warning(f"📢 Rasm fayli topilmadi: {fpath}")
            return

        # Tavsif uchun ASL rasm (bo'lsa) — unda tovar atrofidagi kontekst ham
        # bor, AI aniqroq yozadi. Kanalga esa BRENDLANGAN rasm ketadi.
        desc_path = fpath
        orig_url = p.get("image_orig_url", "") or ""
        if orig_url:
            op = os.path.join(
                UPLOAD_DIR, orig_url.lstrip("/").replace("api/img/", "").lstrip("/"))
            if os.path.exists(op):
                desc_path = op

        # Gemini bilan rasm tahlili
        ai_desc = await _gemini_describe_image(desc_path, p.get("name", "") or "")

        from database._formatters import _fmt_product
        # Bo'lim nomi — kaptiondagi hashtag uchun (#santexnika kabi).
        if p.get("category_id") and not p.get("category_name"):
            try:
                cat = await db.get_category(int(p["category_id"]))
                p = dict(p, category_name=(cat or {}).get("name", ""))
            except Exception:
                pass
        buy_url = (f"https://t.me/{db._bot_username}?start=buy_{pid}"
                   if getattr(db, "_bot_username", "") else "")
        cap = _fmt_product(p, ai_desc=ai_desc, buy_url=buy_url)
        markup = db._buy_markup(pid)

        from aiogram.types import FSInputFile, InputMediaPhoto
        # Eski kanal postini (albom bo'lsa — hammasini) o'chirib, yangisini
        # yuboramiz. Eski postlarda faqat bitta id saqlangan.
        old_ids = []
        try:
            old_ids = _json.loads(p.get("channel_msg_ids") or "[]")
        except (ValueError, TypeError):
            old_ids = []
        if not old_ids and p.get("channel_msg_id"):
            old_ids = [p["channel_msg_id"]]
        for m in old_ids:
            try:
                await bot.delete_message(CHANNEL_ID, m)
            except Exception:
                pass

        # ASL rasm ham bo'lsa — IKKI rasmli albom: 1) katalog ko'rinishi
        # (foni kesilgan), 2) tovarning haqiqiy surati. Telegram albomga
        # tugma qo'yishga ruxsat bermaydi, shuning uchun "Sotib olish"
        # havolasi kaptionning ichida (`buy_url`) beriladi.
        second = desc_path if desc_path != fpath else ""
        if second:
            msgs = await bot.send_media_group(CHANNEL_ID, [
                InputMediaPhoto(media=FSInputFile(fpath), caption=cap,
                                parse_mode="HTML"),
                InputMediaPhoto(media=FSInputFile(second)),
            ], disable_notification=silent)
            msg = msgs[0]
            new_ids = [m.message_id for m in msgs]
        else:
            msg = await bot.send_photo(
                CHANNEL_ID, FSInputFile(fpath), caption=cap,
                parse_mode="HTML", reply_markup=markup,
                disable_notification=silent,
            )
            new_ids = [msg.message_id]
        tg_file_id = msg.photo[-1].file_id
        mid = msg.message_id

        # «Qanday ishlatiladi» — post ostiga IZOH bo'lib yoziladi (kanal
        # lentasida alohida xabar bo'lib turmaydi). Izoh muhokama guruhi
        # orqali ketadi: `comments` moduli guruhdagi nusxa kelishini kutadi.
        # Kerak bo'lmasa .env da POST_USAGE_GUIDE=0 — qo'llanma bazada
        # qolaveradi (Mini App va saytda ko'rinadi), lekin kanalga chiqmaydi.
        info_txt = (p.get("usage_info") or "").strip()
        if info_txt and os.getenv("POST_USAGE_GUIDE", "1").strip() != "0":
            from bot import comments
            await comments.drop(bot, p.get("usage_comment") or "")

            def _save_ref(chat_id, msg_id, _pid=pid):
                # `update_product` EMAS: u kanal kaptionini qayta yozib
                # yuboradi (AI tavsifi va "Sotib olish" havolasi yo'qoladi).
                from database._helpers import DB_PATH
                conn = sqlite3.connect(DB_PATH, timeout=10)
                try:
                    conn.execute(
                        "UPDATE products SET usage_comment=? WHERE id=?",
                        (f"{chat_id}:{msg_id}", _pid))
                    conn.commit()
                finally:
                    conn.close()

            await comments.queue(bot, mid, info_txt, _save_ref)

        ids_json = _json.dumps(new_ids)
        # _refresh_product ni chaqirmaslik uchun to'g'ridan SQL ga yozamiz
        # AI tavsifini description ga ham saqlaymiz (bo'sh bo'lsa)
        from database._helpers import DB_PATH
        def _save_ids():
            conn = sqlite3.connect(DB_PATH, timeout=10)
            try:
                cur_desc = (conn.execute(
                    "SELECT description FROM products WHERE id=?", (pid,)
                ).fetchone() or [""])[0] or ""
                if ai_desc and not cur_desc.strip():
                    conn.execute(
                        "UPDATE products SET image_file_id=?, channel_msg_id=?, "
                        "channel_msg_ids=?, description=? WHERE id=?",
                        (tg_file_id, mid, ids_json, ai_desc, pid)
                    )
                else:
                    conn.execute(
                        "UPDATE products SET image_file_id=?, channel_msg_id=?, "
                        "channel_msg_ids=? WHERE id=?",
                        (tg_file_id, mid, ids_json, pid)
                    )
                conn.commit()
            finally:
                conn.close()
        await asyncio.get_event_loop().run_in_executor(None, _save_ids)
        logger.info(f"📢 Kanalga yuborildi: pid={pid}, mid={mid}")
    except Exception as e:
        logger.warning(f"📢 Kanalga yuborishda xato: {e}")

# Session token store — SQLite'ga yoziladi, bot qayta ishganda ham saqlanadi
_SESSION_TTL = 1800  # 30 daqiqa

def _sess_db() -> sqlite3.Connection:
    from database._helpers import DB_PATH
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS web_sessions (
        token TEXT PRIMARY KEY,
        data  TEXT NOT NULL,
        expires REAL NOT NULL
    )""")
    conn.commit()
    return conn

def _create_session(auth: dict, ttl: float = _SESSION_TTL) -> str:
    """Yangi session token yaratib, SQLite'ga saqlaydi.
    ttl — token amal qilish muddati (sekundda). Desktop kassa uchun uzunroq."""
    token = secrets.token_urlsafe(32)
    expires = time.time() + ttl
    conn = _sess_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO web_sessions(token, data, expires) VALUES(?,?,?)",
            (token, _json.dumps(auth), expires)
        )
        conn.execute("DELETE FROM web_sessions WHERE expires < ?", (time.time(),))
        conn.commit()
    finally:
        conn.close()
    return token

def _check_session(token: str) -> dict | None:
    """Token haqiqiy va muddati o'tmagan bo'lsa auth dict qaytaradi.
    Desktop (kassa) sessiyalari SLIDING — har ishlatilganda muddati 30 kunga
    uzayadi, shunda kassa muntazam ishlatilsa qayta kirish kerak bo'lmaydi."""
    if not token:
        return None
    conn = _sess_db()
    try:
        row = conn.execute(
            "SELECT data, expires FROM web_sessions WHERE token=?", (token,)
        ).fetchone()
        if not row:
            return None
        if row["expires"] < time.time():
            conn.execute("DELETE FROM web_sessions WHERE token=?", (token,))
            conn.commit()
            return None
        data = _json.loads(row["data"])
        # Desktop sessiya — muddatni sirpanuvchi qilib uzaytiramiz. Har so'rovda
        # emas (kassa har 12s so'rov yuboradi), faqat farq 1 soatdan oshsa yozamiz.
        if data.get("desktop"):
            new_exp = time.time() + _DESKTOP_SESSION_TTL
            if new_exp - row["expires"] > 3600:
                conn.execute("UPDATE web_sessions SET expires=? WHERE token=?", (new_exp, token))
                conn.commit()
        return data
    finally:
        conn.close()

# Yuklangan mahsulot rasmlari — ma'lumotlar papkasida (DATA_DIR), kod
# daraxtida EMAS. `.env` dagi DATA_DIR / UPLOAD_DIR orqali sozlanadi; aks holda
# repo ildizidagi `data/uploads`. (database._helpers ni import qilmaymiz — u
# jonli pos.db ni ochadi; shuning uchun DATA_DIR shu yerda mustaqil hisoblanadi.)
_DATA_DIR = os.path.abspath(os.environ.get("DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
))
UPLOAD_DIR = os.path.abspath(os.environ.get("UPLOAD_DIR") or os.path.join(_DATA_DIR, "uploads"))
UPLOAD_DIR = os.path.realpath(UPLOAD_DIR)  # Absolute path for security
os.makedirs(UPLOAD_DIR, exist_ok=True)


# ─── CORS middleware ────────────────────────────────────────────────────────

def _is_origin_allowed(origin: str, request: "web.Request | None" = None) -> bool:
    """Origin whitelist tekshiruvi. CORS_ALLOW_ORIGIN'ni whitelist sifatida
    qabul qilamiz (vergul bilan ajratilgan).
    Qo'shimcha: ngrok, localhost va so'rovning o'z hosti avtomatik ruxsat."""
    if not origin:
        return False

    # Same-host: so'rov qaysi hostga kelsa, o'sha origin avtomatik ruxsat
    if request is not None:
        req_host = request.host or ""  # "example.ngrok-free.app" yoki "localhost:8765"
        origin_host = origin.split("://")[-1].rstrip("/")
        if req_host and origin_host == req_host:
            return True

    # ngrok domenlarini avtomatik ruxsat (*.ngrok-free.app, *.ngrok.io)
    origin_clean = origin.split("://")[-1].rstrip("/")
    if origin_clean.endswith(".ngrok-free.app") or origin_clean.endswith(".ngrok.io") \
            or origin_clean.endswith(".ngrok-free.dev"):
        return True

    # localhost (local test)
    if origin_clean.startswith("localhost") or origin_clean.startswith("127.0.0.1"):
        return True

    allowed = (CORS_ALLOW_ORIGIN or "").strip()
    if not allowed:
        return False
    if allowed == "*":
        return True
    # Vergul bilan ajratilgan domenlar (explicit whitelist)
    for domain in allowed.split(","):
        domain = domain.strip()
        if domain == origin:
            return True
        if origin.endswith(domain):
            return True
    return False


def _cors_headers(request: web.Request = None) -> dict:
    """CORS sarlavhalari. Faqat whitelist'dagi origin'larga ruxsat."""
    req_origin = ""
    if request is not None:
        req_origin = request.headers.get("Origin", "") or ""
    
    # /api/img/* va /api/shop/* — OCHIQ resurslar (sayt uchun), CORS erkin
    req_path = request.path if request else ""
    if req_path.startswith("/api/img/") or req_path.startswith("/api/shop/"):
        # Rasm public resource — barcha origin'lardan access
        allow = req_origin if req_origin else "*"
    elif _is_origin_allowed(req_origin, request):
        allow = req_origin
    else:
        # Protected endpoint — Origin header bo'sh bo'lsa ruxsat bermaydi
        allow = ""
    
    return {
        "Access-Control-Allow-Origin": allow,
        "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
        "Access-Control-Allow-Headers":
            "Content-Type, ngrok-skip-browser-warning, X-Telegram-Init-Data, X-Session-Token",
        "Access-Control-Max-Age": "86400",
        "Vary": "Origin",
    }


@web.middleware
async def cors_middleware(request: web.Request, handler):
    if request.method == "OPTIONS":
        return web.Response(status=204, headers=_cors_headers(request))
    try:
        resp = await handler(request)
        # ✅ CORS headers'ni successful response'larni qo'sh!
        resp.headers.update(_cors_headers(request))
        return resp
    except web.HTTPException as e:
        # Allow CORS even on HTTP exceptions
        e.headers.update(_cors_headers(request))
        raise
    except Exception:
        # Ilgari xato JIM yutilardi — 500 qaytardi-yu, logda hech nima yo'q edi.
        # Nosozlikni topish uchun to'liq izni yozamiz (foydalanuvchiga baribir
        # faqat umumiy xabar ketadi).
        logger.exception("💥 So'rovda xato: %s %s", request.method, request.path)
        body = _json.dumps({"ok": False, "error": "server error"})
        return web.Response(
            status=500, body=body, content_type="application/json",
            headers=_cors_headers(request)
        )
    for k, v in _cors_headers(request).items():
        resp.headers[k] = v
    return resp


# ─── Autentifikatsiya yordamchisi (har so'rovda initData orqali) ────────────

async def _authenticate(request: web.Request, db):
    """Har bir himoyalangan so'rov uchun: session token yoki initData ni
    tekshirib, foydalanuvchini (admin yoki mijoz) va rolini aniqlaydi.

    Autentifikatsiya tartibi:
      1. X-Session-Token header (login'dan keyin saqlanadi)
      2. X-Telegram-Init-Data header
      3. GET query: ?initData=...
      4. POST body (JSON): {"initData": "..."}

    Qaytaradi:
      None — autentifikatsiya muvaffaqiyatsiz
      {"role": "admin"|"client", "tg_id": int, "user": dict, "is_glavniy": bool}
    """
    # 1) Session token orqali (eng tez, login'dan keyin ishlaydi)
    session_token = request.headers.get("X-Session-Token", "") or ""
    if session_token:
        auth = _check_session(session_token)
        if auth:
            return auth

    init_data = request.headers.get("X-Telegram-Init-Data", "") or ""
    if not init_data:
        init_data = request.query.get("initData", "") or ""
    if not init_data and request.method == "POST":
        try:
            body = await request.json()
            init_data = body.get("initData", "") or ""
            request["_json_body"] = body   # keyin qayta o'qimaslik uchun kesh
        except Exception:
            pass
    parsed = verify_telegram_init_data(init_data, BOT_TOKEN)
    if not parsed:
        return None
    try:
        tg_id = int((parsed.get("user") or {}).get("id") or 0)
    except (TypeError, ValueError):
        tg_id = 0
    if not tg_id:
        return None

    is_glavniy = (tg_id == GLAVNIY_ADMIN_ID)
    if is_glavniy or await db.is_admin(tg_id):
        admin = await db.get_admin(tg_id)
        return {
            "role": "admin", "tg_id": tg_id,
            "user": admin or {"telegram_id": tg_id, "full_name": "Admin"},
            "is_glavniy": is_glavniy,
        }
    client = await db.get_client_by_tg(tg_id)
    if client:
        return {"role": "client", "tg_id": tg_id, "user": client,
                "is_glavniy": False}
    return None


async def _read_json(request: web.Request) -> dict:
    """Body JSON ni keshlangan holatdan yoki qaytadan o'qiydi."""
    if "_json_body" in request:
        return request["_json_body"]
    try:
        return await request.json()
    except Exception:
        return {}


def _err(msg: str, status: int = 400):
    return web.json_response({"ok": False, "error": msg}, status=status)


# ─── Narx hisoblash (rolga qarab) ───────────────────────────────────────────

def _price_pair(p: dict, ctype: str) -> tuple:
    """(USD, so'm) — mijoz turiga qarab dona yoki optom narxi."""
    if str(ctype).startswith("opt"):
        whs_usd = float(p.get("wholesale_price_usd", 0) or 0)
        whs_sum = float(p.get("wholesale_price", 0) or 0)
        if whs_usd > 0 or whs_sum > 0:
            return whs_usd, whs_sum
    return float(p.get("sell_price_usd", 0) or 0), float(p.get("sell_price", 0) or 0)


def _plain(html_text: str) -> str:
    """Telegram HTML → oddiy matn (Mini App uni matn sifatida ko'rsatadi)."""
    import html as _html
    import re as _re
    return _html.unescape(_re.sub(r"<[^>]+>", "", html_text or "")).strip()


def _product_json(p: dict, role: str, ctype: str) -> dict:
    usd, summ = _price_pair(p, ctype)
    # Mijozga CHIROYLI nom (display_name), adminga ASL nom + ikkalasi ham.
    # Sabab: bazadagi nomlar imlosi turlicha ("Zakllopka", "shipatel"), lekin
    # ularni o'zgartirib bo'lmaydi — kassada xodimlar o'sha nom bilan taniydi.
    real_name = p.get("name", "") or ""
    disp = (p.get("display_name") or "").strip() or real_name
    out = {
        "id": p["id"],
        "name": disp if role == "client" else real_name,
        "description": p.get("description", ""),
        "qty": float(p.get("qty", 0) or 0),
        "unit": p.get("unit", "dona"),
        "barcode": p.get("barcode", "") or "",
        "price_usd": usd,
        "price_sum": summ,
        "image_url": p.get("image_url", "") or "",
        # ASL (foni kesilmagan) rasm — mijoz "asl rasm"ni ko'ra olishi uchun.
        # Brendlanmagan eski tovarlarda bo'sh bo'ladi.
        "image_orig_url": p.get("image_orig_url", "") or "",
        # "Qayerda ishlatiladi" — Mini App kartochkasida ham ko'rsatiladi.
        "usage_tags": [t.strip() for t in (p.get("usage_tags") or "").split(",")
                       if t.strip()],
        # «Qanday ishlatiladi» qo'llanmasi — kanal posti uchun Telegram HTML
        # shaklida saqlanadi; Mini App'ga oddiy matn bo'lib beriladi.
        "usage_info": _plain(p.get("usage_info") or ""),
        "has_image": bool(p.get("image_file_id") or p.get("image_url")),
        "category_id": int(p.get("category_id") or 0),
    }
    if role == "admin":
        # Admin'ga tannarx va optom ham ko'rinadi
        out["cost_price_sum"] = float(p.get("cost_price", 0) or 0)
        out["cost_price_usd"] = float(p.get("cost_price_usd", 0) or 0)
        out["wholesale_sum"] = float(p.get("wholesale_price", 0) or 0)
        out["wholesale_usd"] = float(p.get("wholesale_price_usd", 0) or 0)
        out["sell_price_sum"] = float(p.get("sell_price", 0) or 0)
        out["sell_price_usd"] = float(p.get("sell_price_usd", 0) or 0)
        # Biriktirilgan yetkazib beruvchi (0 = biriktirilmagan). Nomini admin UI
        # suppliers ro'yxatidan id bo'yicha topadi.
        out["display_name"] = (p.get("display_name") or "").strip()
        out["supplier_id"] = int(p.get("supplier_id", 0) or 0)
        # Tahrirda saqlash to'liq obyektni qaytarib yuboradi — kategoriya
        # yo'qolib qolmasligi uchun uni ham beramiz.
        out["category_id"] = int(p.get("category_id", 0) or 0)
    return out


# ─── Health ─────────────────────────────────────────────────────────────────

@ROUTES.get("/api/health")
async def health(request: web.Request):
    return web.json_response({"ok": True, "service": "pos-bot-api"})


# ─── Joriy foydalanuvchi ────────────────────────────────────────────────────

@ROUTES.get("/api/me")
async def api_me(request: web.Request):
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth:
        return _err("Avtorizatsiya talab qilinadi", 401)
    u = auth["user"]
    role = auth["role"]
    rate = _db.get_usd_rate()
    data = {
        "ok": True,
        "role": role,
        "is_glavniy": auth.get("is_glavniy", False),
        "rate": rate,
    }
    if role == "admin":
        data["name"] = u.get("full_name") or u.get("username") or "Admin"
        data["admin_role"] = u.get("role", "full")
    else:
        data["name"] = u.get("shop_name") or u.get("username") or "Mijoz"
        data["client_type"] = (u.get("client_type") or "dona").lower()
        data["debt_sum"] = float(u.get("debt", 0) or 0)
        data["debt_usd"] = float(u.get("debt_usd", 0) or 0)
    return web.json_response(data)


# ─── Mahsulotlar ────────────────────────────────────────────────────────────

@ROUTES.get("/api/client/top")
async def api_client_top(request: web.Request):
    """Mijoz bosh sahifasi: ko'p sotilayotgan va yangi tovarlar.

    Ikkala ro'yxat ham FAQAT haqiqiy ma'lumotdan: "ko'p sotilayotgan" —
    oxirgi 30 kunda real sotilganlar, "yangi" — 30 kun ichida qo'shilganlar.
    Ma'lumot bo'lmasa ro'yxat bo'sh qaytadi va Mini App bo'limni yashiradi.
    Ilgari bo'sh joy qoldiq tovarlar bilan (alifbo tartibida) to'ldirilardi —
    mijozga "0 shina" ko'p sotilayotgandek ko'rinardi, bu yolg'on edi."""
    from database.channel_db import db as _db
    from bot import api_recommend
    auth = await _authenticate(request, _db)
    if not auth:
        return _err("Avtorizatsiya talab qilinadi", 401)
    idx = await api_recommend._index(_db)
    top = api_recommend.recent_top(idx, limit=12)
    import datetime
    cutoff = datetime.datetime.now() - datetime.timedelta(days=30)
    new_prods = []
    for p in idx.items.values():
        try:
            dt = datetime.datetime.fromisoformat(str(p.get("created_at") or "").replace("Z", ""))
        except ValueError:
            continue
        if dt.replace(tzinfo=None) > cutoff:
            new_prods.append((dt.replace(tzinfo=None), p))
    new_prods = [p for _, p in sorted(new_prods, key=lambda x: x[0], reverse=True)[:12]]

    u = auth.get("user") or {}
    ctype = (u.get("client_type") or "dona").lower() if auth["role"] == "client" else "dona"

    def _clip(lst):
        out = []
        for p in lst:
            j = _product_json(p, "client", ctype)
            j["sold"] = float(idx.sales.get(p["id"], 0) or 0)
            out.append(j)
        return out
    return web.json_response({
        "ok": True, "top": _clip(top), "new": _clip(new_prods),
        "orders_enabled": _db.is_client_orders_enabled(),
        "shop": {"phone": CONTACT_PHONE_1, "phone2": CONTACT_PHONE_2,
                 "location": SHOP_LOCATION},
    })


@ROUTES.post("/api/client/consult")
async def api_client_consult(request: web.Request):
    """Klient AI sotuvchiga savol beradi."""
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth:
        return _err("Avtorizatsiya talab qilinadi", 401)
    body = await _read_json(request)
    question = (body.get("question") or "").strip()
    if not question:
        return _err("Savol bo'sh")
    try:
        from bot.gemini_analyzer import expand_query_keywords, consult_client
        keywords = await expand_query_keywords(question)
        candidates = []
        seen = set()
        for kw in keywords[:6]:
            rows = await _db.search_products(kw, limit=6)
            for p in rows:
                if p["id"] not in seen:
                    seen.add(p["id"])
                    candidates.append(p)
        answer = await consult_client(question, candidates)
        import re
        ids = re.findall(r"#(\d{2,10})", answer or "")
        mentioned = []
        for sid in ids[:5]:
            p = await _db.get_product(int(sid))
            if p:
                mentioned.append(_product_json(p, "client", "dona"))
        return web.json_response({"ok": True, "answer": answer, "products": mentioned})
    except Exception as e:
        return _err(f"AI xato: {e}", 500)


@ROUTES.get("/api/products")
async def api_products(request: web.Request):
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth:
        return _err("Avtorizatsiya talab qilinadi", 401)

    role = auth["role"]
    ctype = "dona"
    if role == "client":
        ctype = (auth["user"].get("client_type") or "dona").lower()

    q = (request.query.get("q") or "").strip()
    try:
        page = max(0, int(request.query.get("page", "0") or 0))
    except ValueError:
        page = 0
    # Desktop admin katalogni to'liq oladi — `limit` bilan sahifa hajmini
    # kattalashtirish mumkin (20 taliklarda yuzlab ketma-ket so'rov sekin).
    page_size = 20
    try:
        req_ps = int(request.query.get("limit", "0") or 0)
    except ValueError:
        req_ps = 0
    if req_ps > 0:
        page_size = min(req_ps, 500)

    if q:
        prods = await _db.search_products(q, limit=200)
        # qidiruvni jurnalga yozamiz (AI analitika uchun)
        try:
            await _db.log_search(auth["tg_id"], q, len(prods),
                                  source=f"miniapp_{role}")
        except Exception:
            pass
    else:
        prods = await _db.get_all_products(active_only=True)
        # Mijozga faqat qoldig'i borlar; adminga hammasi
        if role == "client":
            prods = [p for p in prods if (p.get("qty", 0) or 0) > 0]

    # Kategoriya bo'yicha saralash (qidiruvga ham qo'llanadi)
    try:
        cat_id = int(request.query.get("category", "") or 0)
    except ValueError:
        cat_id = 0
    if cat_id:
        prods = [p for p in prods if int(p.get("category_id") or 0) == cat_id]

    # Mahsulotlar SKU (id) raqami bo'yicha o'sish tartibida: 10000, 10001, ...
    prods.sort(key=lambda p: (p.get("id") or 0))

    total = len(prods)
    start = page * page_size
    page_items = prods[start:start + page_size]
    items = [_product_json(p, role, ctype) for p in page_items]
    return web.json_response({
        "ok": True,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": (start + page_size) < total,
        "rate": _db.get_usd_rate(),
        "role": role,
        "items": items,
    })


# ─── OCHIQ katalog (do'kon sayti uchun) ─────────────────────────────────────
# Saytga login shart emas: bu ma'lumot allaqachon Telegram kanalida ochiq
# (nom, narx, rasm, qoldiq). MAXFIY hech narsa berilmaydi — tannarx, optom
# narx, yetkazib beruvchi, mijoz ma'lumotlari bu yerga TUSHMAYDI.

_shop_cache = {"at": 0.0, "items": [], "cats": [],
               "all_products": 0, "all_categories": 0}
_SHOP_TTL = 60          # sekund: baza har so'rovda o'qilmasin


async def _shop_data(db):
    """Katalogni tayyorlaydi va 60 soniya keshda saqlaydi."""
    now = time.time()
    if now - _shop_cache["at"] < _SHOP_TTL and _shop_cache["items"]:
        return _shop_cache["items"], _shop_cache["cats"]

    prods = await db.get_all_products(active_only=True)
    cat_rows = await db.get_all_categories()
    cats = {c["id"]: c["name"] for c in cat_rows}
    # Saytdagi "bo'limlar" ro'yxati — BUTUN do'kon bo'yicha (rasmlilar emas).
    all_cats = [{"name": c["name"], "count": int(c.get("product_count") or 0)}
                for c in cat_rows if int(c.get("product_count") or 0) > 0]
    all_cats.sort(key=lambda x: -x["count"])
    items = []
    for p in prods:
        if not (p.get("image_url") or ""):
            continue                      # rasmsiz tovar saytda ko'rinmaydi
        qty = float(p.get("qty", 0) or 0)
        items.append({
            "id": p["id"],
            "name": (p.get("display_name") or p.get("name") or "").strip(),
            "price": float(p.get("sell_price", 0) or 0),
            "unit": p.get("unit", "dona"),
            "qty": qty,
            "image": p.get("image_url", ""),
            "image_orig": p.get("image_orig_url", "") or "",
            "category": cats.get(int(p.get("category_id") or 0), ""),
            "tags": [t.strip() for t in (p.get("usage_tags") or "").split(",")
                     if t.strip()],
            "description": (p.get("description") or "").strip(),
            # «Qanday ishlatiladi» — kanal postidagi bilan bir xil qo'llanma.
            "usage_info": _plain(p.get("usage_info") or ""),
        })
    items.sort(key=lambda x: (x["qty"] <= 0, -x["id"]))   # avval mavjudlari
    used = {}
    for it in items:
        if it["category"]:
            used[it["category"]] = used.get(it["category"], 0) + 1
    cat_list = [{"name": n, "count": k} for n, k in
                sorted(used.items(), key=lambda x: -x[1])]
    # Saytdagi raqamlar butun do'kon bo'yicha bo'lsin: rasmli tovar 84 ta,
    # lekin katalogda mingdan ortiq nom bor — "84 ta tovar" deyish do'konni
    # kichraytirib ko'rsatadi.
    _shop_cache.update({"at": now, "items": items, "cats": cat_list,
                        "all_cats": all_cats,
                        "all_products": len(prods),
                        "all_categories": len(cats)})
    return items, cat_list


@ROUTES.get("/api/shop/catalog")
async def api_shop_catalog(request: web.Request):
    """Ochiq katalog: sayt shu manzildan tovarlarni oladi (login kerak emas)."""
    from database.channel_db import db as _db
    items, cats = await _shop_data(_db)

    q = (request.query.get("q") or "").strip().lower()
    cat = (request.query.get("category") or "").strip()
    if q:
        items = [i for i in items
                 if q in i["name"].lower() or q in i["category"].lower()
                 or any(q in t.lower() for t in i["tags"])]
    if cat:
        items = [i for i in items if i["category"] == cat]

    try:
        page = max(0, int(request.query.get("page", "0") or 0))
        size = min(60, max(6, int(request.query.get("limit", "24") or 24)))
    except ValueError:
        page, size = 0, 24
    total = len(items)
    chunk = items[page * size:(page + 1) * size]
    return web.json_response({
        "ok": True, "total": total, "page": page,
        "has_more": (page + 1) * size < total,
        "categories": cats, "all_categories": _shop_cache.get("all_cats", []),
        "items": chunk,
        "stats": {"products": _shop_cache.get("all_products", 0),
                  "categories": _shop_cache.get("all_categories", 0),
                  "with_photo": len(_shop_cache.get("items") or [])},
        "shop": {
            "name": "Chinor",
            "phones": [x for x in (CONTACT_PHONE_1, CONTACT_PHONE_2) if x],
            "location": SHOP_LOCATION,
            "bot": _db._bot_username or "",
        },
    })


@ROUTES.get(r"/api/product/{pid:\d+}")
async def api_product_detail(request: web.Request):
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth:
        return _err("Avtorizatsiya talab qilinadi", 401)
    try:
        pid = int(request.match_info["pid"])
    except (KeyError, ValueError):
        return _err("Noto'g'ri ID")
    p = await _db.get_product_any(pid)
    if not p or not p.get("is_active", 1):
        return _err("Mahsulot topilmadi", 404)
    role = auth["role"]
    ctype = "dona"
    if role == "client":
        ctype = (auth["user"].get("client_type") or "dona").lower()
    out = _product_json(p, role, ctype)
    # Bo'lim nomi — Mini App kartochkasida ko'rsatiladi (id o'zi hech nima demaydi).
    if out.get("category_id"):
        try:
            cat = await _db.get_category(out["category_id"])
            out["category_name"] = (cat or {}).get("name", "")
        except Exception:
            out["category_name"] = ""
    return web.json_response({"ok": True, "product": out})


# ─── Mahsulot CRUD (admin) ──────────────────────────────────────────────────

async def _admin_guard(request, db, perm):
    """Admin + ruxsat tekshiruvi. (auth, error_response) qaytaradi."""
    from bot.permissions import has_permission
    auth = await _authenticate(request, db)
    if not auth:
        return None, _err("Avtorizatsiya talab qilinadi", 401)
    if auth["role"] != "admin":
        return None, _err("Faqat adminlar uchun", 403)
    if not await has_permission(db, auth["tg_id"], perm):
        return None, _err("Bu amal uchun ruxsat yo'q", 403)
    return auth, None


def _to_float(v, default=0.0):
    try:
        return float(str(v).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return default


@ROUTES.post("/api/product/save")
async def api_product_save(request: web.Request):
    """Yangi mahsulot qo'shadi (id=0) yoki mavjudini tahrirlaydi.
    Narxlar SO'M da keladi (admin so'mda kiritadi) — USD kanonik qiymat
    joriy kurs bo'yicha hisoblanadi."""
    from database.channel_db import db as _db
    from database._helpers import sum_to_usd
    body = await _read_json(request)
    pid = int(body.get("id") or 0)
    perm = "products_edit" if pid else "products_add"
    auth, err = await _admin_guard(request, _db, perm)
    if err is not None:
        return err

    name = (body.get("name") or "").strip()
    # Mijozga ko'rinadigan chiroyli nom (ixtiyoriy). Bo'sh bo'lsa asl nom
    # ishlatiladi — ya'ni tegilmasa hech narsa o'zgarmaydi.
    display_name = (body.get("display_name") or "").strip()[:120]
    if not name:
        return _err("Mahsulot nomi bo'sh")
    rate = _db.get_usd_rate()
    sell_sum = _to_float(body.get("sell_price_sum"))
    cost_sum = _to_float(body.get("cost_price_sum"))
    whs_sum  = _to_float(body.get("wholesale_sum"))
    qty      = _to_float(body.get("qty"))
    unit     = (body.get("unit") or "dona").strip() or "dona"
    barcode  = (body.get("barcode") or "").strip()
    desc     = (body.get("description") or "").strip()
    cat_id   = int(body.get("category_id") or 0)
    sup_id   = int(body.get("supplier_id") or 0)

    sell_usd = round(sum_to_usd(sell_sum, rate), 4) if sell_sum else 0
    cost_usd = round(sum_to_usd(cost_sum, rate), 4) if cost_sum else 0
    whs_usd  = round(sum_to_usd(whs_sum, rate), 4) if whs_sum else 0

    # Shtrix-kod boshqa mahsulotda bormi?
    if barcode:
        ex = await _db.get_product_by_barcode(barcode)
        if ex and ex.get("id") != pid:
            return _err(f"Bu shtrix-kod «{ex['name']}» da bor", 409)

    if pid:
        p = await _db.get_product_any(pid)
        if not p:
            return _err("Mahsulot topilmadi", 404)
        await _db.update_product(
            pid, name=name, display_name=display_name, description=desc,
            sell_price_usd=sell_usd, cost_price_usd=cost_usd,
            wholesale_price_usd=whs_usd,
            qty=qty, unit=unit, barcode=barcode,
            category_id=cat_id, supplier_id=sup_id,
        )
        new_id = pid
    else:
        new_id = await _db.add_product(
            name=name, description=desc,
            sell_price_usd=sell_usd, cost_price_usd=cost_usd,
            qty=qty, unit=unit, wholesale_price_usd=whs_usd,
            barcode=barcode, category_id=cat_id, supplier_id=sup_id,
        )
    p = await _db.get_product_any(new_id)

    # Saqlashdan keyin kanalga yuborish (rasm bo'lsa)
    # Rasm bo'lsa darrov e'lon qilamiz; rasm keyin yuklansa — `_enrich_product`
    # o'zi yuboradi (takror bo'lmasligi uchun `_last_post` tekshiriladi).
    asyncio.ensure_future(_post_debounced(_db, p or {}, new_id))

    return web.json_response({"ok": True, "id": new_id,
                              "product": _product_json(p, "admin", "dona")})


@ROUTES.post("/api/product/qty")
async def api_product_qty(request: web.Request):
    """Prixod — mavjud qoldiqqa qo'shadi (yoki ayiradi)."""
    from database.channel_db import db as _db
    auth, err = await _admin_guard(request, _db, "products_qty")
    if err is not None:
        return err
    body = await _read_json(request)
    pid = int(body.get("id") or 0)
    delta = _to_float(body.get("delta"))
    if not pid or delta == 0:
        return _err("ID yoki miqdor noto'g'ri")
    p = await _db.get_product_any(pid)
    if not p:
        return _err("Mahsulot topilmadi", 404)
    await _db.change_qty(pid, delta)
    p = await _db.get_product_any(pid)
    return web.json_response({"ok": True, "qty": float(p.get("qty", 0) or 0)})


# ─────────────────────────────────────────────────────────────────────────────
#  AI PRIXOD — nakladnoy (faktura) rasmidan tovarlarni o'qib, katalogga moslash
#  Hech narsa YOZMAYDI — faqat o'qib taklif qiladi (odam tekshirib saqlaydi).
# ─────────────────────────────────────────────────────────────────────────────

_INVOICE_PROMPT = (
    "Bu — do'kon uchun yetkazib beruvchidan kelgan NAKLADNOY (faktura/invoice) rasmi. "
    "Undagi har bir tovar qatorini diqqat bilan o'qi. "
    "FAQAT JSON massiv qaytar, boshqa hech narsa yozma. Har element shu ko'rinishda bo'lsin:\n"
    '{"name": "tovar nomi", "qty": miqdori_son, "unit": "dona", "price": dona_narxi_son}\n'
    "Qoidalar: narx va miqdor — faqat SON (vergul, probel, valyuta belgisini olib tashla). "
    "Agar qatorda faqat umumiy summa bo'lsa va dona narx ko'rinmasa, price = umumiy_summa / qty. "
    "Miqdor yoki narx ko'rinmasa 0 qo'y. unit ko'rinmasa 'dona' qo'y. "
    "Sarlavha, 'Jami'/'Itogo', izoh va imzо qatorlarini QO'SHMA — faqat haqiqiy tovarlar."
)


def _parse_json_array(text: str) -> list:
    """AI matnidan birinchi JSON massivni toza ajratib oladi (```...``` va
    qo'shimcha matnga chidamli)."""
    import json
    import re
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text).rsplit("```", 1)[0].strip()
    try:
        v = json.loads(text)
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            for k in ("items", "data", "rows", "products", "tovarlar"):
                if isinstance(v.get(k), list):
                    return v[k]
    except Exception:
        pass
    i, j = text.find("["), text.rfind("]")
    if i != -1 and j != -1 and j > i:
        try:
            v = json.loads(text[i:j + 1])
            if isinstance(v, list):
                return v
        except Exception:
            pass
    return []


async def _ai_extract_invoice(img_bytes: bytes) -> list:
    """Nakladnoy rasmidan [{name, qty, unit, price}] chiqaradi.
    Avval Gemini (strukturali JSON), keyin Groq vision (zaxira)."""
    import base64

    # 1) Gemini — response_mime_type=json bilan eng ishonchli
    try:
        from bot.config import GEMINI_API_KEY, GEMINI_MODEL
        if GEMINI_API_KEY:
            import importlib
            genai = importlib.import_module("google.genai")
            client = genai.Client(api_key=GEMINI_API_KEY)
            Part = genai.types.Part
            resp = await asyncio.get_event_loop().run_in_executor(None, lambda:
                client.models.generate_content(
                    model=GEMINI_MODEL or "gemini-2.0-flash",
                    contents=[
                        Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
                        _INVOICE_PROMPT,
                    ],
                    config={"response_mime_type": "application/json", "temperature": 0},
                )
            )
            items = _parse_json_array(resp.text or "")
            if items:
                logger.info(f"🧾 Gemini nakladnoy: {len(items)} qator o'qildi")
                return items
    except Exception as e:
        logger.warning(f"🧾 Gemini extraction xato: {e}")

    # 2) Groq vision (zaxira)
    try:
        from bot.config import GROQ_API_KEY
        if GROQ_API_KEY:
            from groq import Groq
            img_b64 = base64.b64encode(img_bytes).decode()
            client = Groq(api_key=GROQ_API_KEY)
            resp = await asyncio.get_event_loop().run_in_executor(None, lambda:
                client.chat.completions.create(
                    model="meta-llama/llama-4-scout-17b-16e-instruct",
                    messages=[{"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                        {"type": "text", "text": _INVOICE_PROMPT},
                    ]}],
                    max_tokens=2000,
                    temperature=0,
                )
            )
            items = _parse_json_array(resp.choices[0].message.content or "")
            if items:
                logger.info(f"🧾 Groq nakladnoy: {len(items)} qator o'qildi")
                return items
    except Exception as e:
        logger.warning(f"🧾 Groq extraction xato: {e}")

    return []


# Kirill → lotin (katalog ham lotin, ham kirill bo'lishi mumkin — kross-skript moslik)
_CYR2LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "ғ": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "j", "з": "z", "и": "i", "й": "y", "к": "k", "қ": "q", "л": "l", "м": "m",
    "н": "n", "о": "o", "ў": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "x", "ҳ": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh",
    "ъ": "", "ы": "i", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def _translit(s: str) -> str:
    return "".join(_CYR2LAT.get(ch, ch) for ch in s)


def _norm_name(s: str) -> str:
    """Nomni moslashtirish uchun normallashtiradi: registr, kirill→lotin, tinish, probel."""
    import re
    s = (s or "").lower().strip()
    s = s.replace("ʼ", "'").replace("`", "'").replace("'", "'").replace("ʻ", "'")
    s = _translit(s)
    s = re.sub(r"[^0-9a-z\s']", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _match_score(a: str, b: str) -> float:
    """0..1 oralig'ida ikki normallashgan nom o'xshashligi."""
    import difflib
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    jac = (len(ta & tb) / len(ta | tb)) if (ta | tb) else 0.0
    contains = 0.88 if (len(a) >= 3 and len(b) >= 3 and (a in b or b in a)) else 0.0
    return max(ratio, jac, contains)


@ROUTES.post("/api/prixod/scan")
async def api_prixod_scan(request: web.Request):
    """Nakladnoy rasmini AI bilan o'qib, har tovarni katalogga moslab qaytaradi.
    Yozuv qilmaydi — desktop-admin natijani ko'rsatadi, odam tekshirib saqlaydi."""
    from database.channel_db import db as _db
    auth, err = await _admin_guard(request, _db, "products_qty")
    if err is not None:
        return err

    # Rasmni multipartdan o'qiymiz (product/image bilan bir xil naqsh)
    file_bytes = None
    try:
        post = await request.post()
        field = post.get("photo")
        if field is not None:
            if hasattr(field, "file"):
                file_bytes = field.file.read()
            elif isinstance(field, (bytes, bytearray)):
                file_bytes = bytes(field)
    except Exception as e:
        logger.error(f"🧾 Faylni o'qib bo'lmadi: {e}")
        return _err(f"Faylni o'qib bo'lmadi: {e}", 400)
    if not file_bytes:
        return _err("Rasm topilmadi (photo maydoni bo'sh)")
    if len(file_bytes) > 25 * 1024 * 1024:
        return _err("Rasm 25MB dan katta")

    extracted = await _ai_extract_invoice(file_bytes)
    if not extracted:
        return _err("AI nakladnoyni o'qiy olmadi — rasm tiniqroq bo'lsin", 422)

    # Katalog indeksi (norm nom + barcode + eski narxlar)
    prods = await _db.get_all_products(active_only=True)
    idx, bybc = [], {}
    for p in prods:
        bc = str(p.get("barcode") or "").strip()
        rec = {
            "id": p["id"], "name": p.get("name", ""), "norm": _norm_name(p.get("name", "")),
            "unit": p.get("unit", "dona"), "qty": float(p.get("qty", 0) or 0), "barcode": bc,
            "cost_price_sum": float(p.get("cost_price", 0) or 0),
            "sell_price_sum": float(p.get("sell_price", 0) or 0),
            "wholesale_sum": float(p.get("wholesale_price", 0) or 0),
        }
        idx.append(rec)
        if bc:
            bybc[bc] = rec

    def _cand(rec, conf):
        return {
            "id": rec["id"], "name": rec["name"], "unit": rec["unit"], "qty": rec["qty"],
            "barcode": rec["barcode"], "cost_price_sum": rec["cost_price_sum"],
            "sell_price_sum": rec["sell_price_sum"], "wholesale_sum": rec["wholesale_sum"],
            "confidence": round(conf, 3),
        }

    out_items = []
    for it in extracted:
        raw_name = str(it.get("name") or "").strip()
        if not raw_name:
            continue
        qty = _to_float(it.get("qty"))
        unit = str(it.get("unit") or "").strip() or "dona"
        new_price = _to_float(it.get("price"))
        bc = str(it.get("barcode") or "").strip()

        # Barcode aniq mos kelsa — to'g'ridan-to'g'ri (ishonch 1.0)
        if bc and bc in bybc:
            cands = [_cand(bybc[bc], 1.0)]
        else:
            nrm = _norm_name(raw_name)
            scored = [(_match_score(nrm, rec["norm"]), rec) for rec in idx]
            scored = [x for x in scored if x[0] > 0.30]
            scored.sort(key=lambda x: x[0], reverse=True)
            cands = [_cand(rec, sc) for sc, rec in scored[:5]]

        best = cands[0] if (cands and cands[0]["confidence"] >= 0.55) else None
        out_items.append({
            "raw_name": raw_name, "qty": qty, "unit": unit,
            "new_price": new_price, "match": best, "candidates": cands,
        })

    matched = sum(1 for x in out_items if x["match"])
    logger.info(f"🧾 Prixod-scan: {len(out_items)} qator, {matched} mos keldi")
    return web.json_response({
        "ok": True, "count": len(out_items), "matched": matched,
        "unmatched": len(out_items) - matched, "items": out_items,
    })


# ─────────────────────────────────────────────────────────────────────────────
#  PRIXOD TARIXI — qabul qilingan hujjatlar jurnali (purchases)
# ─────────────────────────────────────────────────────────────────────────────

def _purch_db() -> sqlite3.Connection:
    """purchases jadvaliga ulanish (pos.db). web_sessions bilan bir xil naqsh."""
    from database._helpers import DB_PATH
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS purchases (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at    TEXT    NOT NULL,
        employee_name TEXT    DEFAULT '',
        note          TEXT    DEFAULT '',
        item_count    INTEGER DEFAULT 0,
        total_qty     REAL    DEFAULT 0,
        total_cost    REAL    DEFAULT 0,
        total_sell    REAL    DEFAULT 0,
        items_json    TEXT    DEFAULT '[]',
        source        TEXT    DEFAULT ''
    )""")
    # Migratsiya: eski jadvalga yetkazib beruvchi ustunlarini qo'shamiz
    cols = {r[1] for r in conn.execute("PRAGMA table_info(purchases)")}
    if "supplier_id" not in cols:
        conn.execute("ALTER TABLE purchases ADD COLUMN supplier_id INTEGER DEFAULT 0")
    if "supplier_name" not in cols:
        conn.execute("ALTER TABLE purchases ADD COLUMN supplier_name TEXT DEFAULT ''")
    conn.commit()
    return conn


@ROUTES.post("/api/prixod/save")
async def api_prixod_save(request: web.Request):
    """Prixod hujjatini saqlaydi: har tovar qoldig'iga qo'shadi + narxlarni
    yangilaydi, so'ng hujjatni `purchases` jurnaliga yozadi (tarix uchun)."""
    from database.channel_db import db as _db
    from database._helpers import sum_to_usd, now_local
    auth, err = await _admin_guard(request, _db, "products_qty")
    if err is not None:
        return err
    body = await _read_json(request)
    lines = body.get("lines") or []
    if not isinstance(lines, list) or not lines:
        return _err("Bo'sh hujjat — kamida bitta tovar kerak")
    note = (body.get("note") or "").strip()
    source = (body.get("source") or "").strip()
    # Prixod doim yetkazib beruvchidan bo'ladi — talab qilamiz, qabul qilingan
    # har bir tovarga shu yetkazib beruvchini biriktiramiz.
    supplier_id = int(body.get("supplier_id") or 0)
    if not supplier_id:
        return _err("Yetkazib beruvchini tanlang")
    supplier = await _db.get_supplier(supplier_id)
    if not supplier:
        return _err("Yetkazib beruvchi topilmadi", 404)
    supplier_name = supplier.get("name", "") or ""
    rate = _db.get_usd_rate()

    saved, errs = [], []
    for ln in lines:
        try:
            pid = int(ln.get("id") or 0)
            qty = _to_float(ln.get("qty"))
            if not pid or qty <= 0:
                continue
            p = await _db.get_product_any(pid)
            if not p:
                errs.append(f"#{pid} topilmadi")
                continue
            await _db.change_qty(pid, qty)
            cost = _to_float(ln.get("cost"))
            sell = _to_float(ln.get("sell"))
            whs = _to_float(ln.get("wholesale"))
            # Yetkazib beruvchini har qabul qilingan tovarga biriktiramiz
            upd = {"supplier_id": supplier_id}
            if cost > 0:
                upd["cost_price_usd"] = round(sum_to_usd(cost, rate), 4)
            if sell > 0:
                upd["sell_price_usd"] = round(sum_to_usd(sell, rate), 4)
            if whs > 0:
                upd["wholesale_price_usd"] = round(sum_to_usd(whs, rate), 4)
            if upd:
                await _db.update_product(pid, **upd)
            saved.append({
                "id": pid, "name": ln.get("name") or p.get("name", ""),
                "unit": ln.get("unit") or p.get("unit", "dona"),
                "qty": qty, "cost": cost, "sell": sell,
            })
        except Exception as e:
            errs.append(str(e))

    if not saved:
        return _err("Hech bir tovar saqlanmadi" + (f": {errs[0]}" if errs else ""))

    total_qty = sum(s["qty"] for s in saved)
    total_cost = sum(s["qty"] * s["cost"] for s in saved)
    total_sell = sum(s["qty"] * s["sell"] for s in saved)
    emp = (auth.get("user") or {}).get("full_name") or "Admin"
    created_at = now_local().strftime("%Y-%m-%d %H:%M:%S")

    conn = _purch_db()
    try:
        cur = conn.execute(
            "INSERT INTO purchases(created_at, employee_name, note, item_count, "
            "total_qty, total_cost, total_sell, items_json, source, "
            "supplier_id, supplier_name) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (created_at, emp, note, len(saved), total_qty, total_cost, total_sell,
             _json.dumps(saved, ensure_ascii=False), source,
             supplier_id, supplier_name)
        )
        conn.commit()
        new_id = cur.lastrowid
    finally:
        conn.close()

    logger.info(f"📦 Prixod #{new_id} saqlandi: {len(saved)} tovar, tannarx {total_cost}")
    return web.json_response({
        "ok": True, "id": new_id, "saved": len(saved), "errors": errs,
        "total_cost": total_cost, "total_sell": total_sell,
    })


@ROUTES.get("/api/prixod/list")
async def api_prixod_list(request: web.Request):
    """Oldingi prixod hujjatlari + yig'indi. Filtr: from/to (YYYY-MM-DD), limit."""
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth or auth["role"] != "admin":
        return _err("Avtorizatsiya talab qilinadi", 401)
    dfrom = (request.query.get("from") or "").strip()
    dto = (request.query.get("to") or "").strip()
    try:
        limit = max(1, min(500, int(request.query.get("limit") or 200)))
    except ValueError:
        limit = 200
    where, params = [], []
    if dfrom:
        where.append("created_at >= ?"); params.append(dfrom + " 00:00:00")
    if dto:
        where.append("created_at <= ?"); params.append(dto + " 23:59:59")
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    conn = _purch_db()
    try:
        rows = conn.execute(
            f"SELECT * FROM purchases{wsql} ORDER BY id DESC LIMIT ?",
            params + [limit]
        ).fetchall()
    finally:
        conn.close()

    items, sum_cost, sum_sell = [], 0.0, 0.0
    for r in rows:
        try:
            its = _json.loads(r["items_json"] or "[]")
        except Exception:
            its = []
        items.append({
            "id": r["id"], "created_at": r["created_at"],
            "employee_name": r["employee_name"], "note": r["note"],
            "item_count": r["item_count"], "total_qty": r["total_qty"],
            "total_cost": r["total_cost"], "total_sell": r["total_sell"], "items": its,
        })
        sum_cost += r["total_cost"] or 0
        sum_sell += r["total_sell"] or 0
    markup = round((sum_sell - sum_cost) / sum_cost * 100, 1) if sum_cost else 0
    return web.json_response({
        "ok": True, "count": len(items),
        "summary": {"count": len(items), "total_cost": sum_cost,
                    "total_sell": sum_sell, "markup": markup},
        "items": items,
    })


@ROUTES.post("/api/product/delete")
async def api_product_delete(request: web.Request):
    from database.channel_db import db as _db
    auth, err = await _admin_guard(request, _db, "products_del")
    if err is not None:
        return err
    body = await _read_json(request)
    pid = int(body.get("id") or 0)
    if not pid:
        return _err("ID yo'q")
    await _db.deactivate_product(pid)
    return web.json_response({"ok": True})


def _compress_image(raw: bytes, max_side: int = 1280, quality: int = 82):
    """Rasmni kichraytirib JPEG'ga siqadi — server diski to'lib ketmasligi uchun
    (telefon rasmlari 3-5 MB bo'lishi mumkin → ~100-200 KB ga tushadi).
    Qaytaradi: (bytes, ok). Pillow yo'q / HEIC plagini yo'q / xato bo'lsa —
    (asl_baytlar, False) qaytaradi (hech narsa buzilmaydi)."""
    try:
        import io
        from PIL import Image, ImageOps
        im = Image.open(io.BytesIO(raw))
        im = ImageOps.exif_transpose(im)          # telefon rasmi aylanib qolmasin
        if im.mode != "RGB":
            im = im.convert("RGB")
        if max(im.size) > max_side:
            im.thumbnail((max_side, max_side), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=quality, optimize=True)
        data = out.getvalue()
        if data:
            return data, True
    except Exception as e:
        logger.warning(f"📸 Siqib bo'lmadi (asl rasm qoldi): {e}")
    return raw, False


@ROUTES.post("/api/product/image")
async def api_product_image(request: web.Request):
    """Mahsulot rasmini yuklaydi (multipart). image_url ni saqlaydi."""
    from database.channel_db import db as _db
    # Multipart bo'lgani uchun initData header orqali keladi
    auth, err = await _admin_guard(request, _db, "products_edit")
    if err is not None:
        logger.warning(f"📸 products_edit ruxsati yo'q: {err}")
        # Yangi mahsulotga rasm qo'yishda products_add ham bo'lishi mumkin
        auth2, err2 = await _admin_guard(request, _db, "products_add")
        if err2 is not None:
            logger.warning(f"📸 products_add ruxsati yo'q: {err2}")
            return err2
        auth = auth2

    pid = 0
    file_bytes = None
    fname_in = "photo.jpg"
    try:
        post = await request.post()   # multipart'ni xotiraga o'qiydi (oddiy, ishonchli)
        pid = int(post.get("id") or 0)
        field = post.get("photo")
        if field is not None:
            # aiohttp FileField: .file (fayl-obyekt), .filename
            if hasattr(field, "file"):
                fname_in = (getattr(field, "filename", None) or "photo.jpg")
                file_bytes = field.file.read()
            elif isinstance(field, (bytes, bytearray)):
                file_bytes = bytes(field)
    except Exception as e:
        logger.error(f"📸 Faylni o'qib bo'lmadi: {e}")
        return _err(f"Faylni o'qib bo'lmadi: {e}", 400)

    if not file_bytes:
        logger.warning("📸 Rasm topilmadi (photo maydoni bo'sh)")
        return _err("Rasm topilmadi (photo maydoni bo'sh)")
    logger.info(f"📸 Rasm o'qildi: {fname_in} ({len(file_bytes)} bytes)")
    if len(file_bytes) > 25 * 1024 * 1024:
        logger.warning(f"📸 Rasm juda katta: {len(file_bytes)} bytes")
        return _err("Rasm 25MB dan katta")

    # Diskni tejash uchun siqamiz (8000+ tovar bemalol sig'sin). Siqilsa — .jpg.
    orig_len = len(file_bytes)
    file_bytes, _compressed = _compress_image(file_bytes)
    if _compressed:
        logger.info(f"📸 Siqildi: {orig_len} → {len(file_bytes)} bytes")

    # Kengaytmani aniqlaymiz (siqilgan bo'lsa — .jpg; aks holda asl turi)
    if _compressed:
        file_ext = ".jpg"
    else:
        fn = (fname_in or "photo.jpg").lower()
        file_ext = ".jpg"
        for e in (".png", ".jpeg", ".jpg", ".webp", ".gif", ".heic", ".heif"):
            if fn.endswith(e):
                file_ext = ".jpg" if e == ".jpeg" else e
                break

    stamp = int(time.time())
    fname = f"prod_{pid or 'new'}_{stamp}{file_ext}"
    fpath = os.path.join(UPLOAD_DIR, fname)
    try:
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        with open(fpath, "wb") as f:
            f.write(file_bytes)
        logger.info(f"📸 Rasm saqland'i: {fpath}")
    except Exception as e:
        logger.error(f"📸 Saqlanmadi: {e}")
        return _err(f"Saqlanmadi: {e}", 500)

    url = orig_url = f"/api/img/{fname}"

    # ── Brendlash: fonni kesib, och yashil fonga + logotip ────────────────
    # ASL rasm o'chirilmaydi — u `image_orig_url` bo'lib qoladi. Kesish
    # yiqilsa (rembg yo'q / model yuklanmadi) asl rasm asosiy bo'lib qolaveradi.
    brand_info = {"ok": False, "cut": False}
    try:
        from bot import imageproc
        # Tovarda teg/bo'lim ALLAQACHON bo'lsa — darrov to'g'ri sahna va
        # lenta bilan chizamiz (aks holda rasm avval "umumiy" fonda chiqib,
        # keyin qayta chizilardi).
        tags0, cat0 = [], ""
        if pid:
            p0 = await _db.get_product_any(pid)
            if p0:
                tags0 = [t.strip() for t in (p0.get("usage_tags") or "").split(",")
                         if t.strip()]
                if p0.get("category_id"):
                    c0 = await _db.get_category(int(p0["category_id"]))
                    cat0 = (c0 or {}).get("name", "")
        bname = f"prod_{pid or 'new'}_{stamp}_br.jpg"
        loop = asyncio.get_running_loop()
        # ALOHIDA jarayonda: sifatli model (u2net) 400+ MB oladi, lekin
        # jarayon tugashi bilan xotira qaytadi — bot 1 GB serverda yashaydi.
        brand_info = await loop.run_in_executor(
            None, lambda: imageproc.brand_file_isolated(
                fpath, os.path.join(UPLOAD_DIR, bname), tags0, cat0))
        if brand_info.get("ok"):
            url = f"/api/img/{bname}"
            if pid:
                _drop_old_branded(pid, bname)   # eski nusxa disk egallamasin
            logger.info(f"🖼 Brendlandi (fon kesildi: {brand_info.get('cut')}): {url}")
    except Exception as e:
        logger.warning(f"🖼 Brendlash o'tkazib yuborildi: {e}")

    if pid:
        try:
            await _db.update_product(pid, image_url=url,
                                     image_orig_url=orig_url, image_file_id="")
            logger.info(f"📸 Database'ga saqland'i: pid={pid}, url={url}")
        except Exception as e:
            logger.error(f"📸 Bazaga yozilmadi: {e}")
            return _err(f"Bazaga yozilmadi: {e}", 500)
        # Fon rejimida: bo'lim + "qayerda ishlatiladi" teglari aniqlanadi va
        # rasm o'sha teglar bilan qayta chizib chiqiladi. Rasm yuklash javobi
        # AI ni kutib turmasin (u 5-20 soniya olishi mumkin) — admin darrov
        # brendlangan rasmni ko'radi, teglar bir necha soniyada qo'shiladi.
        asyncio.ensure_future(_enrich_product(pid, fpath))
    else:
        logger.warning("📸 pid=0, rasm saqland'i lekin database'ga yozilmadi")
    logger.info(f"📸 Yuklash muvaffaqiyatli: {url}")
    return web.json_response({"ok": True, "image_url": url,
                              "image_orig_url": orig_url,
                              "branded": bool(brand_info.get("ok")),
                              "bg_removed": bool(brand_info.get("cut"))})


async def _auto_categorize(pid: int, image_path: str = "", force: bool = False):
    """Fon vazifasi: kategoriyasi yo'q tovarga rasm bo'yicha kategoriya qo'yadi.

    Jim ishlaydi — yiqilsa rasm yuklash baribir muvaffaqiyatli hisoblanadi.
    """
    from database.channel_db import db as _db
    from bot import ai_category
    try:
        p = await _db.get_product_any(pid)
        if not p:
            return
        if not force and int(p.get("category_id") or 0):
            return          # admin qo'lda qo'ygan — AI ustidan yozmaydi
        await ai_category.apply_to_product(
            _db, pid, p.get("name", "") or "", image_path)
    except Exception as e:
        logger.warning(f"🏷 Avto-kategoriya bo'lmadi (#{pid}): {e}")


async def _rebrand_with_tags(pid: int, tags: list) -> str:
    """Rasmni "qayerda ishlatiladi" lentasi bilan qayta chizadi.

    Har safar ASL rasmdan boshlanadi — qayta-qayta chizilsa ham sifat
    pasaymaydi va lenta ikki marta tushmaydi.
    """
    from database.channel_db import db as _db
    from bot import imageproc
    p = await _db.get_product_any(pid)
    if not p:
        return ""
    src = _img_path(p.get("image_orig_url") or p.get("image_url") or "")
    if not src:
        return ""
    cat_name = ""
    if p.get("category_id"):
        cat = await _db.get_category(int(p["category_id"]))
        cat_name = (cat or {}).get("name", "")
    bname = f"prod_{pid}_{int(time.time())}_br.jpg"
    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(
        None, lambda: imageproc.brand_file_isolated(
            src, os.path.join(UPLOAD_DIR, bname), tags, cat_name))
    if not info.get("ok"):
        return ""
    url = f"/api/img/{bname}"
    await _db.update_product(pid, image_url=url,
                             image_orig_url=p.get("image_orig_url") or "")
    _drop_old_branded(pid, bname)
    return url


_last_post: dict = {}          # pid → oxirgi kanal posti vaqti (takrorlanmasin)
_enriching: set = set()        # pid → hozir boyitilyapti (post kutib tursin)


async def _post_debounced(db, p: dict, pid: int, gap: int = 30):
    """Kanalga e'lon — yaqin oradagi takroriy chaqiruvni o'tkazib yuboradi.

    Tovarni saqlash ham, rasm yuklash ham e'lon qilishi mumkin; ikkalasi
    ketma-ket kelsa kanalda ikki marta post-o'chirish sodir bo'lardi.
    """
    # Rasm hozirgina yuklangan va boyitish (bo'lim → teglar → lenta+sahna →
    # qo'llanma) hali tugamagan bo'lsa — POST KUTADI. Aks holda admin
    # "Saqlash"ni bosishi bilan kanalga YARIM tayyor post ketardi: rasmda
    # "qayerda ishlatiladi" lentasi yo'q, kaptionda bo'lim hashtagi yo'q
    # (2026-08-06, #15681 — post 12:31:29 da ketdi, teglar 12:31:31 da
    # tayyor bo'ldi). Boyitish o'zi tugagach e'lon qiladi.
    if pid in _enriching:
        logger.info(f"📢 Post kutmoqda — boyitish tugamagan (#{pid})")
        return
    now = time.time()
    if now - _last_post.get(pid, 0) < gap:
        return
    _last_post[pid] = now
    await _post_product_to_channel(db, p, pid)


async def _enrich_product(pid: int, image_path: str = "", post: bool = True):
    """Rasm yuklangandan keyingi TO'LIQ oqim (fon rejimida):

        bo'lim aniqlash → «qayerda ishlatiladi» teglari → rasmni sahnaga
        qo'yib qayta chizish → qo'llanmani internetdan topish → KANALGA e'lon.

    Kanalga e'lon aynan shu yerda, ENG OXIRIDA yuboriladi: shunda postga
    tayyor rasm, to'g'ri bo'lim va teglar tushadi. (Ilgari post tovar
    saqlanganda ketardi — o'shanda rasm hali yuklanmagan bo'lardi va yangi
    tovar kanalga UMUMAN chiqmasdi.)

    Boyitish davomida `_enriching` da turadi — shu payt "Saqlash" bosilsa
    post yuborilmaydi, bu yerdagi e'lonni kutadi.

    Jim ishlaydi: biror bosqich yiqilsa qolgani baribir bajariladi.
    """
    from database.channel_db import db as _db
    from bot import ai_category
    _enriching.add(pid)
    try:
        await _enrich_steps(pid, image_path, _db, ai_category)
    finally:
        _enriching.discard(pid)

    if not post:
        return
    # ── Kanalga e'lon (tayyor rasm bilan) ─────────────────────────────────
    try:
        now = time.time()
        if now - _last_post.get(pid, 0) < 30:
            return                       # yaqinda yuborilgan — takrorlamaymiz
        p = await _db.get_product_any(pid)
        if not p or not (p.get("image_url") or ""):
            return
        _last_post[pid] = now
        await _post_product_to_channel(_db, p, pid)
    except Exception as e:
        logger.warning(f"📢 Kanalga yuborilmadi (#{pid}): {e}")


async def _enrich_steps(pid: int, image_path: str, _db, ai_category):
    """`_enrich_product` ning bosqichlari — har biri alohida himoyalangan."""
    try:
        await _auto_categorize(pid, image_path)
    except Exception as e:
        logger.warning(f"🏷 Avto-kategoriya bo'lmadi (#{pid}): {e}")

    async def _cat_name() -> str:
        p = await _db.get_product_any(pid)
        if p and p.get("category_id"):
            cat = await _db.get_category(int(p["category_id"]))
            return (cat or {}).get("name", "")
        return ""

    # AI birinchi urinishda "ishonchim komil emas" deyishi mumkin — o'shanda
    # bo'lim qo'yilmaydi va postda hashtag (#tozalash_vositalari) chiqmaydi.
    # Bir marta qayta so'raymiz: zanjir boshqa modelga tushishi mumkin va
    # amalda o'sha tovar ikkinchi urinishda aniqlanadi (#15681 shunday bo'ldi).
    try:
        if not await _cat_name():
            await _auto_categorize(pid, image_path)
    except Exception as e:
        logger.warning(f"🏷 Bo'lim qayta aniqlanmadi (#{pid}): {e}")

    tags = []
    try:
        p = await _db.get_product_any(pid)
        if not p:
            return
        tags = [t.strip() for t in (p.get("usage_tags") or "").split(",")
                if t.strip()]
        if not tags:
            loop = asyncio.get_running_loop()
            tags = await loop.run_in_executor(
                None, ai_category.usage_tags,
                p.get("name", "") or "", image_path, await _cat_name())
            if tags:
                await _db.update_product(pid, usage_tags=", ".join(tags))
                # Teglar YANGI aniqlandi — rasmni ular bilan qayta chizamiz.
                if await _rebrand_with_tags(pid, tags):
                    logger.info(f"🖼 Lenta+sahna (#{pid}): {', '.join(tags)}")
    except Exception as e:
        logger.warning(f"🏷 Teglar qo'shilmadi (#{pid}): {e}")

    # ── «Qanday ishlatiladi» qo'llanmasi (internetdan) ────────────────────
    # Bir marta topiladi va bazada qoladi; keyingi e'lonlarda qayta izlanmaydi.
    try:
        p = await _db.get_product_any(pid)
        if p and not (p.get("usage_info") or "").strip():
            from bot import product_info
            name = (p.get("display_name") or "").strip() or p.get("name", "")
            cat = await _cat_name()
            loop = asyncio.get_running_loop()
            # ASL rasm (foni kesilmagan) — unda qadoqdagi yozuv to'liq
            # ko'rinadi, AI tovarni aniqroq taniydi.
            img = image_path or _img_path(
                p.get("image_orig_url") or p.get("image_url") or "")
            txt = await loop.run_in_executor(
                None, lambda: product_info.usage_guide(name, cat, tags, img))
            if txt:
                await _db.update_product(pid, usage_info=txt)
    except Exception as e:
        logger.warning(f"📖 Qo'llanma topilmadi (#{pid}): {e}")


def _drop_old_branded(pid: int, keep: str):
    """Shu tovarning ESKI brendlangan nusxalarini o'chiradi (disk to'lmasin).

    Faqat `prod_<pid>_*_br.jpg` naqshiga tushadigan fayllar o'chadi — ASL
    rasmga hech qachon tegilmaydi, chunki brendlangani asldan istalgan vaqtda
    qayta yasaladi, asl esa qayta tiklanmaydi.
    """
    keep = os.path.basename(keep or "")
    try:
        for f in os.listdir(UPLOAD_DIR):
            if (f.startswith(f"prod_{pid}_") and f.endswith("_br.jpg")
                    and f != keep):
                try:
                    os.remove(os.path.join(UPLOAD_DIR, f))
                except OSError:
                    pass
    except OSError:
        pass


def _img_path(url: str) -> str:
    """'/api/img/xxx.jpg' → diskdagi to'liq yo'l ('' — noto'g'ri/tashqi manzil)."""
    name = os.path.basename((url or "").split("?")[0])
    if not name:
        return ""
    p = os.path.realpath(os.path.join(UPLOAD_DIR, name))
    return p if p.startswith(UPLOAD_DIR) and os.path.isfile(p) else ""


@ROUTES.post("/api/product/categorize")
async def api_product_categorize(request: web.Request):
    """Bitta tovar bo'limini AI bilan (rasmga qarab) qayta aniqlaydi.

    Admin Mini App'dan "🏷 Bo'limni aniqla" tugmasini bosganda ishlaydi —
    noto'g'ri joyga tushgan tovarni bitta bosishda tuzatish uchun.
    """
    from database.channel_db import db as _db
    from bot import ai_category
    auth, err = await _admin_guard(request, _db, "products_edit")
    if err is not None:
        return err
    body = await _read_json(request)
    pid = int(body.get("id") or 0)
    if not pid:
        return _err("ID yo'q")
    p = await _db.get_product_any(pid)
    if not p:
        return _err("Mahsulot topilmadi", 404)
    # Kesilmagan ASL rasm AI uchun ma'lumotliroq (fon ham kontekst beradi).
    img = _img_path(p.get("image_orig_url") or p.get("image_url") or "")
    cat = await ai_category.apply_to_product(
        _db, pid, p.get("name", "") or "", img)
    if not cat:
        return web.json_response({"ok": True, "category": "",
                                  "msg": "AI aniq javob bermadi"})
    # `image_sent` — rasm AI ga BERILDI degani. Rasm modellari yiqilgan bo'lsa
    # ai_engine o'zi nomdan javob berishga o'tadi, buni bu yerdan bilib
    # bo'lmaydi — shuning uchun "rasmga qarab aniqladi" deb va'da bermaymiz.
    return web.json_response({"ok": True, "category": cat,
                              "image_sent": bool(img)})


@ROUTES.post("/api/product/rebrand")
async def api_product_rebrand(request: web.Request):
    """Mavjud rasmni qayta brendlaydi (fon kesish + och yashil fon + logotip).

    Asl rasm saqlanib qoladi; qayta-qayta brendlansa ham sifat pasaymaydi,
    chunki har safar ASLIDAN boshlanadi.
    """
    from database.channel_db import db as _db
    from bot import imageproc
    auth, err = await _admin_guard(request, _db, "products_edit")
    if err is not None:
        return err
    body = await _read_json(request)
    pid = int(body.get("id") or 0)
    if not pid:
        return _err("ID yo'q")
    p = await _db.get_product_any(pid)
    if not p:
        return _err("Mahsulot topilmadi", 404)

    src_url = p.get("image_orig_url") or p.get("image_url") or ""
    src = _img_path(src_url)
    if not src:
        return _err("Rasm yo'q")

    # Saqlangan "qayerda ishlatiladi" teglari va bo'lim (sahna uchun).
    tags = [t.strip() for t in (p.get("usage_tags") or "").split(",") if t.strip()]
    cat_name = ""
    if p.get("category_id"):
        cat = await _db.get_category(int(p["category_id"]))
        cat_name = (cat or {}).get("name", "")
    bname = f"prod_{pid}_{int(time.time())}_br.jpg"
    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(
        None, lambda: imageproc.brand_file_isolated(
            src, os.path.join(UPLOAD_DIR, bname), tags, cat_name))
    if not info.get("ok"):
        return _err("Brendlash bo'lmadi: " + (info.get("error") or "noma'lum"))
    url = f"/api/img/{bname}"
    await _db.update_product(pid, image_url=url, image_orig_url=src_url)
    _drop_old_branded(pid, bname)
    return web.json_response({"ok": True, "image_url": url,
                              "image_orig_url": src_url,
                              "bg_removed": bool(info.get("cut"))})


@ROUTES.get(r"/api/img/{name}")
async def api_img(request: web.Request):
    """Yuklangan rasmni qaytaradi (CORS bilan). Path traversal tekshiruvi bilan."""
    name = request.match_info.get("name", "")
    if not name:
        return _err("Fayl nomi kerak", 400)
    
    # Yo'l traversal himoyasi — realpath orqali tekshirish
    fpath = os.path.realpath(os.path.join(UPLOAD_DIR, name))
    
    # Haqiqiy yo'l UPLOAD_DIR ichida ekanligini tekshirish
    if not fpath.startswith(UPLOAD_DIR):
        logger.warning(f"Path traversal attempt: {name} -> {fpath}")
        return _err("Noto'g'ri nom", 400)
    
    if not os.path.isfile(fpath):
        return _err("Topilmadi", 404)
    
    ctype = mimetypes.guess_type(fpath)[0] or "application/octet-stream"
    try:
        with open(fpath, "rb") as f:
            data = f.read()
        return web.Response(body=data, content_type=ctype,
                            headers={"Cache-Control": "public, max-age=86400"})
    except OSError as e:
        logger.error(f"Failed to read image {fpath}: {e}")
        return _err("Faylni o'qib bo'lmadi", 500)


# ─── Statistika (admin) ─────────────────────────────────────────────────────

@ROUTES.get("/api/stats")
async def api_stats(request: web.Request):
    from database.channel_db import db as _db
    from database._helpers import now_local
    from bot.permissions import has_permission
    auth = await _authenticate(request, _db)
    if not auth:
        return _err("Avtorizatsiya talab qilinadi", 401)
    if auth["role"] != "admin":
        return _err("Faqat adminlar uchun", 403)
    # 'stats' ruxsati (bosh admin har doim ega)
    if not await has_permission(_db, auth["tg_id"], "stats"):
        return _err("Statistika ruxsati yo'q", 403)

    today = now_local().strftime("%Y-%m-%d")
    month = now_local().strftime("%Y-%m")
    st_today = await _db.stats_day(today)
    st_month = await _db.stats_month(month)
    top = await _db.top_products(month, limit=7)
    rate = _db.get_usd_rate()

    def block(s):
        return {
            "sale_count": s.get("sale_count", 0),
            "order_count": s.get("order_count", 0),
            "revenue_sum": float(s.get("revenue", 0) or 0),
            "revenue_usd": float(s.get("revenue_usd", 0) or 0),
            "profit_sum": float(s.get("profit", 0) or 0),
            "profit_usd": float(s.get("profit_usd", 0) or 0),
            "cost_sum": float(s.get("cost", 0) or 0),
            # To'lov turlari bo'yicha (netto — qaytarishlar ayirilgan)
            "cash_sum": float(s.get("cash_total", 0) or 0),
            "card_sum": float(s.get("card_total", 0) or 0),
            "click_sum": float(s.get("other_total", 0) or 0),
            "nasiya_sum": float(s.get("nasiya_total", 0) or 0),
            # Qaytarishlar (musbat ko'rsatiladi)
            "return_count": s.get("return_count", 0),
            "return_sum": float(s.get("return_total", 0) or 0),
            # «Chinor» ichki rasxod (tannarx bo'yicha, foydaga kirmaydi)
            "expense_sum": float(s.get("expense", 0) or 0),
            "expense_usd": float(s.get("expense_usd", 0) or 0),
            "expense_count": s.get("expense_count", 0),
        }

    top_list = [{
        "name": t.get("name", ""),
        "qty": float(t.get("qty", 0) or 0),
        "revenue_sum": float(t.get("revenue", 0) or 0),
    } for t in top]

    payload = {
        "ok": True,
        "rate": rate,
        "today": block(st_today),
        "month": block(st_month),
        "month_label": now_local().strftime("%Y-%m"),
        "top_products": top_list,
        "source": "chinor",
    }

    # HAQIQIY SAVDO — YesPOS cheklaridan.
    #
    # Savdo YesPOS kassasida bo'ladi, Chinor `sales` jadvalida bir nechta
    # yozuv bor xolos. Shuning uchun aylanma/foyda cheklardan olinadi va
    # yuqoridagi bloklar ustidan yoziladi. Javob SHAKLI o'zgarmaydi —
    # Mini App ham, chinor-admin ilovasi ham qayta qurilmasdan to'g'ri
    # raqamni ko'radi.
    #
    # Cheklar hali yig'ilmagan bo'lsa (yoki xato bo'lsa) eski qiymat qoladi:
    # bo'sh ekran ko'rsatgandan ko'ra bor ma'lumot yaxshiroq.
    try:
        from bot.api_receipts import stats_blocks
        real = stats_blocks(today, month)
        if real:
            for key in ("today", "month"):
                payload[key].update(real[key])
            payload["top_products"] = real["top_products"] or top_list
            payload["source"] = "yespos"
            payload["profit_coverage"] = real["profit_coverage"]
    except Exception as e:
        logger.warning(f"[api stats] cheklardan o'qilmadi: {e}")

    return web.json_response(payload)


# ─── Kassa naqd harakatlari (rasxod/kirim) — admin ─────────────────────────

@ROUTES.get("/api/cash-movements")
async def api_cash_movements(request: web.Request):
    """Naqd kirim/chiqim jurnali (kassalardan sinxron bo'lgan + botdan yozilgan).
    Filtr: from/to (YYYY-MM-DD), limit. Yig'indi bilan qaytaradi."""
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth or auth["role"] != "admin":
        return _err("Avtorizatsiya talab qilinadi", 401)
    dfrom = (request.query.get("from") or "").strip()
    dto = (request.query.get("to") or "").strip()
    try:
        limit = max(1, min(500, int(request.query.get("limit") or 200)))
    except (TypeError, ValueError):
        limit = 200
    rows = await _db.get_cash_movements(dfrom, dto, limit)
    items, in_total, out_total = [], 0.0, 0.0
    for r in rows:
        amount = float(r.get("amount", 0) or 0)
        direction = r.get("direction", "in")
        if direction == "out":
            out_total += amount
        else:
            in_total += amount
        items.append({
            "id": r.get("id"),
            "direction": direction,
            "amount": amount,
            "category": r.get("category", "") or "",
            "note": r.get("note", "") or "",
            "recipient": r.get("recipient", "") or "",
            "cashier_name": r.get("cashier_name", "") or "",
            "source": r.get("source", "") or "",
            "created_at": r.get("created_at", "") or "",
        })
    return web.json_response({
        "ok": True, "items": items,
        "summary": {"count": len(items), "in_total": in_total,
                    "out_total": out_total, "net": in_total - out_total},
    })


# ─── Mijoz hisobim ──────────────────────────────────────────────────────────

@ROUTES.get("/api/my-account")
async def api_my_account(request: web.Request):
    from database.channel_db import db as _db
    from database._helpers import now_local
    auth = await _authenticate(request, _db)
    if not auth:
        return _err("Avtorizatsiya talab qilinadi", 401)
    if auth["role"] != "client":
        return _err("Faqat mijozlar uchun", 403)
    c = auth["user"]
    cid = c["id"]
    month = now_local().strftime("%Y-%m")
    rep = await _db.client_monthly_report(cid, month)
    try:
        orders = await _db.get_client_orders(cid)
    except Exception:
        orders = []
    # Eng yangisi birinchi. Tarkibi ham beriladi — Mini App'da "Yana
    # buyurtma" tugmasi xuddi shu tovarlarni bir bosishda savatga soladi.
    orders = sorted(orders or [], key=lambda o: o.get("id", 0), reverse=True)
    order_list = [{
        "id": o["id"],
        "total": float(o.get("total", 0) or 0),
        "status": o.get("status", ""),
        "created_at": (o.get("created_at", "") or "")[:16],
        "items": [{"product_id": it.get("product_id"),
                   "name": it.get("name", ""),
                   "qty": float(it.get("qty", 0) or 0)}
                  for it in (o.get("items") or [])],
    } for o in orders[:15]]

    return web.json_response({
        "ok": True,
        "name": c.get("shop_name", ""),
        "phone": c.get("phone", ""),
        "client_type": (c.get("client_type") or "dona").lower(),
        "debt_sum": float(c.get("debt", 0) or 0),
        "debt_usd": float(c.get("debt_usd", 0) or 0),
        "month_ordered": float(rep.get("total_ordered", 0) or 0),
        "month_paid": float(rep.get("total_paid", 0) or 0),
        "orders": order_list,
        "rate": _db.get_usd_rate(),
    })


# ─── Mijoz Mini App'dan buyurtma beradi ────────────────────────────────────

_PAY_LABELS = {"cash": "💵 Naqd", "card": "💳 Karta", "click": "📲 Click/Payme"}


@ROUTES.post("/api/client/order")
async def api_client_order(request: web.Request):
    """Mini App savatidan buyurtma. Botdagi '🛒 Buyurtma berish' bilan BIR
    XIL oqim (`create_order`): ombor kamayadi, mijoz hisobiga yoziladi,
    adminlarga holat tugmalari bilan xabar boradi.

    Narx va qoldiq FAQAT serverda olinadi — brauzerdan kelgan narxga
    ishonmaymiz (aks holda mijoz narxni o'zi yozib yuborishi mumkin)."""
    from database.channel_db import db as _db
    from bot.keyboards import order_status_kb
    auth = await _authenticate(request, _db)
    if not auth:
        return _err("Avtorizatsiya talab qilinadi", 401)
    if auth["role"] != "client":
        return _err("Faqat mijozlar buyurtma bera oladi", 403)
    if not _db.is_client_orders_enabled():
        return _err("Buyurtma qabul qilish hozircha o'chirilgan. "
                    "Iltimos, do'konga qo'ng'iroq qiling.", 403)
    body = await _read_json(request)
    c = auth["user"]
    ctype = (c.get("client_type") or "dona").lower()

    raw_items = body.get("items") or []
    if not isinstance(raw_items, list) or not raw_items:
        return _err("Savat bo'sh")
    merged: dict = {}
    for it in raw_items[:100]:
        try:
            pid = int(it.get("product_id") or 0)
            qty = float(it.get("qty") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if pid > 0 and qty > 0:
            merged[pid] = merged.get(pid, 0) + qty
    if not merged:
        return _err("Savat bo'sh")

    items, problems = [], []
    for pid, qty in merged.items():
        p = await _db.get_product(pid)
        if not p or not p.get("is_active", 1):
            problems.append({"product_id": pid, "error": "Tovar topilmadi"})
            continue
        name = (p.get("display_name") or "").strip() or p.get("name", "")
        stock = float(p.get("qty", 0) or 0)
        if qty > stock:
            problems.append({"product_id": pid, "name": name, "available": stock,
                             "error": f"{name}: faqat {stock:g} ta qoldi"})
            continue
        _, price = _price_pair(p, ctype)
        if price <= 0:
            problems.append({"product_id": pid, "name": name,
                             "error": f"{name}: narxi belgilanmagan"})
            continue
        items.append({"product_id": pid, "qty": qty, "price": price})
    if problems:
        # Savatni o'zgartirmaymiz — mijoz nima o'zgarganini ko'rib, o'zi
        # tuzatsin (jimgina kamaytirib yuborish ishonchni yo'qotadi).
        return web.json_response({"ok": False, "error": problems[0]["error"],
                                  "problems": problems}, status=409)

    delivery = "pickup" if body.get("delivery") == "pickup" else "delivery"
    pay = body.get("payment") if body.get("payment") in _PAY_LABELS else "cash"
    phone = str(body.get("phone") or c.get("phone") or "").strip()[:32]
    address = str(body.get("address") or "").strip()[:300]
    comment = str(body.get("comment") or "").strip()[:300]
    if delivery == "delivery" and not address:
        return _err("Yetkazish manzilini yozing")
    if not phone:
        return _err("Telefon raqamingizni yozing")
    note_lines = [
        "🚚 Yetkazib berish" if delivery == "delivery" else "🏪 Do'kondan olib ketish",
        f"📍 {address}" if address else "",
        f"📱 {phone}",
        f"To'lov: {_PAY_LABELS[pay]}",
        f"💬 {comment}" if comment else "",
        "(Mini App)",
    ]
    note = "\n".join(l for l in note_lines if l)

    oid, total = await _db.create_order(c["id"], items, note=note)
    if not oid:
        return _err("Buyurtma yaratilmadi", 500)

    # Adminlarga — botdagi buyurtma bilan bir xil xabar va holat tugmalari
    bot = getattr(_db, "_bot", None)
    if bot:
        o = await _db.get_order(oid) or {}
        lines = "".join(f"• {it['name']}: {float(it['qty']):g} × "
                        f"{float(it['price']):,.0f} so'm\n"
                        for it in (o.get("items") or []))
        notif = (f"🔔 <b>Yangi buyurtma #{oid}</b> (Mini App)\n\n"
                 f"👤 {_html_esc(c.get('shop_name', ''))}\n"
                 f"{_html_esc(note)}\n\n{_html_esc(lines)}\n"
                 f"💰 <b>Jami: {total:,.0f} so'm</b>")
        kb = order_status_kb(oid, "accepted")
        targets = {GLAVNIY_ADMIN_ID}
        try:
            targets |= {a["telegram_id"] for a in await _db.get_all_admins()}
        except Exception:
            pass
        for tid in targets:
            try:
                await bot.send_message(tid, notif, reply_markup=kb, parse_mode="HTML")
            except Exception:
                pass
        # Mijozning o'ziga ham chatda tasdiq — Mini App yopilsa ham iz qoladi
        try:
            await bot.send_message(
                c["telegram_id"],
                f"✅ <b>Buyurtmangiz #{oid} qabul qilindi!</b>\n\n"
                f"{_html_esc(lines)}\n💰 Jami: <b>{total:,.0f} so'm</b>\n\n"
                f"Tez orada siz bilan bog'lanamiz. Savollar: {CONTACT_PHONE_1}",
                parse_mode="HTML")
        except Exception:
            pass

    return web.json_response({"ok": True, "order_id": oid, "total": total,
                              "contact_phone": CONTACT_PHONE_1})


def _html_esc(s) -> str:
    import html as _html
    return _html.escape(str(s or ""), quote=False)


# ─── Login ─────────────────────────────────────────────────────────────────

@ROUTES.post("/api/login")
async def login(request: web.Request):
    # DB ni har chaqiriqda olamiz (singletondan)
    from database.channel_db import db as _db

    try:
        data = await request.json()
    except Exception:
        return web.json_response(
            {"ok": False, "error": "Noto'g'ri JSON"}, status=400
        )

    login_in    = (data.get("login") or "").strip()
    password_in = (data.get("password") or "").strip()  # bo'sh joylarni olib tashlash
    init_data   = data.get("initData") or ""

    # 1) Maydonlarni tekshirish
    if not login_in or not password_in:
        return web.json_response(
            {"ok": False, "error": "Login yoki parol bo'sh"}, status=400
        )
    if len(login_in) > 64 or len(password_in) > 128:
        return web.json_response(
            {"ok": False, "error": "Maydon juda uzun"}, status=400
        )

    # 2) Telegram WebApp initData HMAC tekshiruvi
    if init_data == "LOCAL_TEST":
        # Local test rejimi
        tg_id = 6787907623  # GLAVNIY_ADMIN_ID
        tg_user = {"id": tg_id, "first_name": "LocalTest"}
    else:
        parsed = verify_telegram_init_data(init_data, BOT_TOKEN)
        if not parsed:
            return web.json_response({
                "ok": False,
                "error": "Telegram WebApp imzosi noto'g'ri. Iltimos, Mini App'ni "
                         "Telegram bot ichidan oching.",
            }, status=401)
        tg_user = parsed.get("user") or {}
        try:
            tg_id = int(tg_user.get("id") or 0)
        except (TypeError, ValueError):
            tg_id = 0
        if not tg_id:
            return web.json_response({
                "ok": False, "error": "Telegram foydalanuvchi aniqlanmadi"
            }, status=401)

    # 3) Rate limit
    locked, remaining = attempt_tracker.is_locked(tg_id)
    if locked:
        mins = max(1, remaining // 60)
        return web.json_response({
            "ok": False,
            "error": (f"Juda ko'p noto'g'ri urinish. "
                       f"{mins} daqiqadan keyin qayta urinib ko'ring."),
            "locked": True,
            "lock_remaining_seconds": remaining,
        }, status=429)

    # 4) Asosiy tekshiruv
    result = await verify_login(_db, login_in, password_in)
    if not result:
        count, just_locked = attempt_tracker.record_failure(tg_id)
        body = {
            "ok": False,
            "error": "Login yoki parol noto'g'ri",
            "remaining_attempts": max(
                0, LoginAttemptTracker.LOCK_THRESHOLD - count
            ),
            "just_locked": just_locked,
        }
        return web.json_response(body, status=401)

    # 5) Strict TG ID binding
    user = result["user"]
    role = result["role"]
    bound_tg = user.get("telegram_id") or 0
    if bound_tg and int(bound_tg) != tg_id:
        attempt_tracker.record_failure(tg_id)
        return web.json_response({
            "ok": False,
            "error": ("Bu hisob boshqa Telegram akkauntiga biriktirilgan. "
                       "Agar bu sizning hisobingiz bo'lsa, admin bilan bog'laning."),
        }, status=403)

    # Birinchi muvaffaqiyatli mijoz login — TG ID ni biriktirib qo'yamiz
    if role == "client" and not bound_tg:
        try:
            await _db.set_client_tg_id(user["id"], tg_id)
        except Exception as e:
            print(f"[api login] set_client_tg_id xato: {e}")

    # 6) Muvaffaqiyat
    attempt_tracker.clear(tg_id)
    display_name = (
        user.get("full_name") if role == "admin"
        else user.get("shop_name")
    ) or user.get("username") or login_in

    auth_info = {
        "role": role,
        "tg_id": tg_id,
        "user": user,
        "is_glavniy": (tg_id == GLAVNIY_ADMIN_ID),
    }
    session_token = _create_session(auth_info)

    return web.json_response({
        "ok": True,
        "role": role,
        "session_token": session_token,
        "user": {
            "id": int(user.get("telegram_id") or user.get("id") or 0),
            "name": display_name,
            "username": user.get("username", ""),
        }
    })


@ROUTES.post("/api/auth/telegram")
async def auth_telegram(request: web.Request):
    """Parolsiz kirish — faqat Telegram `initData` imzosi bo'yicha.

    Mini App Telegram ichida ochiladi, ya'ni Telegram uni bot tokeni bilan
    imzolangan `initData` bilan ta'minlaydi. Bu imzo login+paroldan kuchliroq:
    uni foydalanuvchi soxtalashtira olmaydi va unutib ham qo'ymaydi. Shuning
    uchun Mini App'da login ekrani ortiqcha edi.

    `/api/login` TEGILMAYDI: kassa va admin ish stoli ilovalari
    `/api/desktop/login` orqali login+parol bilan kiradi — ularda Telegram
    initData yo'q, boshqa yo'li ham yo'q.
    """
    from database.channel_db import db as _db

    auth = await _authenticate(request, _db)
    if not auth:
        # Imzo yaroqsiz, YOKI bu Telegram foydalanuvchisi tizimda yo'q.
        # Ikkinchisi normal holat: odam avval botga /start berib raqamini
        # tasdiqlashi kerak, shunda mijoz sifatida yaratiladi.
        return web.json_response({
            "ok": False,
            "error": ("Sizni tanimadim. Avval botga qayting, /start bosing va "
                       "«📱 Raqamni yuborish» tugmasi orqali raqamingizni "
                       "tasdiqlang."),
            "need_start": True,
        }, status=401)

    user = auth["user"] or {}
    role = auth["role"]
    display_name = (
        user.get("full_name") if role == "admin" else user.get("shop_name")
    ) or user.get("username") or "Foydalanuvchi"

    return web.json_response({
        "ok": True,
        "role": role,
        "session_token": _create_session(auth),
        "user": {
            "id": int(user.get("telegram_id") or user.get("id") or 0),
            "name": display_name,
            "username": user.get("username", ""),
        },
    })


@ROUTES.get("/api/credentials")
async def credentials_status(request: web.Request):
    """Kassa/admin ilovalari uchun login o'rnatilganmi — holat."""
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth:
        return _err("Avtorizatsiya talab qilinadi", 401)

    user = auth["user"] or {}
    if auth["role"] == "admin":
        has = await _db.admin_has_credentials(auth["tg_id"])
    else:
        has = await _db.client_has_credentials(int(user.get("id") or 0))
    return web.json_response({
        "ok": True,
        "has_credentials": bool(has),
        "username": user.get("username", "") or "",
    })


@ROUTES.post("/api/credentials")
async def credentials_save(request: web.Request):
    """Kassa va admin ish stoli ilovalari uchun login/parol o'rnatish.

    Nega hali ham kerak: Mini App Telegram imzosi bilan kiradi, lekin kassa
    va admin ilovalari Telegramdan tashqarida ishlaydi — ularda initData
    yo'q, shuning uchun `/api/desktop/login` uchun login+parol zarur.
    Ilgari buni bot menyusidagi «🔑 Login/parol» tugmasi qilardi; endi
    boshqaruv Mini App ichida.
    """
    from database.channel_db import db as _db
    from bot.auth import validate_username, validate_password, hash_password

    auth = await _authenticate(request, _db)
    if not auth:
        return _err("Avtorizatsiya talab qilinadi", 401)

    body = await _read_json(request)
    username = (body.get("username") or "").strip()
    password = (body.get("password") or "").strip()

    ok_u, why_u = validate_username(username)
    if not ok_u:
        return _err(why_u)
    ok_p, why_p = validate_password(password)
    if not ok_p:
        return _err(why_p)

    user = auth["user"] or {}
    pw_hash = hash_password(password)
    if auth["role"] == "admin":
        saved = await _db.set_admin_credentials(auth["tg_id"], username, pw_hash)
    else:
        saved = await _db.set_client_credentials(
            int(user.get("id") or 0), username, pw_hash)

    if not saved:
        return _err("Bu login band — boshqasini tanlang", 409)
    return web.json_response({"ok": True, "username": username})


# ─── Bot menyusidan Mini App'ga ko'chirilgan funksiyalar ────────────────────
# Quyidagilar ilgari faqat botda edi. Endi Mini App ham chaqira oladi,
# shu sababli bot menyusidan olib tashlandi (bir funksiya ikki joyda
# turishi chalg'itardi va ikkalasini birga qo'llab-quvvatlash kerak bo'lardi).

async def _need_admin(request, _db, perm: str = ""):
    """(auth, xato_javobi) — admin ekanini va ruxsatini tekshiradi."""
    from bot.permissions import has_permission
    auth = await _authenticate(request, _db)
    if not auth:
        return None, _err("Avtorizatsiya talab qilinadi", 401)
    if auth["role"] != "admin":
        return None, _err("Faqat adminlar uchun", 403)
    if perm and not await has_permission(_db, auth["tg_id"], perm):
        return None, _err("Ruxsat yo'q", 403)
    return auth, None


@ROUTES.get("/api/categories")
async def api_categories_list(request: web.Request):
    """Kategoriyalar ro'yxati. Har qanday tizimga kirgan foydalanuvchiga
    ochiq — kategoriya nomi maxfiy ma'lumot emas, mijoz katalogni shu
    bo'yicha saralaydi. Tahrirlash esa (save/delete) admin huquqini talab
    qiladi.

    Mijozga faqat QOLDIG'I BOR tovarlar sanaladi va bo'sh kategoriya
    umuman ko'rsatilmaydi — aks holda bosib kirgan mijoz bo'sh ro'yxat
    ko'rardi.
    """
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth:
        return _err("Avtorizatsiya talab qilinadi", 401)

    is_client = auth["role"] == "client"
    rows = await _db.get_all_categories()
    prods = await _db.get_all_products(active_only=True)
    counts = {}
    for p in prods:
        if is_client and (p.get("qty", 0) or 0) <= 0:
            continue
        cid = int(p.get("category_id") or 0)
        if cid:
            counts[cid] = counts.get(cid, 0) + 1

    out = []
    for r in rows:
        d = dict(r)
        d["count"] = counts.get(int(d["id"]), 0)
        if is_client and d["count"] == 0:
            continue
        out.append(d)
    out.sort(key=lambda x: -x["count"])
    return web.json_response({"ok": True, "categories": out})


@ROUTES.post("/api/category/save")
async def api_category_save(request: web.Request):
    from database.channel_db import db as _db
    auth, err = await _need_admin(request, _db, "categories")
    if err is not None:
        return err
    body = await _read_json(request)
    name = (body.get("name") or "").strip()
    cid = int(body.get("id") or 0)
    if not name:
        return _err("Nom bo'sh")
    if len(name) > 64:
        return _err("Nom juda uzun (64 belgigacha)")
    if cid:
        await _db.update_category(cid, name)
        return web.json_response({"ok": True, "id": cid})
    if await _db.category_exists(name):
        return _err("Bunday kategoriya allaqachon bor", 409)
    new_id = await _db.add_category(name)
    return web.json_response({"ok": True, "id": new_id})


@ROUTES.post("/api/category/delete")
async def api_category_delete(request: web.Request):
    from database.channel_db import db as _db
    auth, err = await _need_admin(request, _db, "categories")
    if err is not None:
        return err
    cid = int((await _read_json(request)).get("id") or 0)
    if not cid:
        return _err("ID yo'q")
    await _db.delete_category(cid)
    return web.json_response({"ok": True})


@ROUTES.get("/api/low-stock")
async def api_low_stock(request: web.Request):
    """Qoldig'i ostonadan past tovarlar."""
    from database.channel_db import db as _db
    auth, err = await _need_admin(request, _db, "low_stock")
    if err is not None:
        return err
    try:
        threshold = float(request.query.get("threshold", "") or 0) or None
    except ValueError:
        threshold = None
    rows = await _db.get_low_stock(threshold)
    out = [{
        "id": r["id"], "name": r["name"],
        "qty": float(r["qty"] or 0), "unit": r.get("unit", "dona"),
        "sell_price": float(r["sell_price"] or 0),
    } for r in rows]
    return web.json_response({"ok": True, "count": len(out), "products": out})


@ROUTES.get("/api/export")
async def api_export(request: web.Request):
    """Excel hisobot: sales | clients | debtors. Faylni to'g'ridan qaytaradi."""
    import os as _os
    from database.channel_db import db as _db
    from bot import exporter

    auth, err = await _need_admin(request, _db, "export")
    if err is not None:
        return err

    kind = (request.query.get("type") or "sales").strip().lower()
    month = (request.query.get("month") or "").strip() or None
    try:
        if kind == "clients":
            path = await exporter.export_clients()
        elif kind == "debtors":
            path = await exporter.export_debtors()
        else:
            path = await exporter.export_sales(month)
    except Exception as e:
        logger.warning(f"[api export] xato: {e}")
        return _err("Hisobot tayyorlanmadi")

    if not path or not _os.path.exists(path):
        return _err("Fayl topilmadi")
    return web.FileResponse(path, headers={
        "Content-Disposition":
            f'attachment; filename="{_os.path.basename(path)}"',
    })


@ROUTES.post("/api/ai/analyze")
async def api_ai_analyze(request: web.Request):
    """AI biznes-analitika (admin). Savol + do'kon konteksti → tahlil.

    DIQQAT: bu yerda tashqi AI ga BIZNES KONTEKSTI yuboriladi (savdo,
    foyda, top tovarlar). Mijoz suhbatidan farqli — u yerda faqat tovar
    nomi va sotuv narxi ketadi. Bu ataylab shunday: admin aynan shu
    ma'lumot ustida tahlil so'raydi.
    """
    from database.channel_db import db as _db
    from bot.gemini_analyzer import analyze, QUICK_QUESTIONS, is_available

    auth, err = await _need_admin(request, _db, "ai_analytics")
    if err is not None:
        return err

    ok_ai, why = is_available()
    if not ok_ai:
        return _err(why, 503)

    body = await _read_json(request)
    preset = (body.get("preset") or "").strip()
    question = (body.get("question") or "").strip()
    if preset and preset in QUICK_QUESTIONS:
        question = QUICK_QUESTIONS[preset]
    if not question:
        return _err("Savol bo'sh")
    if len(question) > 500:
        return _err("Savol juda uzun (500 belgigacha)")

    ctx = await _db.gather_ai_context()
    answer = await analyze(question, ctx)
    return web.json_response({"ok": True, "answer": answer,
                              "presets": list(QUICK_QUESTIONS.keys())})


# ─── POS Sotuv (admin) ──────────────────────────────────────────────────────

@ROUTES.post("/api/sale")
async def api_sale(request: web.Request):
    from database.channel_db import db as _db
    from database._helpers import sum_to_usd
    from bot.permissions import has_permission
    auth = await _authenticate(request, _db)
    if not auth:
        return _err("Avtorizatsiya talab qilinadi", 401)
    if auth["role"] != "admin":
        return _err("Faqat adminlar uchun", 403)
    if not await has_permission(_db, auth["tg_id"], "sale"):
        return _err("Kassada sotuv ruxsati yo'q", 403)

    body = await _read_json(request)
    items_in = body.get("items") or []
    payment = (body.get("payment") or "cash").lower()
    if payment not in ("cash", "card", "click", "split"):
        payment = "cash"
    # Aralash (split) to'lov qismlari — kassa sync bilan bir xil maydonlar
    def _num(v):
        try:
            return max(0.0, float(v or 0))
        except (TypeError, ValueError):
            return 0.0
    split_cash = _num(body.get("paid_cash"))
    split_card = _num(body.get("paid_card"))
    split_other = _num(body.get("paid_other"))
    split_debt = _num(body.get("debt_sum"))
    is_split = (payment == "split") or \
        (split_cash + split_card + split_other + split_debt) > 0
    try:
        discount_sum = float(body.get("discount_sum") or 0)
    except (TypeError, ValueError):
        discount_sum = 0
    if not items_in:
        return _err("Savat bo'sh")

    # Mijoz + savdo turi (qarzga / Chinor ichki rasxod)
    try:
        client_id = int(body.get("client_id") or 0)
    except (TypeError, ValueError):
        client_id = 0
    is_nasiya = bool(body.get("is_nasiya"))
    is_internal = bool(body.get("is_internal"))

    client = None
    if client_id:
        client = await _db.get_client_by_id(client_id)
        if not client:
            return _err("Mijoz topilmadi", 404)
    # Ichki rasxod (Chinor): mijoz ichki bo'lishi shart
    if is_internal:
        if not client or not client.get("is_internal"):
            client = await _db.get_internal_client()
            if not client:
                return _err("«Chinor» ichki mijozi topilmadi", 400)
            client_id = client["id"]
        is_nasiya = False
    elif client and client.get("is_internal"):
        # «Chinor» tanlangan — har qanday holatda ichki rasxod
        is_internal = True
        is_nasiya = False
    # Nasiya (qarzga): faqat ruxsat berilgan mijozga
    if is_nasiya:
        if not _db.is_nasiya_enabled():
            return _err("Nasiya (qarzga) savdo o'chirilgan")
        if not client:
            return _err("Nasiya uchun mijoz tanlang")
        if not client.get("allow_credit"):
            return _err("Bu mijozga qarzga savdo ruxsat etilmagan")

    rate = _db.get_usd_rate()
    sale_items = []
    # Mahsulotlarni tekshirib, narxni SERVER hisoblaydi (mijoz narxiga ishonmaymiz)
    for it in items_in:
        try:
            pid = int(it.get("product_id"))
            qty = float(it.get("qty") or 0)
        except (TypeError, ValueError):
            return _err("Noto'g'ri savat elementi")
        if qty <= 0:
            continue
        p = await _db.get_product_any(pid)
        if not p or not p.get("is_active", 1):
            return _err(f"Mahsulot topilmadi (ID {pid})", 404)
        avail = float(p.get("qty", 0) or 0)
        if qty > avail:
            return _err(f"«{p['name']}» — faqat {avail:g} {p.get('unit','dona')} bor")
        # Narx so'mda kanonik (YesPOS marketplace sinxroni — pos_bridge.py).
        # Avval so'mdagi narx olinadi; USD faqat so'm bo'lmasa ishlatiladi.
        # Ilgari teskarisi edi — shu sabab ekranda yangi narx turib, chek eski
        # (USD'dan hisoblangan) narxda urilardi.
        price_sum = float(p.get("sell_price", 0) or 0)
        if price_sum > 0:
            price_usd = round(sum_to_usd(price_sum, rate), 4)
        else:
            price_usd = float(p.get("sell_price_usd", 0) or 0)
        sale_items.append({"product_id": pid, "qty": qty, "price": price_usd})

    if not sale_items:
        return _err("Savat bo'sh")

    # Jami (chegirma bilan)
    subtotal_sum = 0.0
    for si in sale_items:
        subtotal_sum += round(si["price"] * rate, 2) * si["qty"]
    override_sum = 0
    if discount_sum > 0 and discount_sum < subtotal_sum:
        override_sum = subtotal_sum - discount_sum

    eff_sum = override_sum if override_sum > 0 else subtotal_sum

    # Aralash to'lovda qismlar jami chek summasiga teng bo'lishi kerak
    if is_split and not is_internal and not is_nasiya:
        parts_total = split_cash + split_card + split_other + split_debt
        if abs(parts_total - eff_sum) > 1:
            return _err(f"Aralash to'lov qismlari ({parts_total:,.0f}) chek "
                        f"jamiga ({eff_sum:,.0f}) teng emas")
        if split_debt > 0:
            if not client:
                return _err("Qarz qismi uchun mijoz tanlang")
            if not client.get("allow_credit"):
                return _err("Bu mijozga qarzga savdo ruxsat etilmagan")

    # Chek raqami — kassa terminallari "<kassa_no>-<seq>" ishlatadi; Mini App
    # sotuvlariga "M-<seq>" beramiz (atomik hisoblagich — bir soniyada ikki
    # sotuv bo'lsa ham to'qnashmaydi).
    receipt_no = f"M-{_db.next_receipt_seq('miniapp')}"

    try:
        kw = {}
        if is_internal:
            # «Chinor» ichki rasxod — to'lov/chegirma yo'q, narx tannarxda
            kw = {"is_internal": True, "client_id": client_id}
        elif is_nasiya:
            # Qarzga — to'liq summa mijoz qarziga yoziladi
            kw = {"is_nasiya": True, "client_id": client_id}
        elif is_split:
            # Aralash to'lov: har qism o'z paid_* ustuniga, qarz qismi mijozga
            kw = {"paid_cash": split_cash, "paid_card": split_card,
                  "paid_other": split_other}
            if split_debt > 0 and client_id:
                kw["debt_sum"] = split_debt
            if client_id:
                kw["client_id"] = client_id
        else:
            if payment == "cash":
                kw["paid_cash"] = eff_sum
            elif payment == "card":
                kw["paid_card"] = eff_sum
            else:  # click
                kw["paid_other"] = eff_sum
            if client_id:
                kw["client_id"] = client_id  # chek mijozga Telegram orqali ketadi
        sale = await _db.create_sale(
            auth["tg_id"],
            auth["user"].get("full_name") or "Mini App",
            sale_items,
            paid_currency="sum",
            override_total_sum=(0 if is_internal else override_sum),
            source="miniapp",
            receipt_no=receipt_no,
            **kw,
        )
    except Exception as e:
        return _err(f"Sotuvni saqlashda xato: {e}", 500)

    return web.json_response({
        "ok": True,
        "sale_id": sale["id"],
        "receipt_no": sale.get("receipt_no", "") or "",
        "total_sum": float(sale.get("total", 0) or 0),
        "items_count": len(sale_items),
        "change_sum": float(sale.get("change", 0) or 0),
        "is_nasiya": bool(sale.get("is_nasiya")),
        "is_internal": bool(sale.get("is_internal")),
    })


# ─── Desktop kassa (offline) sinxronizatsiya ───────────────────────────────
# Windows kassa ilovasi internetsiz ishlaydi, internet kelganda shu
# endpoint'lar orqali sotuvlarni serverga yuboradi va katalogni yangilaydi.

_DESKTOP_SESSION_TTL = 30 * 24 * 3600  # 30 kun, sliding — kassa muntazam ishlatilsa qayta kirish kerak emas


@ROUTES.post("/api/desktop/login")
async def api_desktop_login(request: web.Request):
    """Kassa terminali uchun login (Telegram initData'siz).
    Faqat admin + 'sale' ruxsatiga ega hisoblar kira oladi.
    Uzoq muddatli (24 soat) session token qaytaradi."""
    from database.channel_db import db as _db
    from bot.permissions import has_permission

    body = await _read_json(request)
    login_in = (body.get("login") or "").strip()
    password_in = (body.get("password") or "").strip()
    if not login_in or not password_in:
        return _err("Login yoki parol bo'sh")
    if len(login_in) > 64 or len(password_in) > 128:
        return _err("Maydon juda uzun")

    result = await verify_login(_db, login_in, password_in)
    if not result or result.get("role") != "admin":
        return _err("Login yoki parol noto'g'ri (faqat kassir/admin kira oladi)", 401)

    user = result["user"]
    tg_id = int(user.get("telegram_id") or user.get("id") or 0)
    if tg_id != GLAVNIY_ADMIN_ID and not await has_permission(_db, tg_id, "sale"):
        return _err("Bu hisobda kassada sotuv ruxsati yo'q", 403)

    auth_info = {
        "role": "admin", "tg_id": tg_id, "user": user,
        "is_glavniy": (tg_id == GLAVNIY_ADMIN_ID), "desktop": True,
    }
    token = _create_session(auth_info, ttl=_DESKTOP_SESSION_TTL)
    cashier_name = user.get("full_name") or user.get("username") or login_in
    # Bu terminalga (device_id) DOIMIY noyob kassa raqamini biriktiramiz —
    # chek raqami prefiksi shu raqam bo'ladi ("<kassa_no>-<seq>"), shunda turli
    # terminallar chek raqamlari to'qnashmaydi va qurilmalararo sinxron buzilmaydi.
    device_id = (body.get("device_id") or "").strip()
    kassa_no = 0
    if device_id:
        try:
            kassa_no = await _db.assign_kassa_no(device_id, name=cashier_name)
        except Exception as e:
            logger.warning(f"[desktop/login] kassa_no biriktirish xato: {e}")
    return web.json_response({
        "ok": True,
        "session_token": token,
        "cashier": {
            "id": tg_id,
            "name": cashier_name,
        },
        "kassa_no": kassa_no,
        "usd_rate": _db.get_usd_rate(),
    })


async def _desktop_auth(request: web.Request):
    """Kassa (desktop) session'ini tekshiradi. Auth bo'lsa dict, bo'lmasa None."""
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth or auth.get("role") != "admin":
        return None
    return auth


@ROUTES.post("/api/desktop/payment/begin")
async def api_payment_begin(request: web.Request):
    """Kassa QR to'lovni boshlaydi. Server "shu vaqtdan keyin" belgisini (since)
    qaytaradi — keyin shu paytdan KEYIN kelgan link hisobga olinadi."""
    if await _desktop_auth(request) is None:
        return _err("Avtorizatsiya talab qilinadi", 401)
    if not payment_relay.is_enabled():
        return web.json_response({"ok": False, "error": "QR to'lov sozlanmagan"})
    return web.json_response({"ok": True, "since": payment_relay.begin()})


@ROUTES.get("/api/desktop/payment/poll")
async def api_payment_poll(request: web.Request):
    """`since` dan keyin egadan kelgan to'lov linki bo'lsa qaytaradi."""
    if await _desktop_auth(request) is None:
        return _err("Avtorizatsiya talab qilinadi", 401)
    try:
        since = float(request.query.get("since", "0") or 0)
    except (TypeError, ValueError):
        since = 0.0
    link = payment_relay.get_link(since)
    if link:
        return web.json_response({"ok": True, "link": link})
    return web.json_response({"ok": False})


@ROUTES.post("/api/desktop/payment/notify")
async def api_payment_notify(request: web.Request):
    """Kassadan egaga to'lov boti orqali xabar yuboradi (link so'rovi va h.k.)."""
    if await _desktop_auth(request) is None:
        return _err("Avtorizatsiya talab qilinadi", 401)
    body = await _read_json(request)
    text = (body.get("text") or "").strip()
    if not text:
        return _err("Matn bo'sh")
    await payment_relay.send_message(text)
    return web.json_response({"ok": True})


@ROUTES.post("/api/desktop/draft-post")
async def api_desktop_draft_post(request: web.Request):
    """Kassada qoralama (parked) chek saqlanganda — uni «qoralama cheklar»
    kanaliga post qiladi. Body: {draft: {id, items[], client, created_at}}.
    items — [{name, qty, price_sum, unit}]. Best-effort: kassa onlayn bo'lsa
    chaqiradi, xato bo'lsa kassa savdoni baribir davom ettiradi."""
    from database.channel_db import db as _db
    from database._formatters import _fmt_draft
    from bot.config import CHANNEL_DRAFTS_ID

    auth = await _desktop_auth(request)
    if auth is None:
        return _err("Avtorizatsiya talab qilinadi", 401)

    body = await _read_json(request)
    draft = body.get("draft") or {}
    if not isinstance(draft, dict) or not draft.get("items"):
        return _err("Qoralama bo'sh")

    # Kassir nomini session'dan to'ldiramiz (kassa yubormagan bo'lsa).
    if not draft.get("cashier_name"):
        draft["cashier_name"] = auth["user"].get("full_name") or "Kassa"

    # Klient telefonini bazadan to'ldiramiz (nakladnoyda ko'rinadi)
    client_phone = ""
    cl = draft.get("client") or {}
    if isinstance(cl, dict) and cl.get("id"):
        try:
            c = await _db.get_client_by_id(int(cl["id"]))
            if c:
                client_phone = c.get("phone", "") or ""
        except Exception:
            pass

    # Excel NAKLADNOY yasab, kanalga HUJJAT sifatida yuboramiz (matn caption
    # bilan). Excel yasashda xato bo'lsa — eski usul: faqat matnli post.
    caption = _fmt_draft(draft)
    try:
        from bot.exporter import build_draft_invoice_bytes
        data, fname = build_draft_invoice_bytes(draft, client_phone)
        mid = await _db._post_document_to(CHANNEL_DRAFTS_ID, fname, data, caption)
        if mid:
            return web.json_response({"ok": True, "document": True})
        # hujjat ketmadi — matnga tushamiz
        logger.warning("[draft-post] hujjat yuborilmadi — matnli postga o'tildi")
    except Exception as e:
        logger.warning(f"[draft-post] Excel yasashda xato: {e} — matnli post")

    try:
        await _db._post_to(CHANNEL_DRAFTS_ID, caption)
    except Exception as e:
        logger.warning(f"[draft-post] kanal post xato: {e}")
        return web.json_response({"ok": False, "error": str(e)})
    return web.json_response({"ok": True})


@ROUTES.get("/api/sync/catalog")
async def api_sync_catalog(request: web.Request):
    """Offline kesh uchun barcha aktiv mahsulotlar + USD kurs."""
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth or auth["role"] != "admin":
        return _err("Avtorizatsiya talab qilinadi", 401)

    # Bu terminalga (device_id) noyob kassa raqamini biriktiramiz — login'dan
    # tashqari HAR sinxronda ham, shunda qurilma qayta login qilmasa ham raqamni
    # avtomatik oladi (chek prefiksi "<kassa_no>-<seq>", terminallar to'qnashmaydi).
    device_id = (request.query.get("device_id") or "").strip()
    kassa_no = 0
    if device_id:
        try:
            kassa_no = await _db.assign_kassa_no(
                device_id, name=(auth["user"].get("full_name") or ""))
        except Exception as e:
            logger.warning(f"[sync/catalog] kassa_no biriktirish xato: {e}")

    prods = await _db.get_all_products(active_only=True)
    out = []
    for p in prods:
        out.append({
            "id": p["id"],
            "name": p.get("name", ""),
            "barcode": str(p.get("barcode") or "").strip(),
            "unit": p.get("unit", "dona"),
            "qty": float(p.get("qty", 0) or 0),
            "sell_price_sum": float(p.get("sell_price", 0) or 0),
            "sell_price_usd": float(p.get("sell_price_usd", 0) or 0),
            "wholesale_sum": float(p.get("wholesale_price", 0) or 0),
            "wholesale_usd": float(p.get("wholesale_price_usd", 0) or 0),
            "cost_sum": float(p.get("cost_price", 0) or 0),
            "cost_usd": float(p.get("cost_price_usd", 0) or 0),
            "image_url": p.get("image_url", "") or "",
        })
    clients = []
    try:
        for c in await _db.get_all_clients():
            clients.append({
                "id": c["id"],
                "name": c.get("shop_name") or c.get("username") or f"Mijoz #{c['id']}",
                "type": (c.get("client_type") or "dona"),
                "debt_sum": float(c.get("debt", 0) or 0),
                "is_internal": 1 if c.get("is_internal") else 0,
                "allow_credit": 1 if c.get("allow_credit") else 0,
            })
    except Exception as e:
        logger.warning(f"[sync/catalog] klientlar xato: {e}")

    return web.json_response({
        "ok": True,
        "usd_rate": _db.get_usd_rate(),
        "products": out,
        "count": len(out),
        "clients": clients,
        "kassa_no": kassa_no,
    })


@ROUTES.post("/api/sync/sales")
async def api_sync_sales(request: web.Request):
    """Offline kassada qilingan sotuvlar to'plamini bazaga yozadi.
    Har bir sotuv: {local_id, items:[{product_id, qty, price_sum?}],
                    payment, discount_sum, created_at}.
    Qaytaradi: har bir local_id uchun {ok, server_id|error}.
    Internet uzilib qaytsa, kassa faqat YUBORILMAGAN sotuvlarni qayta yuboradi,
    shuning uchun bu yerda dublikat bo'lmasligi kassa tomonida ta'minlanadi."""
    from database.channel_db import db as _db
    from database._helpers import sum_to_usd
    auth = await _authenticate(request, _db)
    if not auth or auth["role"] != "admin":
        return _err("Avtorizatsiya talab qilinadi", 401)

    body = await _read_json(request)
    sales_in = body.get("sales") or []
    if not isinstance(sales_in, list):
        return _err("sales massiv bo'lishi kerak")

    rate = _db.get_usd_rate()
    results = []
    for s in sales_in:
        local_id = s.get("local_id")
        try:
            items_in = s.get("items") or []
            payment = (s.get("payment") or "cash").lower()
            if payment not in ("cash", "card", "click", "other", "split"):
                payment = "cash"

            # Split (aralash) to'lov — kassa har turdagi summani alohida yuboradi:
            # paid_cash + paid_card + paid_other (click) + debt_sum (qarz qismi).
            def _num(v):
                try:
                    return max(0.0, float(v or 0))
                except (TypeError, ValueError):
                    return 0.0
            split_cash = _num(s.get("paid_cash"))
            split_card = _num(s.get("paid_card"))
            split_other = _num(s.get("paid_other"))
            split_debt = _num(s.get("debt_sum"))
            is_split = (payment == "split") or \
                (split_cash + split_card + split_other + split_debt) > 0
            try:
                client_id = int(s.get("client_id") or 0)
            except (TypeError, ValueError):
                client_id = 0
            is_nasiya = bool(s.get("is_nasiya"))
            is_internal = bool(s.get("is_internal"))
            is_return = bool(s.get("is_return"))
            created_at = (s.get("created_at") or "").strip()
            source = (s.get("source") or "").strip()[:80]
            receipt_no = (s.get("receipt_no") or "").strip()[:32]
            orig_receipt_no = (s.get("orig_receipt_no") or "").strip()[:32]

            # Dublikat himoyasi: kassa yuborgan, lekin javob unga yetib
            # bormagan bo'lsa, o'sha chekni qayta yuboradi — shu chek raqami
            # (receipt_no) va qurilma (source) bilan yozuv allaqachon bo'lsa,
            # QAYTA YOZMAY mavjud id qaytariladi (idempotent).
            if receipt_no and source:
                dup_id = await _db.find_sale_by_receipt(receipt_no, source)
                if dup_id:
                    logger.info(f"[sync/sales] dublikat o'tkazildi: "
                                f"{source} {receipt_no} → id={dup_id}")
                    results.append({"local_id": local_id, "ok": True,
                                    "server_id": dup_id, "duplicate": True})
                    continue
            try:
                total_in = float(s.get("total_sum") or 0)
            except (TypeError, ValueError):
                total_in = 0.0

            sale_items = []
            subtotal_sum = 0.0
            for it in items_in:
                pid = int(it.get("product_id"))
                qty = float(it.get("qty") or 0)
                if qty <= 0:
                    continue
                # Kassir narxni o'zgartirgan bo'lishi mumkin — avval kassadan
                # kelgan haqiqiy narxni ishlatamiz; bo'lmasa katalog narxi.
                price_sum_in = float(it.get("price_sum") or 0)
                if price_sum_in > 0:
                    price_usd = round(sum_to_usd(price_sum_in, rate), 4)
                else:
                    p = await _db.get_product_any(pid)
                    price_usd = float(p.get("sell_price_usd", 0) or 0) if p else 0.0
                    if price_usd <= 0:
                        base_sum = float(p.get("sell_price", 0) or 0) if p else 0.0
                        price_usd = round(sum_to_usd(base_sum, rate), 4)
                sale_items.append({
                    "product_id": pid, "qty": qty, "price": price_usd,
                })
                subtotal_sum += round(price_usd * rate, 2) * qty

            if not sale_items:
                results.append({"local_id": local_id, "ok": False,
                                "error": "Savat bo'sh"})
                continue

            # Qaytarish (refund) — qoldiq tiklanadi, manfiy summali yozuv.
            # return_method: cash|card|click|debt (debt = mijoz qarzidan ayirish)
            if is_return:
                ret_method = (s.get("return_method") or payment or "cash").lower()
                if ret_method not in ("cash", "card", "click", "other", "debt"):
                    ret_method = "cash"
                ret = await _db.create_return(
                    auth["tg_id"],
                    auth["user"].get("full_name") or "Kassa (offline)",
                    sale_items,
                    method=ret_method,
                    client_id=client_id,
                    created_at=created_at,
                    source=source,
                    receipt_no=receipt_no,
                    orig_receipt_no=orig_receipt_no,
                )
                results.append({"local_id": local_id, "ok": True,
                                "server_id": ret["id"]})
                continue

            # Savdo turi: «Chinor» ichki rasxod / qarzga (nasiya) / oddiy
            client = await _db.get_client_by_id(client_id) if client_id else None
            if is_internal or (client and client.get("is_internal")):
                is_internal = True
                is_nasiya = False
                if not client or not client.get("is_internal"):
                    client = await _db.get_internal_client()
                    if client:
                        client_id = client["id"]
            elif is_nasiya and not client_id:
                # Nasiya uchun mijoz shart — bo'lmasa oddiy savdo sifatida yoziladi
                is_nasiya = False

            # Kassada yumaloqlangan/o'zgartirilgan yakuniy jami bo'lsa — o'sha,
            # bo'lmasa item narxlaridan hisoblangan jami.
            override_sum = total_in if total_in > 0 else subtotal_sum

            kw = {}
            if is_internal:
                kw = {"is_internal": True}
                override_sum = 0  # ichki rasxod — tannarx bo'yicha, chegirmasiz
            elif is_split:
                # Aralash to'lov: har qism o'z paid_* ustuniga; qarz qismi
                # (faqat mijoz bo'lsa) qisman qarzga yoziladi.
                kw = {
                    "paid_cash": split_cash,
                    "paid_card": split_card,
                    "paid_other": split_other,
                }
                if split_debt > 0 and client_id:
                    kw["debt_sum"] = split_debt
            elif is_nasiya:
                kw = {"is_nasiya": True}
            else:
                eff_sum = override_sum
                if payment == "cash":
                    kw = {"paid_cash": eff_sum}
                elif payment == "card":
                    kw = {"paid_card": eff_sum}
                else:  # click / payme / other
                    kw = {"paid_other": eff_sum}

            sale = await _db.create_sale(
                auth["tg_id"],
                auth["user"].get("full_name") or "Kassa (offline)",
                sale_items,
                paid_currency="sum",
                override_total_sum=override_sum,
                created_at=created_at,
                client_id=client_id,
                source=source,
                receipt_no=receipt_no,
                **kw,
            )
            results.append({"local_id": local_id, "ok": True,
                            "server_id": sale["id"]})
        except Exception as e:
            logger.warning(f"[sync/sales] local_id={local_id} xato: {e}")
            results.append({"local_id": local_id, "ok": False, "error": str(e)})

    ok_count = sum(1 for r in results if r.get("ok"))
    logger.info(f"[sync/sales] {ok_count}/{len(results)} sotuv qabul qilindi")
    return web.json_response({"ok": True, "results": results})


@ROUTES.post("/api/sync/cash-txns")
async def api_sync_cash_txns(request: web.Request):
    """Offline kassa tranzaksiyalari to'plamini bazaga yozadi:
      • debt_payment — mijoz qarz to'lovi (qarzni kamaytiradi + Telegram chek)
      • cash_in / cash_out — naqd kirim/chiqim (cash_movements jadvali + Telegram log)
    Har biri: {local_id, type, method, amount, client_id?, note, category?,
               recipient?, by?, created_at}.
    Qaytaradi: har bir local_id uchun {ok, server_id|error}. Dublikatsizlik
    kassa tomonida ta'minlanadi (faqat YUBORILMAGANLARI qayta yuboriladi)."""
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth or auth["role"] != "admin":
        return _err("Avtorizatsiya talab qilinadi", 401)

    body = await _read_json(request)
    txns_in = body.get("txns") or []
    if not isinstance(txns_in, list):
        return _err("txns massiv bo'lishi kerak")

    cashier_name = auth["user"].get("full_name") or "Kassa (offline)"
    results = []
    for t in txns_in:
        local_id = t.get("local_id")
        try:
            ttype = (t.get("type") or "").strip()
            amount = abs(float(t.get("amount") or 0))
            if amount <= 0:
                results.append({"local_id": local_id, "ok": False,
                                "error": "Summa noto'g'ri"})
                continue
            note = (t.get("note") or "").strip()
            created_at = (t.get("created_at") or "").strip()
            source = (t.get("source") or "").strip()[:80]
            by = (t.get("by") or "").strip()[:80] or cashier_name

            if ttype == "debt_payment":
                try:
                    client_id = int(t.get("client_id") or 0)
                except (TypeError, ValueError):
                    client_id = 0
                if not client_id:
                    results.append({"local_id": local_id, "ok": False,
                                    "error": "Mijoz tanlanmagan"})
                    continue
                method = (t.get("method") or "cash").lower()
                if method not in ("cash", "card", "click"):
                    method = "cash"
                # To'lov usulini izohga yozamiz (payments jadvalida usul ustuni yo'q)
                full_note = f"Kassa to'lovi ({method})"
                if note:
                    full_note += f" — {note}"
                pay = await _db.add_payment(client_id, amount, "sum", full_note)
                if not pay:
                    results.append({"local_id": local_id, "ok": False,
                                    "error": "Mijoz topilmadi"})
                    continue
                results.append({"local_id": local_id, "ok": True,
                                "server_id": pay.get("id")})

            elif ttype in ("cash_in", "cash_out"):
                direction = "out" if ttype == "cash_out" else "in"
                mv = await _db.add_cash_movement(
                    direction, amount,
                    category=(t.get("category") or "").strip()[:60],
                    note=note,
                    recipient=(t.get("recipient") or "").strip()[:80],
                    cashier_id=auth["tg_id"],
                    cashier_name=by,
                    source=source,
                    created_at=created_at,
                )
                results.append({"local_id": local_id, "ok": True,
                                "server_id": mv.get("id")})
            else:
                results.append({"local_id": local_id, "ok": False,
                                "error": f"Noma'lum tur: {ttype}"})
        except Exception as e:
            logger.warning(f"[sync/cash-txns] local_id={local_id} xato: {e}")
            results.append({"local_id": local_id, "ok": False, "error": str(e)})

    ok_count = sum(1 for r in results if r.get("ok"))
    logger.info(f"[sync/cash-txns] {ok_count}/{len(results)} tranzaksiya qabul qilindi")
    return web.json_response({"ok": True, "results": results})


@ROUTES.get("/api/sync/recent-sales")
async def api_recent_sales(request: web.Request):
    """Hamma manbalardan (kassa, bot, mini app) so'nggi sotuvlar — kassa
    ilovasidagi 'Cheklar' uchun. Har birida manba (qurilma), kassir, vaqt."""
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth or auth["role"] != "admin":
        return _err("Avtorizatsiya talab qilinadi", 401)
    since = (request.query.get("since") or "").strip()
    # `since` berilsa inkremental sinxron — ko'proq yozuvga ruxsat
    cap = 1000 if since else 200
    try:
        limit = min(cap, max(1, int(request.query.get("limit", "60"))))
    except (TypeError, ValueError):
        limit = cap if since else 60

    sales = await _db.get_recent_sales(limit, since)
    out = []
    for s in sales:
        p_cash = float(s.get("paid_cash", 0) or 0)
        p_card = float(s.get("paid_card", 0) or 0)
        p_other = float(s.get("paid_other", 0) or 0)
        p_total = float(s.get("paid_total", 0) or 0)
        s_total = float(s.get("total", 0) or 0)
        # Aralash to'lovda qarzga yozilgan qism (to'liq nasiya emas)
        p_debt = 0.0
        if (not s.get("is_nasiya") and not s.get("is_internal")
                and not s.get("is_return") and p_total > 0):
            p_debt = max(0.0, round(s_total - p_total, 2))
        if s.get("is_return"):
            pay = "qaytarish"
        elif s.get("is_internal"):
            pay = "rasxod"
        elif s.get("is_nasiya"):
            pay = "qarz"
        elif sum(1 for v in (p_cash, p_card, p_other, p_debt) if v > 0) > 1:
            pay = "split"   # aralash — qismlari paid_* maydonlarida
        elif p_card > 0:
            pay = "card"
        elif p_other > 0:
            pay = "click"
        else:
            pay = "cash"
        items = [{
            "name": it.get("name", ""),
            "qty": it.get("qty", 0),
            "price_sum": float(it.get("price", 0) or 0),
            "product_id": it.get("product_id", 0),
        } for it in (s.get("items") or [])]
        # Chek foydasi — admin «Foyda» grafigi uchun. Ichki rasxod foyda emas.
        # Qaytarishda qty manfiy → foyda ham manfiy chiqadi (netto to'g'ri).
        total_sum = float(s.get("total", 0) or 0)
        if s.get("is_internal"):
            profit_sum = 0.0
        else:
            cost_sum = sum(
                float(it.get("cost_price", 0) or 0) * float(it.get("qty", 0) or 0)
                for it in (s.get("items") or [])
            )
            profit_sum = total_sum - cost_sum
        # Qaytarish bo'lsa — asl chek raqamini source ichidan ajratamiz
        src = s.get("source", "") or ""
        orig_rno = src.split("qaytarish←")[1].split(" ")[0] if "qaytarish←" in src else ""
        out.append({
            "id": s.get("id"),
            "receipt_no": s.get("receipt_no", "") or "",
            "created_at": s.get("created_at", ""),
            "cashier_name": s.get("cashier_name", "") or "",
            "source": s.get("source", "") or "",
            "total_sum": total_sum,
            "profit_sum": profit_sum,
            "subtotal_sum": float(s.get("subtotal", 0) or 0),
            "discount_sum": float(s.get("discount", 0) or 0),
            "payment": pay,
            # To'lov qismlari — split chekni hisobotda to'g'ri taqsimlash uchun
            "paid_cash": p_cash,
            "paid_card": p_card,
            "paid_other": p_other,
            "debt_part": p_debt,
            "client_name": s.get("client_name", "") or "",
            "client_id": int(s.get("client_id", 0) or 0),
            "is_internal": 1 if s.get("is_internal") else 0,
            "is_nasiya": 1 if s.get("is_nasiya") else 0,
            "is_return": 1 if s.get("is_return") else 0,
            "orig_receipt_no": orig_rno,
            "items": items,
        })

    # YesPOS kassasidagi savdo — «offline savdo» sifatida shu ro'yxatga
    # qo'shiladi. Admin ilovasidagi «Cheklar» va «Sotuvlar grafigi» aynan shu
    # endpointdan oziqlanadi, Chinor `sales` jadvali esa deyarli bo'sh —
    # shuning uchun ular bo'sh chiqardi. Manbasi `source: "YesPOS"` bo'lib
    # ko'rinadi, id manfiy (Chinor id lari bilan to'qnashmaydi).
    try:
        from bot.api_receipts import recent_sales as _yespos_sales
        out.extend(_yespos_sales(limit=limit, since=since))
        out.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    except Exception as e:
        logger.warning(f"[recent-sales] YesPOS cheklari qo'shilmadi: {e}")

    return web.json_response({"ok": True, "sales": out})


# ─── Foydalanuvchilar (admin) ──────────────────────────────────────────────

@ROUTES.get("/api/clients")
async def api_clients(request: web.Request):
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth:
        return _err("Avtorizatsiya talab qilinadi", 401)
    if auth["role"] != "admin":
        return _err("Faqat adminlar uchun", 403)
    clients = await _db.get_all_clients()
    items = []
    for c in clients:
        items.append({
            "id": c.get("id", 0),
            "shop_name": c.get("shop_name", ""),
            "phone": c.get("phone", ""),
            "debt_sum": float(c.get("debt", 0) or 0),
            "debt_usd": float(c.get("debt_usd", 0) or 0),
            "client_type": c.get("client_type", "dona"),
            "telegram_id": c.get("telegram_id", 0),
            "is_internal": 1 if c.get("is_internal") else 0,
            "allow_credit": 1 if c.get("allow_credit") else 0,
            "bonus_balance": float(c.get("bonus_balance", 0) or 0),
            "created_at": (c.get("created_at", "") or "")[:16],
        })
    return web.json_response({"ok": True, "items": items, "total": len(items)})


@ROUTES.post("/api/client/save")
async def api_client_save(request: web.Request):
    """Yangi mijoz yaratish (kassa/Mini App'dan). Body: {shop_name, phone,
    client_type: dona|optom, allow_credit: bool}. Faqat admin."""
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth or auth["role"] != "admin":
        return _err("Avtorizatsiya talab qilinadi", 401)
    body = await _read_json(request)
    shop_name = (body.get("shop_name") or body.get("name") or "").strip()[:80]
    phone = (body.get("phone") or "").strip()[:32]
    client_type = (body.get("client_type") or "dona").strip().lower()
    allow_credit = bool(body.get("allow_credit"))
    if not shop_name:
        return _err("Mijoz nomi bo'sh")
    if len("".join(ch for ch in phone if ch.isdigit())) < 7:
        return _err("Telefon raqami noto'g'ri (kamida 7 raqam)")
    ok = await _db.add_client(None, shop_name, phone, auth["tg_id"], client_type)
    if not ok:
        return _err("Bu telefon raqamli mijoz allaqachon mavjud", 409)
    c = await _db.get_client_by_phone(phone)
    if not c:
        return _err("Mijoz saqlandi, lekin qayta o'qilmadi", 500)
    if allow_credit:
        await _db.set_client_allow_credit(c["id"], True)
    return web.json_response({"ok": True, "client": {
        "id": c["id"],
        "name": c.get("shop_name", ""),
        "phone": c.get("phone", ""),
        "type": c.get("client_type", "dona"),
        "allow_credit": 1 if allow_credit else 0,
        "is_internal": 0,
        "debt_sum": 0,
    }})


@ROUTES.post("/api/client/type")
async def api_client_type(request: web.Request):
    """Mijoz turini almashtirish: dona ⇄ optom. Body: {client_id, client_type}.
    Faqat admin. Ichki (Chinor rasxod) mijozga qo'llanmaydi."""
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth or auth["role"] != "admin":
        return _err("Avtorizatsiya talab qilinadi", 401)
    body = await _read_json(request)
    try:
        cid = int(body.get("client_id") or 0)
    except (TypeError, ValueError):
        cid = 0
    if not cid:
        return _err("client_id kerak")
    c = await _db.get_client_by_id(cid)
    if not c:
        return _err("Mijoz topilmadi", 404)
    if c.get("is_internal"):
        return _err("«Chinor» ichki mijozining turi o'zgartirilmaydi")
    ctype = "optom" if str(body.get("client_type") or "").lower().startswith("opt") else "dona"
    await _db.set_client_type(cid, ctype)
    return web.json_response({"ok": True, "client_id": cid, "client_type": ctype})


@ROUTES.post("/api/client/credit")
async def api_client_credit(request: web.Request):
    """Mijozni qarzga (nasiya) savdoga belgilash / belgini olib tashlash.
    Body: {client_id, allow: true|false}. Faqat admin."""
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth:
        return _err("Avtorizatsiya talab qilinadi", 401)
    if auth["role"] != "admin":
        return _err("Faqat adminlar uchun", 403)
    body = await _read_json(request)
    try:
        cid = int(body.get("client_id") or 0)
    except (TypeError, ValueError):
        cid = 0
    if not cid:
        return _err("client_id kerak")
    c = await _db.get_client_by_id(cid)
    if not c:
        return _err("Mijoz topilmadi", 404)
    if c.get("is_internal"):
        return _err("«Chinor» ichki mijoziga qarz qo'llanmaydi")
    allow = bool(body.get("allow"))
    await _db.set_client_allow_credit(cid, allow)
    return web.json_response({"ok": True, "client_id": cid, "allow_credit": 1 if allow else 0})


@ROUTES.post("/api/client/update")
async def api_client_update(request: web.Request):
    """Mijozni tahrirlash (admin paneli). Body: {client_id, shop_name?, phone?,
    client_type?, allow_credit?}. Berilgan maydonlargina o'zgaradi."""
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth or auth["role"] != "admin":
        return _err("Avtorizatsiya talab qilinadi", 401)
    body = await _read_json(request)
    try:
        cid = int(body.get("client_id") or 0)
    except (TypeError, ValueError):
        cid = 0
    if not cid:
        return _err("client_id kerak")
    c = await _db.get_client_by_id(cid)
    if not c:
        return _err("Mijoz topilmadi", 404)
    if c.get("is_internal"):
        return _err("«Chinor» ichki mijozi tahrirlanmaydi")
    shop_name = body.get("shop_name")
    phone = body.get("phone")
    if shop_name is not None or phone is not None:
        if phone is not None and len("".join(ch for ch in str(phone) if ch.isdigit())) < 7:
            return _err("Telefon raqami noto'g'ri (kamida 7 raqam)")
        ok, why = await _db.update_client(
            cid,
            shop_name=str(shop_name) if shop_name is not None else None,
            phone=str(phone) if phone is not None else None,
        )
        if not ok:
            return _err(why or "Saqlanmadi", 409)
    if body.get("client_type") is not None:
        ctype = "optom" if str(body["client_type"]).lower().startswith("opt") else "dona"
        await _db.set_client_type(cid, ctype)
    if body.get("allow_credit") is not None:
        await _db.set_client_allow_credit(cid, bool(body["allow_credit"]))
    c = await _db.get_client_by_id(cid)
    return web.json_response({"ok": True, "client": _client_json(c)})


@ROUTES.post("/api/client/delete")
async def api_client_delete(request: web.Request):
    """Mijozni o'chirish. Body: {client_id}. Ichki (Chinor) mijoz o'chmaydi;
    qarzi bor mijoz ham himoyalangan (avval qarz yopilsin)."""
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth or auth["role"] != "admin":
        return _err("Avtorizatsiya talab qilinadi", 401)
    body = await _read_json(request)
    try:
        cid = int(body.get("client_id") or 0)
    except (TypeError, ValueError):
        cid = 0
    if not cid:
        return _err("client_id kerak")
    c = await _db.get_client_by_id(cid)
    if not c:
        return _err("Mijoz topilmadi", 404)
    if c.get("is_internal"):
        return _err("«Chinor» ichki mijozi o'chirilmaydi")
    if float(c.get("debt", 0) or 0) > 0:
        return _err("Qarzi bor mijoz o'chirilmaydi — avval qarzni yoping", 409)
    await _db.delete_client(cid)
    return web.json_response({"ok": True, "client_id": cid})


@ROUTES.post("/api/client/payment")
async def api_client_payment(request: web.Request):
    """Mijozdan qarz to'lovini qabul qilish (admin paneli).
    Body: {client_id, amount, method: cash|card|click, note?}."""
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth or auth["role"] != "admin":
        return _err("Avtorizatsiya talab qilinadi", 401)
    body = await _read_json(request)
    try:
        cid = int(body.get("client_id") or 0)
        amount = float(body.get("amount") or 0)
    except (TypeError, ValueError):
        return _err("client_id/amount noto'g'ri")
    if not cid or amount <= 0:
        return _err("Mijoz va musbat summa kerak")
    c = await _db.get_client_by_id(cid)
    if not c:
        return _err("Mijoz topilmadi", 404)
    if c.get("is_internal"):
        return _err("«Chinor» ichki mijoziga to'lov yozilmaydi")
    method = (body.get("method") or "cash").lower()
    if method not in ("cash", "card", "click"):
        method = "cash"
    note = (body.get("note") or "").strip()[:160]
    by = auth["user"].get("full_name") or "Admin"
    full_note = f"Admin panel to'lovi ({method}) — {by}"
    if note:
        full_note += f": {note}"
    pay = await _db.add_payment(cid, amount, "sum", full_note)
    if not pay:
        return _err("To'lov yozilmadi", 500)
    c = await _db.get_client_by_id(cid)
    return web.json_response({"ok": True, "payment_id": pay.get("id"),
                              "client": _client_json(c)})


@ROUTES.post("/api/client/bonus")
async def api_client_bonus(request: web.Request):
    """Mijoz bonusini qo'lda o'zgartirish (+qo'shish / -ishlatish).
    Body: {client_id, delta, reason?}. Balans 0 dan pastga tushmaydi."""
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth or auth["role"] != "admin":
        return _err("Avtorizatsiya talab qilinadi", 401)
    body = await _read_json(request)
    try:
        cid = int(body.get("client_id") or 0)
        delta = float(body.get("delta") or 0)
    except (TypeError, ValueError):
        return _err("client_id/delta noto'g'ri")
    if not cid or not delta:
        return _err("Mijoz va nolga teng bo'lmagan delta kerak")
    c = await _db.get_client_by_id(cid)
    if not c:
        return _err("Mijoz topilmadi", 404)
    if c.get("is_internal"):
        return _err("«Chinor» ichki mijoziga bonus qo'llanmaydi")
    by = auth["user"].get("full_name") or "Admin"
    reason = (body.get("reason") or "").strip()[:160] or \
        ("Qo'lda qo'shildi" if delta > 0 else "Qo'lda ishlatildi")
    new_bal = await _db.add_bonus(cid, delta, reason=reason, by_name=by)
    if new_bal is None:
        return _err("Bonus yozilmadi", 500)
    return web.json_response({"ok": True, "client_id": cid,
                              "bonus_balance": new_bal})


def _client_json(c: dict) -> dict:
    """Mijozni admin paneli formatiga o'girish (bitta joyda — takror bo'lmasin)."""
    if not c:
        return {}
    return {
        "id": c.get("id", 0),
        "shop_name": c.get("shop_name", ""),
        "phone": c.get("phone", ""),
        "debt_sum": float(c.get("debt", 0) or 0),
        "debt_usd": float(c.get("debt_usd", 0) or 0),
        "client_type": c.get("client_type", "dona"),
        "telegram_id": c.get("telegram_id", 0),
        "is_internal": 1 if c.get("is_internal") else 0,
        "allow_credit": 1 if c.get("allow_credit") else 0,
        "bonus_balance": float(c.get("bonus_balance", 0) or 0),
        "created_at": (c.get("created_at", "") or "")[:16],
    }


def _sale_pay_kind(s: dict) -> str:
    """Sotuvning to'lov turini aniqlaydi (recent-sales bilan bir xil mantiq)."""
    if s.get("is_return"):
        return "qaytarish"
    if s.get("is_internal"):
        return "rasxod"
    if s.get("is_nasiya"):
        return "qarz"
    p_cash = float(s.get("paid_cash", 0) or 0)
    p_card = float(s.get("paid_card", 0) or 0)
    p_other = float(s.get("paid_other", 0) or 0)
    p_total = float(s.get("paid_total", 0) or 0)
    debt_part = max(0.0, round(float(s.get("total", 0) or 0) - p_total, 2)) \
        if p_total > 0 else 0.0
    if sum(1 for v in (p_cash, p_card, p_other, debt_part) if v > 0) > 1:
        return "split"
    if p_card > 0:
        return "card"
    if p_other > 0:
        return "click"
    return "cash"


@ROUTES.get("/api/client/detail")
async def api_client_detail(request: web.Request):
    """Mijoz kartochkasi: ma'lumot + statistika + so'nggi xaridlar +
    to'lovlar + bonus tarixi. Query: ?client_id=N"""
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth or auth["role"] != "admin":
        return _err("Avtorizatsiya talab qilinadi", 401)
    try:
        cid = int(request.query.get("client_id") or 0)
    except (TypeError, ValueError):
        cid = 0
    if not cid:
        return _err("client_id kerak")
    c = await _db.get_client_by_id(cid)
    if not c:
        return _err("Mijoz topilmadi", 404)
    stats = await _db.get_client_stats(cid)
    sales = await _db.get_client_sales(cid, 60)
    payments = await _db.get_client_payments(cid, 60)
    bonus_log = await _db.get_bonus_log(cid, 100)
    sales_out = [{
        "id": s.get("id", 0),
        "receipt_no": s.get("receipt_no", "") or str(s.get("id", 0)),
        "created_at": (s.get("created_at", "") or "")[:16],
        "total_sum": float(s.get("total", 0) or 0),
        "payment": _sale_pay_kind(s),
        "is_return": 1 if s.get("is_return") else 0,
        "item_count": len(s.get("items") or []),
        "cashier_name": s.get("cashier_name", "") or "",
        "items": [{
            "name": it.get("name", ""),
            "qty": it.get("qty", 0),
            "price_sum": float(it.get("price", 0) or 0),
        } for it in (s.get("items") or [])],
    } for s in sales]
    pays_out = [{
        "id": p.get("id", 0),
        "amount": float(p.get("amount", 0) or 0),
        "note": p.get("note", "") or "",
        "created_at": (p.get("created_at", "") or "")[:16],
    } for p in payments]
    bonus_out = [{
        "id": b.get("id", 0),
        "delta": float(b.get("delta", 0) or 0),
        "balance_after": float(b.get("balance_after", 0) or 0),
        "reason": b.get("reason", "") or "",
        "by_name": b.get("by_name", "") or "",
        "sale_id": b.get("sale_id", 0) or 0,
        "created_at": (b.get("created_at", "") or "")[:16],
    } for b in bonus_log]
    return web.json_response({
        "ok": True,
        "client": _client_json(c),
        "stats": stats,
        "sales": sales_out,
        "payments": pays_out,
        "bonus_log": bonus_out,
    })


@ROUTES.get("/api/clients/top")
async def api_clients_top(request: web.Request):
    """Top mijozlar reytingi. Query: ?days=30 (0 = barcha davr) &limit=50."""
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth or auth["role"] != "admin":
        return _err("Avtorizatsiya talab qilinadi", 401)
    try:
        days = max(0, int(request.query.get("days", "30")))
    except (TypeError, ValueError):
        days = 30
    try:
        limit = min(200, max(1, int(request.query.get("limit", "50"))))
    except (TypeError, ValueError):
        limit = 50
    since = ""
    if days:
        from datetime import timedelta
        from database._helpers import now_local
        since = (now_local() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = await _db.get_top_clients(since, limit)
    items = [{
        "id": r.get("id", 0),
        "shop_name": r.get("shop_name", ""),
        "phone": r.get("phone", ""),
        "client_type": r.get("client_type", "dona"),
        "revenue": float(r.get("revenue", 0) or 0),
        "checks": int(r.get("checks", 0) or 0),
        "avg_check": float(r.get("avg_check", 0) or 0),
        "debt_sum": float(r.get("debt", 0) or 0),
        "bonus_balance": float(r.get("bonus_balance", 0) or 0),
        "last_sale_at": (r.get("last_at", "") or "")[:16],
    } for r in rows]
    return web.json_response({"ok": True, "items": items, "days": days})


# ── Xodimlar boshqaruvi (admin paneli) ───────────────────────────────────────

def _staff_can_manage(auth: dict) -> bool:
    """Xodimlarni boshqarish huquqi: glavniy admin yoki 'full' rolli admin."""
    if auth.get("is_glavniy"):
        return True
    role = ((auth.get("user") or {}).get("role") or "full").strip().lower()
    return role == "full"


@ROUTES.get("/api/staff")
async def api_staff(request: web.Request):
    """Xodimlar ro'yxati: faol adminlar + kutilayotganlar (pending_staff)."""
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth or auth["role"] != "admin":
        return _err("Avtorizatsiya talab qilinadi", 401)
    admins = await _db.get_all_admins()
    pending = await _db.get_all_pending_staff()
    items = []
    for a in admins:
        items.append({
            "telegram_id": a.get("telegram_id", 0),
            "full_name": a.get("full_name", ""),
            "phone": a.get("phone", "") or "",
            "role": a.get("role", "full"),
            "username": a.get("username", "") or "",
            "created_at": (a.get("created_at", "") or "")[:16],
            "is_glavniy": 1 if a.get("telegram_id") == GLAVNIY_ADMIN_ID else 0,
            "pending": 0,
        })
    for p in pending:
        items.append({
            "pending_id": p.get("id", 0),
            "telegram_id": 0,
            "full_name": p.get("full_name", ""),
            "phone": p.get("phone", "") or "",
            "role": p.get("role", "full"),
            "username": "",
            "created_at": (p.get("created_at", "") or "")[:16],
            "is_glavniy": 0,
            "pending": 1,
        })
    return web.json_response({"ok": True, "items": items,
                              "can_manage": 1 if _staff_can_manage(auth) else 0})


@ROUTES.post("/api/staff/add")
async def api_staff_add(request: web.Request):
    """Yangi xodimni telefon raqami orqali qo'shish (bot oqimi bilan bir xil:
    pending_staff ga yoziladi, xodim botga kontakt yuborganda faollashadi).
    Body: {phone, full_name?, role?}."""
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth or auth["role"] != "admin":
        return _err("Avtorizatsiya talab qilinadi", 401)
    if not _staff_can_manage(auth):
        return _err("Xodim qo'shish faqat to'liq adminlar uchun", 403)
    body = await _read_json(request)
    phone = (body.get("phone") or "").strip()
    full_name = (body.get("full_name") or "").strip()[:80]
    role = (body.get("role") or "full").strip().lower()
    if role not in ("full", "products", "cashier", "stats"):
        role = "full"
    if len("".join(ch for ch in phone if ch.isdigit())) < 7:
        return _err("Telefon raqami noto'g'ri (kamida 7 raqam)")
    ok, why, admin = await _db.add_staff_by_phone(phone, full_name, role,
                                                  auth["tg_id"])
    if not ok:
        return _err(why or "Qo'shilmadi", 409)
    # `promoted` — raqam mijoz sifatida tanish edi, xodim DARROV faollashdi.
    return web.json_response({
        "ok": True,
        "promoted": 1 if why == "promoted" else 0,
        "name": (admin or {}).get("full_name", "") if admin else "",
    })


@ROUTES.post("/api/staff/role")
async def api_staff_role(request: web.Request):
    """Xodim rolini o'zgartirish. Body: {telegram_id, role}."""
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth or auth["role"] != "admin":
        return _err("Avtorizatsiya talab qilinadi", 401)
    if not _staff_can_manage(auth):
        return _err("Rol o'zgartirish faqat to'liq adminlar uchun", 403)
    body = await _read_json(request)
    try:
        tg_id = int(body.get("telegram_id") or 0)
    except (TypeError, ValueError):
        tg_id = 0
    role = (body.get("role") or "").strip().lower()
    if not tg_id or role not in ("full", "products", "cashier", "stats"):
        return _err("telegram_id va rol (full/products/cashier/stats) kerak")
    if tg_id == GLAVNIY_ADMIN_ID:
        return _err("Glavniy adminning roli o'zgartirilmaydi")
    a = await _db.get_admin(tg_id)
    if not a:
        return _err("Xodim topilmadi", 404)
    await _db.set_admin_role(tg_id, role)
    return web.json_response({"ok": True, "telegram_id": tg_id, "role": role})


@ROUTES.post("/api/staff/remove")
async def api_staff_remove(request: web.Request):
    """Xodimni o'chirish. Body: {telegram_id} (faol) yoki {pending_id}
    (hali botga kirmagan)."""
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth or auth["role"] != "admin":
        return _err("Avtorizatsiya talab qilinadi", 401)
    if not _staff_can_manage(auth):
        return _err("Xodim o'chirish faqat to'liq adminlar uchun", 403)
    body = await _read_json(request)
    try:
        pending_id = int(body.get("pending_id") or 0)
        tg_id = int(body.get("telegram_id") or 0)
    except (TypeError, ValueError):
        pending_id = tg_id = 0
    if pending_id:
        await _db.remove_pending_staff(pending_id)
        return web.json_response({"ok": True})
    if not tg_id:
        return _err("telegram_id yoki pending_id kerak")
    if tg_id == GLAVNIY_ADMIN_ID:
        return _err("Glavniy admin o'chirilmaydi")
    if tg_id == auth["tg_id"]:
        return _err("O'zingizni o'chira olmaysiz")
    await _db.remove_admin(tg_id)
    return web.json_response({"ok": True, "telegram_id": tg_id})


@ROUTES.get("/api/staff/stats")
async def api_staff_stats(request: web.Request):
    """Xodimlar KPI (CRM): davr ichida har bir kassir bo'yicha tushum, foyda,
    cheklar, o'rtacha chek, kunlik seriya. Query: ?days=30 (0 = barcha davr)."""
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth or auth["role"] != "admin":
        return _err("Avtorizatsiya talab qilinadi", 401)
    try:
        days = max(0, int(request.query.get("days", "30")))
    except (TypeError, ValueError):
        days = 30
    since = ""
    if days:
        from datetime import timedelta
        from database._helpers import now_local
        since = (now_local() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    items = await _db.get_staff_stats(since)
    return web.json_response({"ok": True, "items": items, "days": days})


@ROUTES.get("/api/orders")
async def api_orders(request: web.Request):
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth:
        return _err("Avtorizatsiya talab qilinadi", 401)
    if auth["role"] != "admin":
        return _err("Faqat adminlar uchun", 403)
    try:
        orders = await _db.get_recent_orders(limit=50)
    except Exception:
        orders = []
    items = []
    for o in orders:
        items.append({
            "id": o.get("id", 0),
            "client_id": o.get("client_id", 0),
            "shop_name": o.get("shop_name", ""),
            "phone": o.get("phone", ""),
            "total": float(o.get("total", 0) or 0),
            "status": o.get("status", ""),
            "note": o.get("note", ""),
            "created_at": (o.get("created_at", "") or "")[:16],
        })
    return web.json_response({"ok": True, "items": items, "total": len(items)})


@ROUTES.get("/api/admins")
async def api_admins(request: web.Request):
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth:
        return _err("Avtorizatsiya talab qilinadi", 401)
    if auth["role"] != "admin":
        return _err("Faqat adminlar uchun", 403)
    admins = await _db.get_all_admins()
    items = []
    for a in admins:
        items.append({
            "telegram_id": a.get("telegram_id", 0),
            "full_name": a.get("full_name", ""),
            "role": a.get("role", "full"),
            "username": a.get("username", ""),
            "created_at": (a.get("created_at", "") or "")[:16],
        })
    return web.json_response({"ok": True, "items": items, "total": len(items)})


@ROUTES.get("/api/settings")
async def api_settings(request: web.Request):
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth:
        return _err("Avtorizatsiya talab qilinadi", 401)
    if auth["role"] != "admin":
        return _err("Faqat adminlar uchun", 403)
    rate = _db.get_usd_rate()
    settings = {
        "usd_rate": rate,
        "wholesale_enabled": _db.is_wholesale_enabled(),
        "dona_enabled": _db.is_dona_enabled(),
        "barcode_enabled": _db.is_barcode_enabled(),
        "channel_enabled": _db.is_channel_enabled(),
        "client_orders_enabled": _db.is_client_orders_enabled(),
        "nasiya_enabled": _db.is_nasiya_enabled(),
        "categories_enabled": _db.is_categories_enabled(),
        "suppliers_enabled": _db.is_suppliers_enabled(),
        "mini_app_enabled": _db.is_mini_app_enabled(),
        "ai_consult_enabled": _db.is_ai_consult_enabled(),
        "ai_analytics_enabled": _db.is_ai_analytics_enabled(),
        "bonus_enabled": _db.is_bonus_enabled(),
        "bonus_percent": _db.get_bonus_percent(),
    }
    return web.json_response({"ok": True, "settings": settings})


@ROUTES.post("/api/settings")
async def api_settings_save(request: web.Request):
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth:
        return _err("Avtorizatsiya talab qilinadi", 401)
    if auth["role"] != "admin":
        return _err("Faqat adminlar uchun", 403)
    body = await _read_json(request)
    rate = body.get("usd_rate")
    if rate is not None:
        try:
            new_rate = float(rate)
            _db.set_usd_rate(new_rate)
            # Bot oqimi bilan bir xil: so'mdagi narxlar/qarz keshi USD (kanonik)
            # qiymatlardan yangi kursda qayta hisoblanadi. Busiz kurs faqat
            # yozilib, mavjud narxlar eski kursda qolib ketardi.
            if new_rate > 0:
                await _db.reprice_all_with_rate(new_rate)
        except (TypeError, ValueError):
            pass
    # Toggle settings
    toggle_map = {
        "wholesale_enabled": "set_wholesale_enabled",
        "dona_enabled": "set_dona_enabled",
        "barcode_enabled": "set_barcode_enabled",
        "channel_enabled": "set_channel_enabled",
        "client_orders_enabled": "set_client_orders_enabled",
        "nasiya_enabled": "set_nasiya_enabled",
        "categories_enabled": "set_categories_enabled",
        "suppliers_enabled": "set_suppliers_enabled",
        "mini_app_enabled": "set_mini_app_enabled",
        "ai_consult_enabled": "set_ai_consult_enabled",
        "ai_analytics_enabled": "set_ai_analytics_enabled",
        "bonus_enabled": "set_bonus_enabled",
    }
    for key, setter in toggle_map.items():
        if key in body:
            val = body[key]
            if isinstance(val, bool):
                getattr(_db, setter)(val)
            elif isinstance(val, str):
                getattr(_db, setter)(val.lower() in ("1", "true", "on"))
    # Bonus foizi (sotuvdan mijoz kartochkasiga o'tadigan ulush)
    if body.get("bonus_percent") is not None:
        try:
            _db.set_bonus_percent(float(body["bonus_percent"]))
        except (TypeError, ValueError):
            pass
    return web.json_response({"ok": True})


# ─── Static frontend serving ──────────────────────────────────────────────

# Mini App (frontend) endi alohida `app/` papkasida — repo ildizida, chinor-bot
# yonida. Shu sababli bu yerda 3 daraja yuqoriga chiqamiz (web_api.py →
# chinor-bot/bot → chinor-bot → repo_root) va `app` ga ulanamiz.
FRONTEND_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "app"
)
FRONTEND_DIR = os.path.realpath(FRONTEND_DIR)

# Desktop kassa avtomatik yangilanish fayllari (latest.yml, .exe, .blockmap)
UPDATES_DIR = os.path.realpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "updates"
))
os.makedirs(UPDATES_DIR, exist_ok=True)


async def updates_static(request: web.Request):
    """Desktop ilova electron-updater shu yerdan latest.yml va .exe oladi."""
    name = request.match_info.get("name", "")
    if not name or "/" in name or "\\" in name or ".." in name:
        raise web.HTTPNotFound()
    ext = os.path.splitext(name)[1].lower()
    if ext not in (".yml", ".exe", ".blockmap", ".zip"):
        raise web.HTTPNotFound()
    fpath = os.path.realpath(os.path.join(UPDATES_DIR, name))
    if not fpath.startswith(UPDATES_DIR) or not os.path.isfile(fpath):
        raise web.HTTPNotFound()
    return web.FileResponse(fpath)


# Admin ilova yangilanishlari — kassa feed'i bilan to'qnashmasligi uchun
# ALOHIDA papka (updates/admin/). Admin main.js macOS'da shu manzilni
# tekshiradi: <serverUrl>/updates/admin/latest-mac.yml
ADMIN_UPDATES_DIR = os.path.join(UPDATES_DIR, "admin")
os.makedirs(ADMIN_UPDATES_DIR, exist_ok=True)


async def admin_updates_static(request: web.Request):
    """Chinor admin ilovasi uchun yangilanish fayllari (updates/admin/)."""
    name = request.match_info.get("name", "")
    if not name or "/" in name or "\\" in name or ".." in name:
        raise web.HTTPNotFound()
    ext = os.path.splitext(name)[1].lower()
    if ext not in (".yml", ".exe", ".blockmap", ".zip"):
        raise web.HTTPNotFound()
    fpath = os.path.realpath(os.path.join(ADMIN_UPDATES_DIR, name))
    if not fpath.startswith(ADMIN_UPDATES_DIR) or not os.path.isfile(fpath):
        raise web.HTTPNotFound()
    return web.FileResponse(fpath)


async def frontend_index(request: web.Request):
    """Frontend index.html ni qaytaradi — Mini App shu yerda ishlaydi."""
    idx = os.path.join(FRONTEND_DIR, "index.html")
    if not os.path.isfile(idx):
        return _err("Frontend topilmadi", 404)
    return web.FileResponse(idx)


async def frontend_static(request: web.Request):
    """Frontend static fayllar: style.css, app.js, logo.png, favicon, etc.
    Faqat /api/ bilan boshlanmaydigan va ma'lum kengaytmali fayllarni qaytaradi."""
    name = request.match_info.get("filename", "")
    # /api/ so'rovlarini static serving orqali o'tkazmaymiz
    if not name or name.startswith("api/") or ".." in name or "/" in name:
        # Ehtimol bu API endpoint — 404 berish o'rniga handler topilmasin
        raise web.HTTPNotFound()
    # Faqat ma'lum kengaytmali fayllarni serving qilamiz
    ext = os.path.splitext(name)[1].lower()
    if ext not in (".html", ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".json", ".woff", ".woff2", ".ttf"):
        raise web.HTTPNotFound()
    fpath = os.path.realpath(os.path.join(FRONTEND_DIR, name))
    if not fpath.startswith(FRONTEND_DIR):
        raise web.HTTPNotFound()
    if not os.path.isfile(fpath):
        raise web.HTTPNotFound()
    return web.FileResponse(fpath)


# ─── App factory ────────────────────────────────────────────────────────────

def create_app() -> web.Application:
    """aiohttp app yaratadi. main.py shu yerdan oladi."""
    # iPhone rasmlari 25MB-100MB bo'lishi mumkin, limit'ni oshiramiz
    app = web.Application(
        middlewares=[cors_middleware],
        client_max_size=100*1024*1024  # 100MB limit
    )
    # API route'larini qo'shamiz
    app.add_routes(ROUTES)
    # Yetkazib beruvchilar (suppliers) — alohida modulda (web_api.py ni
    # kattalashtirmaslik uchun). Lazy import — circular importdan qochish.
    from bot import api_suppliers
    api_suppliers.setup(app)
    # Buyurtma ro'yxati (ketish tezligiga qarab) — hisob `marketplace`
    # paketida, bu yerda faqat API qatlami.
    from bot import api_reorder
    api_reorder.setup(app)
    # Savdo tarixi — YesPOS cheklaridan (marketplace.db), eski «Cheklar»
    # ekrani tegilmaydi.
    from bot import api_receipts
    api_receipts.setup(app)
    # Mijoz Mini App'i tavsiyalari — o'xshash tovarlar va «Siz uchun» lenta
    from bot import api_recommend
    api_recommend.setup(app)
    # «Ustalar maslahati» kartochkalari va bo'yoq kalkulyatori
    from bot import api_guides
    api_guides.setup(app)
    # Desktop yangilanish fayllari (kassa: /updates/, admin: /updates/admin/)
    app.router.add_get("/updates/admin/{name}", admin_updates_static)
    app.router.add_get("/updates/{name}", updates_static)
    # Frontend statik fayllar
    app.router.add_get("/", frontend_index)
    app.router.add_get("/{filename}", frontend_static)
    return app
