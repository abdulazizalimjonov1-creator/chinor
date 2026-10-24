# -*- coding: utf-8 -*-
"""G'oya → rolik. Egasi Telegram'da ovozli (yoki matnli) g'oya aytadi,
tizim uni tushunib, kerakli dvigatelga uzatadi.

Misollar (ovozli):
    «bugun Arielni reklama qil, xususiyatlari haqida gapir»   → reklama roliki
    «Ariel bilan Tide ni solishtir»                            → taqqoslash
    «Ariel bilan Barf gaplashayotgan qilib ishla»              → suhbat roliki

Nega alohida modul: uchta dvigatel (reels / reels_compare / reels_talk)
allaqachon bor edi, lekin ularning har biri mavzuni O'ZI tanlardi. Bu yerda
faqat bitta ish qilinadi — odamning gapini «qaysi dvigatel + qaysi tovar»
ga aylantirish.
"""

import asyncio
import json
import logging
import os
import re
import time

logger = logging.getLogger(__name__)

TYPES = ("ad", "compare", "talk")

# Gapdagi «bo'sh» so'zlar — tovar nomini izlashda e'tiborga olinmaydi
_STOP = {
    "bugun", "ertaga", "video", "rolik", "reels", "qil", "qilib", "qilasan",
    "ishla", "ishlab", "chiqar", "chiqarasan", "haqida", "gapir", "gapirib",
    "reklama", "reklamasini", "solishtir", "solishtirib", "taqqosla", "bilan",
    "uchun", "ham", "yoki", "va", "bir", "ikkita", "ikki", "tovar", "mahsulot",
    "suhbat", "suhbatlashayotgan", "gaplashayotgan", "qilgin", "qiling",
    "yax", "yaxshi", "zor", "keyin", "endi", "mana", "shu", "shuni", "uni",
    "xususiyatlari", "xususiyati", "foydasi", "narxi", "menga", "sen", "san",
}


def _words(text: str) -> list:
    return [w for w in re.split(r"[^\w'’]+", (text or "").lower())
            if len(w) >= 3 and w not in _STOP]


# ─── Tovarni nomidan topish ────────────────────────────────────────────────

async def find_products(db, name: str, limit: int = 5) -> list:
    """Nomga mos tovarlar (qoldiq va rasm borlari oldinda)."""
    from bot import api_recommend as R
    idx = await R._index(db)
    want = set(_words(name))
    if not want:
        return []
    scored = []
    for pid, p in idx.items.items():
        toks = idx.toks.get(pid, set())
        # Moslik IKKI TOMONLAMA bo'lishi shart: odam «aquafresh» deydi,
        # bazada esa «Aqua fresh …»; «pastasi» deydi, bazada «pasta».
        # Faqat bir tomonga qaralganda «tish pastasi» so'rovi «tishli disk»
        # ni topib kelardi. Og'irlik so'z UZUNLIGI bo'yicha: uzun so'z
        # («aquafresh») qisqasidan («tish») ko'ra ishonchliroq belgi.
        hit = 0
        for w in want:
            for t in toks:
                if (t == w or (len(w) >= 4 and t.startswith(w))
                        or (len(t) >= 4 and w.startswith(t))):
                    hit += len(w)
                    break
        if not hit:
            continue
        from bot import reels_compare as C
        scored.append((
            hit,
            1 if float(p.get("qty") or 0) > 0 else 0,
            1 if C._img_path(p) else 0,
            p,
        ))
    scored.sort(key=lambda s: (-s[0], -s[1], -s[2]))
    return [s[3] for s in scored[:limit]]


async def _partner_for(db, pid: int) -> dict:
    """Taqqoslash/suhbat uchun juftlik — o'sha bo'limdan eng mosi."""
    from bot import api_recommend as R
    from bot import reels_compare as C
    from bot.api_guides import _family
    idx = await R._index(db)
    me = idx.items.get(int(pid))
    if not me:
        return {}
    cid = int(me.get("category_id") or 0)
    narx = float(me.get("sell_price") or 0)
    best, best_s = None, -1e9
    for other in idx.items.values():
        if other["id"] == me["id"] or float(other.get("qty") or 0) <= 0:
            continue
        if _family(other.get("name", "")) == _family(me.get("name", "")):
            continue
        bir_bolim = cid and int(other.get("category_id") or 0) == cid
        umumiy = len(idx.toks.get(me["id"], set()) & idx.toks.get(other["id"], set()))
        if not bir_bolim and umumiy < 2:
            continue
        p2 = float(other.get("sell_price") or 0)
        if p2 <= 0:
            continue
        # Rasmi va yorlig'i borlari afzal, narx farqi sezilarli bo'lsin
        s = (2 if C._img_path(other) else 0) + (1.5 if bir_bolim else 0) + umumiy * 0.3
        if narx > 0:
            s += min(abs(p2 - narx) / max(narx, 1), 1.5)
        if s > best_s:
            best, best_s = other, s
    return best or {}


# ─── G'oyani tushunish ─────────────────────────────────────────────────────

_SYSTEM = (
    "Sen do'kon egasining o'zbekcha buyrug'ini tahlil qilasan. "
    "U qanday video kerakligini aytadi. FAQAT JSON qaytar:\n"
    '{"type":"ad|compare|talk","products":["tovar nomi","ikkinchisi"],'
    '"topic":"videoning qisqa mavzusi","note":"nimaga urg\'u berish"}\n'
    "type: ad = bitta tovar reklamasi; compare = ikki tovarni solishtirish; "
    "talk = ikki tovar o'zaro gaplashadigan hazil rolik. "
    "products — buyruqda AYTILGAN tovar nomlari (bo'lmasa bo'sh ro'yxat). "
    "Boshqa hech narsa yozma."
)


