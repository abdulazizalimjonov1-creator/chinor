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
from bot.config import BOT_TOKEN, CORS_ALLOW_ORIGIN, GLAVNIY_ADMIN_ID, CHANNEL_ID

logger = logging.getLogger(__name__)

ROUTES = web.RouteTableDef()


async def _gemini_describe_image(fpath: str) -> str:
    """Rasmni AI orqali tahlil qilib o'zbek tilida tavsif qaytaradi.
    Avval Groq (tez, bepul), keyin Gemini (zaxira)."""
    prompt = ("Bu tovar rasmi. O'zbek tilida 1-2 jumlada qisqa va jozibali tavsif yoz. "
              "Faqat tovar haqida yoz, narx yoki do'kon haqida yozma.")
    with open(fpath, "rb") as f:
        img_bytes = f.read()
    import base64
    img_b64 = base64.b64encode(img_bytes).decode()

    # 1) Groq vision (meta-llama/llama-4-scout)
    try:
        from bot.config import GROQ_API_KEY
        if GROQ_API_KEY:
            from groq import Groq
            client = Groq(api_key=GROQ_API_KEY)
            resp = await asyncio.get_event_loop().run_in_executor(None, lambda:
                client.chat.completions.create(
                    model="meta-llama/llama-4-scout-17b-16e-instruct",
                    messages=[{"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                        {"type": "text", "text": prompt}
                    ]}],
                    max_tokens=200,
                )
            )
            desc = (resp.choices[0].message.content or "").strip()
            if desc:
                logger.info(f"🤖 Groq tavsif: {desc[:80]}...")
                return desc
    except Exception as e:
        logger.warning(f"🤖 Groq xato: {e}")

    # 2) Gemini (zaxira)
    try:
        from bot.config import GEMINI_API_KEY, GEMINI_MODEL
        if GEMINI_API_KEY:
            import importlib
            genai = importlib.import_module("google.genai")
            client = genai.Client(api_key=GEMINI_API_KEY)
            Part = genai.types.Part
            response = await asyncio.get_event_loop().run_in_executor(None, lambda:
                client.models.generate_content(
                    model=GEMINI_MODEL or "gemini-2.0-flash",
                    contents=[
                        Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
                        prompt
                    ]
                )
            )
            desc = (response.text or "").strip()
            if desc:
                logger.info(f"🤖 Gemini tavsif: {desc[:80]}...")
                return desc
    except Exception as e:
        logger.warning(f"🤖 Gemini xato: {e}")

    return ""


async def _post_product_to_channel(db, p: dict, pid: int):
    """Mahsulot saqlanganidan keyin kanalga post yuboradi.
    Rasm bo'lmasa — yuklamaydi. Gemini bilan qisqa tavsif yozadi."""
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
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            image_url.lstrip("/").replace("api/img/", "uploads/")
        )
        if not os.path.exists(fpath):
            logger.warning(f"📢 Rasm fayli topilmadi: {fpath}")
            return

        # Gemini bilan rasm tahlili
        ai_desc = await _gemini_describe_image(fpath)

        from database._formatters import _fmt_product
        cap = _fmt_product(p, ai_desc=ai_desc)
        markup = db._buy_markup(pid)

        from aiogram.types import FSInputFile
        # Eski kanal postini o'chirib, yangi yuboramiz
        old_mid = p.get("channel_msg_id", 0)
        if old_mid:
            try:
                await bot.delete_message(CHANNEL_ID, old_mid)
            except Exception:
                pass

        msg = await bot.send_photo(
            CHANNEL_ID,
            FSInputFile(fpath),
            caption=cap,
            parse_mode="HTML",
            reply_markup=markup,
        )
        tg_file_id = msg.photo[-1].file_id
        mid = msg.message_id
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
                        "UPDATE products SET image_file_id=?, channel_msg_id=?, description=? WHERE id=?",
                        (tg_file_id, mid, ai_desc, pid)
                    )
                else:
                    conn.execute(
                        "UPDATE products SET image_file_id=?, channel_msg_id=? WHERE id=?",
                        (tg_file_id, mid, pid)
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
    """Token haqiqiy va muddati o'tmagan bo'lsa auth dict qaytaradi."""
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
        return _json.loads(row["data"])
    finally:
        conn.close()

# Mini App orqali yuklangan mahsulot rasmlari shu papkada saqlanadi
UPLOAD_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads"
)
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
    
    # /api/img/* endpoint'lar uchun public resource — CORS permissive
    req_path = request.path if request else ""
    if req_path.startswith("/api/img/"):
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


