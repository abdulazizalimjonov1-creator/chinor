# -*- coding: utf-8 -*-
"""Instagram: izohlar, lichka (Direct) va story — bitta modulda.

Nega polling (so'rab turish), webhook emas?
    Meta webhook'lari faqat ilova «Опубликовано» holatida ishlaydi, bizniki
    esa hozircha dev rejimida. Shuning uchun asosiy yo'l — har bir necha
    daqiqada API'dan so'rash. Webhook kirish nuqtasi ham yozilgan
    (`setup(app)`): ilova nashr qilingan kuni sozlamaga URL qo'yiladi va
    xuddi shu ishlovchilar real vaqtda ishlay boshlaydi, kodni
    o'zgartirish shart emas.

Nima qiladi:
    • yangi izoh / lichka xabari kelsa — bosh adminga Telegram'ga yuboradi;
    • admin O'SHA xabarga reply qilsa — javob Instagram'ga ketadi;
    • «🤖 AI javob» tugmasi — javobni AI yozadi (admin tasdiqlaydi);
    • story: tayyor rolikni (yoki istalgan media havolasini) story qilib
      qo'yadi.

Chegaralar (Meta qoidasi, kod emas):
    • lichkaga BIRINCHI bo'lib yozib bo'lmaydi — faqat mijoz yozgandan
      keyin 24 soat ichida javob beramiz;
    • story'ga stiker/so'rovnoma/havola qo'yib bo'lmaydi (faqat ilovadan).
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

from aiohttp import web

logger = logging.getLogger(__name__)

# Nechada bir so'raymiz (soniya). Juda tez so'rash limitga urinadi.
POLL_SECONDS = int(os.getenv("IG_POLL_SECONDS", "180"))
# Har safar nechta oxirgi post tekshiriladi
MEDIA_LIMIT = int(os.getenv("IG_MEDIA_LIMIT", "8"))
# Lichkada javob berish oynasi (Meta qoidasi: 24 soat)
DM_WINDOW_HOURS = 24


# ─── Baza ──────────────────────────────────────────────────────────────────

def _conn():
    from database._helpers import DB_PATH
    c = sqlite3.connect(DB_PATH, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS ig_events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        kind        TEXT DEFAULT '',      -- comment | dm
        ig_user     TEXT DEFAULT '',      -- bizning profil id
        ext_id      TEXT DEFAULT '',      -- izoh/xabar id (Instagram)
        media_id    TEXT DEFAULT '',
        sender      TEXT DEFAULT '',      -- @nom yoki sender id
        sender_id   TEXT DEFAULT '',      -- lichkaga javob yozish uchun
        text        TEXT DEFAULT '',
        tg_msg      INTEGER DEFAULT 0,    -- adminga yuborilgan xabar id
        draft       TEXT DEFAULT '',      -- AI yozgan, admin tasdiqlamagan javob
        answered    INTEGER DEFAULT 0,
        created_at  TEXT DEFAULT (datetime('now','localtime'))
    )""")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ig_events_uq ON ig_events(kind, ext_id)")
    # Eski bazada ustun bo'lmasligi mumkin — jim qo'shamiz
    if "draft" not in {r["name"] for r in c.execute("PRAGMA table_info(ig_events)")}:
        c.execute("ALTER TABLE ig_events ADD COLUMN draft TEXT DEFAULT ''")
    c.commit()
    return c


def _seen(kind: str, ext_id: str) -> bool:
    c = _conn()
    try:
        return c.execute("SELECT 1 FROM ig_events WHERE kind=? AND ext_id=?",
                         (kind, str(ext_id))).fetchone() is not None
    finally:
        c.close()


def _add(**kw) -> int:
    cols = ("kind", "ig_user", "ext_id", "media_id", "sender", "sender_id", "text")
    c = _conn()
    try:
        cur = c.execute(
            "INSERT OR IGNORE INTO ig_events(%s) VALUES(%s)"
            % (",".join(cols), ",".join("?" * len(cols))),
            tuple(str(kw.get(k) or "") for k in cols))
        c.commit()
        # OR IGNORE o'tib ketsa lastrowid ESKI qatorniki bo'lib qolishi
        # mumkin — shuning uchun rowcount bo'yicha tekshiramiz.
        return (cur.lastrowid or 0) if cur.rowcount else 0
    finally:
        c.close()