def _guess_type(text: str) -> str:
    t = (text or "").lower()
    if any(w in t for w in ("suhbat", "gaplash", "bahslash", "dialog", "aytishuv")):
        return "talk"
    if any(w in t for w in ("solishtir", "taqqosla", "farqi", "qaysi bir", " vs ")):
        return "compare"
    return "ad"


def _parse_json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except ValueError:
        return {}


async def understand(db, text: str) -> dict:
    """Matn → {type, pids, names, topic, note, warn}."""
    from bot import ai_engine
    loop = asyncio.get_event_loop()
    d = {}
    try:
        raw = await loop.run_in_executor(
            None, lambda: ai_engine.chat(f"Buyruq: «{text}»", system=_SYSTEM,
                                         validate=lambda s: "{" in s))
        d = _parse_json(raw)
    except Exception as e:
        logger.warning("💡 G'oyani AI tushunmadi: %s", e)

    turi = (d.get("type") or "").strip().lower()
    if turi not in TYPES:
        turi = _guess_type(text)
    nomlar = [str(x).strip() for x in (d.get("products") or []) if str(x).strip()]
    if not nomlar:
        # AI yiqilsa — gapdagi «bo'sh bo'lmagan» so'zlar bo'yicha qidiramiz
        nomlar = [" ".join(_words(text)[:3])] if _words(text) else []

    pids, topilgan, warn = [], [], ""
    for nom in nomlar[:2]:
        pr = await find_products(db, nom, limit=1)
        if pr:
            pids.append(pr[0]["id"])
            topilgan.append(pr[0]["name"])
        else:
            warn = f"«{nom}» katalogdan topilmadi"
    # Taqqoslash/suhbat uchun ikkinchisi kerak — o'xshashini o'zimiz tanlaymiz
    if turi in ("compare", "talk") and len(pids) == 1:
        other = await _partner_for(db, pids[0])
        if other:
            pids.append(other["id"])
            topilgan.append(other["name"] + " (tizim tanladi)")
        else:
            warn = "Juftlik topilmadi — ikkinchi tovarni ham ayting"
    return {"type": turi, "pids": pids, "names": topilgan,
            "topic": (d.get("topic") or "").strip(),
            "note": (d.get("note") or "").strip(), "warn": warn}


# ─── Bajarish ──────────────────────────────────────────────────────────────

async def run(db, plan: dict) -> dict:
    """Rejani bajaradi: {ok, text}. Video tayyor bo'lgach tasdiq kartasi
    keladi (taqqoslash/suhbat), reklama roliki esa o'z oqimi bilan ketadi."""
    turi, pids = plan.get("type"), plan.get("pids") or []
    if not pids:
        return {"ok": False, "text": "Tovar tanlanmadi"}

    if turi == "compare":
        from bot import reels_compare as C
        res = await C.produce_pair(db, pids, plan.get("topic") or "")
        return ({"ok": True, "text": "🎬 Taqqoslash roliki tayyor — tasdiqlang"}
                if res.get("ok") else {"ok": False, "text": res.get("error", "xato")})

    if turi == "talk":
        from bot import reels_talk as T
        res = await T.produce(db, pids)
        return ({"ok": True, "text": "🗣 Suhbat roliki tayyor — tasdiqlang"}
                if res.get("ok") else {"ok": False, "text": res.get("error", "xato")})

    # ── Reklama roliki ──
    from bot import reels as R, reels_compare as C
    from bot import api_recommend as RC
    idx = await RC._index(db)
    p = idx.items.get(int(pids[0]))
    if not p:
        return {"ok": False, "text": "Tovar topilmadi"}
    img = C._img_path(p)
    if not img:
        return {"ok": False, "text": f"«{p.get('name')}» rasmi yo'q — rolik rasmsiz chiqmaydi"}
    urgu = (plan.get("note") or plan.get("topic") or "").strip()
    rid = await asyncio.get_event_loop().run_in_executor(
        None, lambda: R.enqueue_manual(int(p["id"]), img, urgu))
    if not rid:
        return {"ok": False, "text": "Bu tovar allaqachon navbatda turibdi"}
    return {"ok": True, "text": f"🎬 «{p.get('name')}» reklama roliki navbatga qo'shildi — "
                                f"tayyor bo'lgach o'zi chiqadi"}


# ─── Ovozdan matnga ────────────────────────────────────────────────────────

async def voice_to_text(bot, voice) -> str:
    """Telegram ovozli xabari → matn. Bo'sh = eshitilmadi."""
    from bot import ai_engine
    from database._helpers import DB_PATH
    tmp = os.path.join(os.path.dirname(DB_PATH), "tmp")
    os.makedirs(tmp, exist_ok=True)
    path = os.path.join(tmp, f"idea_{int(time.time())}.ogg")
    try:
        f = await bot.get_file(voice.file_id)
        await bot.download_file(f.file_path, destination=path)
        return await asyncio.get_event_loop().run_in_executor(
            None, lambda: ai_engine.transcribe(path, "audio/ogg"))
    except Exception as e:
        logger.warning("🎧 Ovoz olinmadi: %s", e)
        return ""
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