def _product_json(p: dict, role: str, ctype: str) -> dict:
    usd, summ = _price_pair(p, ctype)
    out = {
        "id": p["id"],
        "name": p.get("name", ""),
        "description": p.get("description", ""),
        "qty": float(p.get("qty", 0) or 0),
        "unit": p.get("unit", "dona"),
        "barcode": p.get("barcode", "") or "",
        "price_usd": usd,
        "price_sum": summ,
        "image_url": p.get("image_url", "") or "",
        "has_image": bool(p.get("image_file_id") or p.get("image_url")),
    }
    if role == "admin":
        # Admin'ga tannarx va optom ham ko'rinadi
        out["cost_price_sum"] = float(p.get("cost_price", 0) or 0)
        out["cost_price_usd"] = float(p.get("cost_price_usd", 0) or 0)
        out["wholesale_sum"] = float(p.get("wholesale_price", 0) or 0)
        out["wholesale_usd"] = float(p.get("wholesale_price_usd", 0) or 0)
        out["sell_price_sum"] = float(p.get("sell_price", 0) or 0)
        out["sell_price_usd"] = float(p.get("sell_price_usd", 0) or 0)
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
    """Klientlar uchun: ko'p sotilgan va yangi tovarlar."""
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth:
        return _err("Avtorizatsiya talab qilinadi", 401)
    try:
        top = await _db.top_selling_products(limit=12)
    except Exception:
        top = []
    # Yangi tovarlar — so'nggi 30 kun ichida qo'shilganlar
    try:
        all_prods = await _db.get_all_products(active_only=True)
        from database._helpers import _now
        import time as _time
        cutoff = _time.time() - 30 * 86400
        new_prods = []
        for p in all_prods:
            try:
                import datetime
                dt = datetime.datetime.fromisoformat(p["created_at"].replace("Z",""))
                if dt.timestamp() > cutoff and p.get("image_url"):
                    new_prods.append(p)
            except Exception:
                pass
        new_prods = new_prods[-12:]
        new_prods.reverse()
    except Exception:
        new_prods = []
    def _clip(lst):
        return [_product_json(p, "client", "dona") for p in lst]
    return web.json_response({"ok": True, "top": _clip(top), "new": _clip(new_prods)})


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
    page_size = 20

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
    return web.json_response({"ok": True, "product": _product_json(p, role, ctype)})


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
            pid, name=name, description=desc,
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
    asyncio.ensure_future(_post_product_to_channel(_db, p or {}, new_id))

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
            upd = {}
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
            "total_qty, total_cost, total_sell, items_json, source) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (created_at, emp, note, len(saved), total_qty, total_cost, total_sell,
             _json.dumps(saved, ensure_ascii=False), source)
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

    # Kengaytmani aniqlaymiz (iPhone HEIC ham bo'lishi mumkin)
    fn = (fname_in or "photo.jpg").lower()
    file_ext = ".jpg"
    for e in (".png", ".jpeg", ".jpg", ".webp", ".gif", ".heic", ".heif"):
        if fn.endswith(e):
            file_ext = ".jpg" if e == ".jpeg" else e
            break

    fname = f"prod_{pid or 'new'}_{int(time.time())}{file_ext}"
    fpath = os.path.join(UPLOAD_DIR, fname)
    try:
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        with open(fpath, "wb") as f:
            f.write(file_bytes)
        logger.info(f"📸 Rasm saqland'i: {fpath}")
    except Exception as e:
        logger.error(f"📸 Saqlanmadi: {e}")
        return _err(f"Saqlanmadi: {e}", 500)

    url = f"/api/img/{fname}"
    if pid:
        try:
            await _db.update_product(pid, image_url=url, image_file_id="")
            logger.info(f"📸 Database'ga saqland'i: pid={pid}, url={url}")
        except Exception as e:
            logger.error(f"📸 Bazaga yozilmadi: {e}")
            return _err(f"Bazaga yozilmadi: {e}", 500)
    else:
        logger.warning("📸 pid=0, rasm saqland'i lekin database'ga yozilmadi")
    logger.info(f"📸 Yuklash muvaffaqiyatli: {url}")
    return web.json_response({"ok": True, "image_url": url})


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

    return web.json_response({
        "ok": True,
        "rate": rate,
        "today": block(st_today),
        "month": block(st_month),
        "month_label": now_local().strftime("%Y-%m"),
        "top_products": top_list,
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
    order_list = [{
        "id": o["id"],
        "total": float(o.get("total", 0) or 0),
        "status": o.get("status", ""),
        "created_at": (o.get("created_at", "") or "")[:16],
    } for o in (orders or [])[:15]]

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
    if payment not in ("cash", "card"):
        payment = "cash"
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
        price_usd = float(p.get("sell_price_usd", 0) or 0)
        if price_usd <= 0:
            price_usd = round(sum_to_usd(float(p.get("sell_price", 0) or 0), rate), 4)
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

    try:
        kw = {}
        if is_internal:
            # «Chinor» ichki rasxod — to'lov/chegirma yo'q, narx tannarxda
            kw = {"is_internal": True, "client_id": client_id}
        elif is_nasiya:
            # Qarzga — to'liq summa mijoz qarziga yoziladi
            kw = {"is_nasiya": True, "client_id": client_id}
        else:
            if payment == "cash":
                kw["paid_cash"] = eff_sum
            else:
                kw["paid_card"] = eff_sum
            if client_id:
                kw["client_id"] = client_id  # chek mijozga Telegram orqali ketadi
        sale = await _db.create_sale(
            auth["tg_id"],
            auth["user"].get("full_name") or "Mini App",
            sale_items,
            paid_currency="sum",
            override_total_sum=(0 if is_internal else override_sum),
            source="miniapp",
            **kw,
        )
    except Exception as e:
        return _err(f"Sotuvni saqlashda xato: {e}", 500)

    return web.json_response({
        "ok": True,
        "sale_id": sale["id"],
        "total_sum": float(sale.get("total", 0) or 0),
        "items_count": len(sale_items),
        "change_sum": float(sale.get("change", 0) or 0),
        "is_nasiya": bool(sale.get("is_nasiya")),
        "is_internal": bool(sale.get("is_internal")),
    })


# ─── Desktop kassa (offline) sinxronizatsiya ───────────────────────────────
# Windows kassa ilovasi internetsiz ishlaydi, internet kelganda shu
# endpoint'lar orqali sotuvlarni serverga yuboradi va katalogni yangilaydi.

_DESKTOP_SESSION_TTL = 24 * 3600  # 24 soat — kassa kun bo'yi ochiq turadi


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
    return web.json_response({
        "ok": True,
        "session_token": token,
        "cashier": {
            "id": tg_id,
            "name": user.get("full_name") or user.get("username") or login_in,
        },
        "usd_rate": _db.get_usd_rate(),
    })


@ROUTES.get("/api/sync/catalog")
async def api_sync_catalog(request: web.Request):
    """Offline kesh uchun barcha aktiv mahsulotlar + USD kurs."""
    from database.channel_db import db as _db
    auth = await _authenticate(request, _db)
    if not auth or auth["role"] != "admin":
        return _err("Avtorizatsiya talab qilinadi", 401)

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
            if payment not in ("cash", "card", "click", "other"):
                payment = "cash"
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
        if s.get("is_return"):
            pay = "qaytarish"
        elif s.get("is_internal"):
            pay = "rasxod"
        elif s.get("is_nasiya"):
            pay = "qarz"
        elif float(s.get("paid_card", 0) or 0) > 0:
            pay = "card"
        elif float(s.get("paid_other", 0) or 0) > 0:
            pay = "click"
        else:
            pay = "cash"
        items = [{
            "name": it.get("name", ""),
            "qty": it.get("qty", 0),
            "price_sum": float(it.get("price", 0) or 0),
            "product_id": it.get("product_id", 0),
        } for it in (s.get("items") or [])]
        # Qaytarish bo'lsa — asl chek raqamini source ichidan ajratamiz
        src = s.get("source", "") or ""
        orig_rno = src.split("qaytarish←")[1].split(" ")[0] if "qaytarish←" in src else ""
        out.append({
            "id": s.get("id"),
            "receipt_no": s.get("receipt_no", "") or "",
            "created_at": s.get("created_at", ""),
            "cashier_name": s.get("cashier_name", "") or "",
            "source": s.get("source", "") or "",
            "total_sum": float(s.get("total", 0) or 0),
            "subtotal_sum": float(s.get("subtotal", 0) or 0),
            "discount_sum": float(s.get("discount", 0) or 0),
            "payment": pay,
            "client_name": s.get("client_name", "") or "",
            "client_id": int(s.get("client_id", 0) or 0),
            "is_internal": 1 if s.get("is_internal") else 0,
            "is_nasiya": 1 if s.get("is_nasiya") else 0,
            "is_return": 1 if s.get("is_return") else 0,
            "orig_receipt_no": orig_rno,
            "items": items,
        })
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
            "created_at": (c.get("created_at", "") or "")[:16],
        })
    return web.json_response({"ok": True, "items": items, "total": len(items)})


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
            _db.set_usd_rate(float(rate))
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
    }
    for key, setter in toggle_map.items():
        if key in body:
            val = body[key]
            if isinstance(val, bool):
                getattr(_db, setter)(val)
            elif isinstance(val, str):
                getattr(_db, setter)(val.lower() in ("1", "true", "on"))
    return web.json_response({"ok": True})


# ─── Static frontend serving ──────────────────────────────────────────────

FRONTEND_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fronted"
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
    # Desktop yangilanish fayllari
    app.router.add_get("/updates/{name}", updates_static)
    # Frontend statik fayllar
    app.router.add_get("/", frontend_index)
    app.router.add_get("/{filename}", frontend_static)
    return app