def _set(eid: int, **kw) -> None:
    if not kw:
        return
    c = _conn()
    try:
        c.execute("UPDATE ig_events SET %s WHERE id=?" % ",".join(f"{k}=?" for k in kw),
                  tuple(kw.values()) + (eid,))
        c.commit()
    finally:
        c.close()


def get(eid: int) -> dict:
    c = _conn()
    try:
        r = c.execute("SELECT * FROM ig_events WHERE id=?", (eid,)).fetchone()
        return dict(r) if r else {}
    finally:
        c.close()


def by_tg_msg(msg_id: int) -> dict:
    """Admin javob yozgan xabar qaysi izoh/lichkaga tegishli."""
    c = _conn()
    try:
        r = c.execute("SELECT * FROM ig_events WHERE tg_msg=? ORDER BY id DESC LIMIT 1",
                      (int(msg_id),)).fetchone()
        return dict(r) if r else {}
    finally:
        c.close()


# ─── Instagram API (xatoning ASL matni bilan) ──────────────────────────────

def _graph() -> str:
    from bot import reels as R
    return R._graph()


def _call(method: str, path: str, params: dict) -> tuple:
    """(javob, xato_matni). Meta xatosi yutilmaydi — matni qaytariladi."""
    url = f"{_graph()}/{path.lstrip('/')}"
    data = urllib.parse.urlencode(params).encode()
    try:
        if method == "GET":
            req = urllib.request.Request(url + "?" + data.decode())
        else:
            req = urllib.request.Request(url, data)
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode()), ""
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            msg = json.loads(body).get("error", {}).get("message", body)
        except Exception:
            msg = body
        return {}, f"{e.code}: {msg}"[:300]
    except Exception as e:
        return {}, str(e)[:300]


def accounts() -> list:
    """[(ig_id, token)] — ULANGAN BARCHA profillar.

    `reels_ig_only` sozlamasi ataylab hisobga olinmaydi: u rolik QAYSI
    profilga chiqishini tanlaydi, izoh va lichka esa ikkala profilga ham
    keladi. 5 mingdan ortiq obunachili @chinors_market ga yozilgan savol
    javobsiz qolmasligi kerak."""
    from bot import reels as R
    out = []
    uid = R._cfg("IG_USER_ID")
    if uid and R.ig_token():
        out.append((uid, R.ig_token()))
    for n in (2, 3):
        u = (os.getenv(f"IG{n}_USER_ID") or "").strip()
        t = (os.getenv(f"IG{n}_ACCESS_TOKEN") or "").strip()
        if u and t:
            out.append((u, R.ig_token(u, t)))
    return out


def post_accounts() -> list:
    """[(ig_id, token)] — rolik/story CHIQADIGAN profillar (reels_ig_only)."""
    from bot import reels as R
    out = []
    for ig_id, base in R.ig_accounts():
        birinchi = ig_id == (R._cfg("IG_USER_ID") or "")
        out.append((ig_id, R.ig_token() if birinchi else R.ig_token(ig_id, base)))
    return out


# ─── Story ─────────────────────────────────────────────────────────────────

def publish_story(media_url: str, ig_id: str = "", token: str = "",
                  is_video: bool = True) -> tuple:
    """Story qo'yadi → (media_id, xato). Instagram faylni O'ZI yuklab oladi,
    shuning uchun `media_url` ochiq HTTPS bo'lishi shart."""
    if not ig_id or not token:
        accs = post_accounts()      # story ham post — tanlangan profilga
        if not accs:
            return "", "Instagram sozlanmagan"
        ig_id, token = accs[0]
    params = {"media_type": "STORIES", "access_token": token}
    params["video_url" if is_video else "image_url"] = media_url
    cr, err = _call("POST", f"{ig_id}/media", params)
    cid = cr.get("id")
    if not cid:
        return "", err or f"story yaratilmadi: {cr}"
    # Video qayta ishlanishini kutamiz (rasm uchun ham zarari yo'q)
    for _ in range(30):
        time.sleep(5)
        st, _e = _call("GET", cid, {"fields": "status_code", "access_token": token})
        code = st.get("status_code", "")
        if code == "FINISHED":
            break
        if code == "ERROR":
            return "", f"Instagram qayta ishlay olmadi: {st}"
    else:
        return "", "Instagram juda uzoq qayta ishladi"
    pub, err = _call("POST", f"{ig_id}/media_publish",
                     {"creation_id": cid, "access_token": token})
    mid = pub.get("id")
    return (mid, "") if mid else ("", err or f"story joylanmadi: {pub}")


async def maybe_story(db, video_url: str) -> str:
    """`ig_story_auto` yoqilgan bo'lsa, yangi rolikni story qilib ham qo'yadi.
    Rolik lentada ko'rinmaganlar story orqali ko'radi — bir xil kontent,
    ikki marta ko'rsatiladi, qo'shimcha ish talab qilmaydi."""
    from bot import reels as R
    if not R._flag_on(db, "ig_story_auto", "0") or not R.ig_configured():
        return ""
    loop = asyncio.get_event_loop()
    mid, err = await loop.run_in_executor(None, lambda: publish_story(video_url))
    if mid:
        logger.info("📸 Story joylandi: %s", mid)
    else:
        logger.warning("📸 Story joylanmadi: %s", err)
    return err


# ─── Izoh va lichkaga javob ────────────────────────────────────────────────

def reply_comment(comment_id: str, text: str, token: str) -> str:
    _, err = _call("POST", f"{comment_id}/replies",
                   {"message": text[:1000], "access_token": token})
    return err


def hide_comment(comment_id: str, token: str, hide: bool = True) -> str:
    _, err = _call("POST", comment_id,
                   {"hide": "true" if hide else "false", "access_token": token})
    return err


def send_dm(ig_id: str, recipient_id: str, text: str, token: str) -> str:
    _, err = _call("POST", f"{ig_id}/messages", {
        "recipient": json.dumps({"id": recipient_id}),
        "message": json.dumps({"text": text[:900]}),
        "access_token": token,
    })
    return err


async def answer(db, eid: int, text: str) -> str:
    """Admin yozgan (yoki AI yozgan) javobni Instagram'ga yuboradi."""
    ev = get(eid)
    if not ev:
        return "Yozuv topilmadi"
    token = dict(accounts()).get(ev["ig_user"], "")
    if not token:
        return "Bu profil uchun token yo'q"
    loop = asyncio.get_event_loop()
    if ev["kind"] == "comment":
        err = await loop.run_in_executor(
            None, lambda: reply_comment(ev["ext_id"], text, token))
    else:
        err = await loop.run_in_executor(
            None, lambda: send_dm(ev["ig_user"], ev["sender_id"], text, token))
    if not err:
        _set(eid, answered=1)
    return err


# ─── AI javob ──────────────────────────────────────────────────────────────

_AI_SYSTEM = (
    "Sen Chinor do'konining Instagram sahifasida ishlaydigan sotuvchisan. "
    "Do'kon: qurilish mollari va uy ro'zg'or buyumlari, Toshkent, Sergeli. "
    "Qisqa (1-2 gap), samimiy, o'zbek tilida javob ber. "
    "NARX yoki QOLDIQ haqida aniq raqam SO'RALSA va sen bilmasang — "
    "«hozir aniqlab, yozamiz» deb javob ber, raqam TO'QIMA. "
    "Emoji ko'pi bilan bitta. Reklama shiorlari yozma."
)


def ai_reply(text: str, kind: str) -> str:
    """Izoh/xabarga AI javobi. Bo'sh qaytsa — AI ishlamadi."""
    from bot import ai_engine
    joy = "izoh" if kind == "comment" else "shaxsiy xabar"
    try:
        out = ai_engine.chat(
            f"Instagram'dagi {joy}: «{text}»\n\nShunga javob yoz.",
            system=_AI_SYSTEM,
            validate=lambda s: 0 < len(s.strip()) <= 400)
        return (out or "").strip()
    except Exception as e:
        logger.warning("🤖 IG AI javobi chiqmadi: %s", e)
        return ""


# ─── Adminga xabar ─────────────────────────────────────────────────────────

async def _notify(db, eid: int, ev: dict) -> None:
    bot = getattr(db, "_bot", None)
    if not bot:
        return
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from bot.config import GLAVNIY_ADMIN_ID
    bosh = "💬 <b>Instagram izohi</b>" if ev["kind"] == "comment" else "✉️ <b>Instagram lichkasi</b>"
    kim = ev.get("sender") or ev.get("sender_id") or "—"
    rows = [[InlineKeyboardButton(text="🤖 AI javob", callback_data=f"igs_ai_{eid}")]]
    if ev["kind"] == "comment":
        rows[0].append(InlineKeyboardButton(text="🙈 Yashirish", callback_data=f"igs_hide_{eid}"))
    txt = (f"{bosh}\n\n👤 {kim}\n\n«{(ev.get('text') or '')[:600]}»\n\n"
           f"✍️ Javob berish uchun SHU xabarga <b>reply</b> qilib yozing.")
    try:
        msg = await bot.send_message(GLAVNIY_ADMIN_ID, txt, parse_mode="HTML",
                                     reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        _set(eid, tg_msg=getattr(msg, "message_id", 0))
    except Exception as e:
        logger.warning("📩 IG xabari adminga ketmadi: %s", e)


# ─── Yangi izoh va xabarlarni yig'ish ──────────────────────────────────────

def username(token: str) -> str:
    me, _err = _call("GET", "me", {"fields": "username", "access_token": token})
    return str(me.get("username") or "")


def _fetch_comments(ig_id: str, token: str) -> list:
    """[(izoh, media_id)] — oxirgi postlardagi barcha izohlar."""
    out = []
    media, err = _call("GET", "me/media", {
        "fields": "id,comments_count", "limit": MEDIA_LIMIT, "access_token": token})
    if err:
        logger.warning("💬 IG postlari o'qilmadi (%s): %s", ig_id, err)
        return out
    for m in media.get("data") or []:
        if not int(m.get("comments_count") or 0):
            continue
        cs, err = _call("GET", f"{m['id']}/comments", {
            "fields": "id,text,username,timestamp", "access_token": token})
        if err:
            logger.warning("💬 Izohlar o'qilmadi: %s", err)
            continue
        for cm in cs.get("data") or []:
            out.append((cm, m["id"]))
    return out


def _fetch_dms(ig_id: str, token: str) -> list:
    """Mijoz yozgan (bizdan kelmagan) oxirgi xabarlar."""
    out = []
    conv, err = _call("GET", "me/conversations", {
        "platform": "instagram",
        "fields": "id,messages.limit(10){id,from,message,created_time}",
        "access_token": token})
    if err:
        logger.warning("✉️ IG suhbatlari o'qilmadi (%s): %s", ig_id, err)
        return out
    for c in conv.get("data") or []:
        for m in ((c.get("messages") or {}).get("data") or []):
            frm = (m.get("from") or {})
            if str(frm.get("id") or "") == str(ig_id):
                continue                      # o'zimiz yozgan xabar
            if not (m.get("message") or "").strip():
                continue                      # rasm/stiker — matn yo'q
            out.append((m, frm))
    return out


async def poll_once(db) -> dict:
    """Bir marta so'rab, yangilarini adminga yuboradi. {comments, dms}."""
    from bot import reels as R
    loop = asyncio.get_event_loop()
    yangi = {"comments": 0, "dms": 0}
    for ig_id, token in accounts():
        if not token:
            continue
        biz_nomi = await loop.run_in_executor(None, lambda t=token: username(t))
        # ── Izohlar ──
        for cm, media_id in await loop.run_in_executor(None, lambda i=ig_id, t=token: _fetch_comments(i, t)):
            cid = str(cm.get("id") or "")
            if not cid or _seen("comment", cid):
                continue
            # O'zimizning javobimiz yana izoh bo'lib qaytmasin
            if biz_nomi and str(cm.get("username") or "") == biz_nomi:
                _add(kind="comment", ig_user=ig_id, ext_id=cid, media_id=media_id,
                     sender="(biz)", text=cm.get("text"))
                continue
            eid = _add(kind="comment", ig_user=ig_id, ext_id=cid, media_id=media_id,
                       sender="@" + str(cm.get("username") or "?"),
                       text=cm.get("text"))
            if not eid:
                continue
            yangi["comments"] += 1
            ev = get(eid)
            if R._flag_on(db, "ig_comments_auto", "0"):
                javob = await loop.run_in_executor(None, lambda t=ev["text"]: ai_reply(t, "comment"))
                if javob:
                    err = await answer(db, eid, javob)
                    await _notify_auto(db, ev, javob, err)
                    continue
            await _notify(db, eid, ev)
        # ── Lichka ──
        for m, frm in await loop.run_in_executor(None, lambda i=ig_id, t=token: _fetch_dms(i, t)):
            mid = str(m.get("id") or "")
            if not mid or _seen("dm", mid):
                continue
            eid = _add(kind="dm", ig_user=ig_id, ext_id=mid,
                       sender="@" + str(frm.get("username") or frm.get("id") or "?"),
                       sender_id=str(frm.get("id") or ""), text=m.get("message"))
            if not eid:
                continue
            yangi["dms"] += 1
            ev = get(eid)
            if R._flag_on(db, "ig_dm_auto", "0"):
                javob = await loop.run_in_executor(None, lambda t=ev["text"]: ai_reply(t, "dm"))
                if javob:
                    err = await answer(db, eid, javob)
                    await _notify_auto(db, ev, javob, err)
                    continue
            await _notify(db, eid, ev)
    return yangi


async def _notify_auto(db, ev: dict, javob: str, err: str) -> None:
    """Avtomatik javob berilgach — adminni xabardor qilamiz (nazorat uchun)."""
    bot = getattr(db, "_bot", None)
    if not bot:
        return
    from bot.config import GLAVNIY_ADMIN_ID
    holat = "✅ yuborildi" if not err else f"❌ {err[:120]}"
    try:
        await bot.send_message(
            GLAVNIY_ADMIN_ID,
            f"🤖 <b>Avtomatik javob</b> ({holat})\n\n"
            f"👤 {ev.get('sender')}\n«{(ev.get('text') or '')[:300]}»\n\n"
            f"➜ {javob[:500]}", parse_mode="HTML")
    except Exception:
        pass


async def loop_forever():
    """Fon ishchisi — main.py shuni ishga tushiradi."""
    from database.channel_db import db as _db
    from bot import reels as R
    await asyncio.sleep(90)              # bot to'liq ko'tarilsin
    while True:
        try:
            if R.ig_configured() and str(_db.get_setting("ig_social_enabled", "1")).strip() in ("1", "true", "on"):
                res = await poll_once(_db)
                if res["comments"] or res["dms"]:
                    logger.info("📩 Instagram: %d izoh, %d lichka", res["comments"], res["dms"])
        except Exception:
            logger.exception("📩 Instagram kuzatuvi xatosi")
        await asyncio.sleep(POLL_SECONDS)


# ─── Webhook (ilova «Опубликовано» bo'lgan kuni ishlaydi) ──────────────────

def _verify_token() -> str:
    from bot import reels as R
    return R._cfg("IG_VERIFY_TOKEN") or "chinor"


def _app_secret() -> str:
    from bot import reels as R
    return R._cfg("IG_APP_SECRET")


async def webhook_verify(request: web.Request):
    """Meta sozlashda GET bilan kelib, hub.challenge ni qaytarishni so'raydi."""
    q = request.query
    if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == _verify_token():
        return web.Response(text=q.get("hub.challenge", ""))
    raise web.HTTPForbidden()


async def webhook_event(request: web.Request):
    """Real vaqtdagi hodisa. Imzo tekshiriladi — soxta so'rov o'tmaydi."""
    raw = await request.read()
    secret = _app_secret()
    if secret:
        sig = (request.headers.get("X-Hub-Signature-256") or "").replace("sha256=", "")
        want = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, want):
            logger.warning("📩 IG webhook: imzo to'g'ri kelmadi")
            raise web.HTTPForbidden()
    # Hodisani o'qiymiz, lekin ishni polling bilan bir xil yo'ldan qilamiz:
    # darhol bir marta so'raymiz — shunda ikki yo'l bir xil natija beradi.
    from database.channel_db import db as _db
    asyncio.get_event_loop().create_task(poll_once(_db))
    return web.Response(text="OK")


def setup(app: web.Application) -> None:
    app.router.add_get("/api/ig/webhook", webhook_verify)
    app.router.add_post("/api/ig/webhook", webhook_event)
