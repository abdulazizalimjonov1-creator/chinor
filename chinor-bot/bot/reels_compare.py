"""«Savol → taqqoslash» roligi — maslahat savollaridan har kuni bitta video.

Oddiy reklama («bu tovar zo'r») lentada o'tib ketadi. Odamni to'xtatadigan
narsa — uning O'Z muammosi bilan boshlanadigan savol va aniq javob:

  0–3 s   ILGAK      "Darvozangiz bir yilda zanglab ketdimi?"
  3–6 s   ZIDDIYAT   ikki tovar yonma-yon: nomi, narxi, «VS»
  6–15 s  TAQQOSLASH qatorlar birin-ketin: mamlakat, 1 kg narxi, sarf,
                     do'kon bahosi — har qatorda yutgan tomon belgilanadi
  15–20 s XULOSA     "Tashqi ish uchun — A", "Arzon variant — B"
  20–23 s CHAQIRUV   "Narxi va buyurtma — Chinor"

ROSTGOYLIK. Taqqoslash QATORLARI AI'dan emas — to'g'ridan-to'g'ri bazadan:
tasdiqlangan yorliq kartochkasi (`api_labels`), nomdagi hajmdan hisoblangan
1 kg narxi, do'kon bahosi. AI faqat ilgak, muammo, xulosa va diktor matnini
yozadi va unga faqat shu faktlar beriladi. Mavzu — admin NASHR qilgan
maslahat savoli (u allaqachon tekshirilgan); bo'lmasa yorliq kartochkasi
bor bir bo'limdagi ikki tovar.

Video avval ADMINGA keladi (✅ Kanalga / ❌). Tasdiqlansa kanalga chiqadi.

Dvigatel: `bot.reels` ning chizish vositalari, `reels_audio` musiqasi va
`tts` diktori qayta ishlatiladi.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import sqlite3
import subprocess
import sys
import time

logger = logging.getLogger(__name__)

FPS = 30
MAX_ROWS = 3            # 4 qator videoni 30 s dan oshirardi
REPEAT_DAYS = 14          # bir mavzu shuncha kun ichida takrorlanmaydi


# ── Navbat / tarix ─────────────────────────────────────────────────────────

def _conn():
    from database._helpers import DB_PATH
    c = sqlite3.connect(DB_PATH, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS reels_compare (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        guide_id    INTEGER DEFAULT 0,
        topic       TEXT DEFAULT '',
        pids        TEXT DEFAULT '',
        script      TEXT DEFAULT '',
        video_path  TEXT DEFAULT '',
        status      TEXT DEFAULT 'new',
        error       TEXT DEFAULT '',
        channel_msg INTEGER DEFAULT 0,
        created_at  TEXT DEFAULT (datetime('now','localtime'))
    )""")
    c.commit()
    return c


def _recent_keys(days: int = REPEAT_DAYS) -> set:
    c = _conn()
    try:
        rows = c.execute(
            "SELECT guide_id, pids FROM reels_compare WHERE status NOT IN ('failed') "
            "AND created_at >= datetime('now','localtime', ?)", (f"-{days} days",)).fetchall()
        return {(r["guide_id"], r["pids"]) for r in rows} | {(r["guide_id"], "") for r in rows if r["guide_id"]}
    finally:
        c.close()


def _set(rid: int, **kw):
    c = _conn()
    try:
        cols = ", ".join(f"{k}=?" for k in kw)
        c.execute(f"UPDATE reels_compare SET {cols} WHERE id=?", (*kw.values(), rid))
        c.commit()
    finally:
        c.close()


def get(rid: int) -> dict:
    c = _conn()
    try:
        r = c.execute("SELECT * FROM reels_compare WHERE id=?", (rid,)).fetchone()
        return dict(r) if r else {}
    finally:
        c.close()


# ── Mavzu tanlash ──────────────────────────────────────────────────────────

def _img_path(p: dict) -> str:
    from bot.web_api import UPLOAD_DIR
    u = (p.get("image_url") or "").strip()
    if not u:
        return ""
    path = os.path.join(UPLOAD_DIR, os.path.basename(u))
    return path if os.path.exists(path) else ""


async def _info(db, pid: int) -> dict:
    from bot import api_labels
    return await db._in_thread(api_labels.public_info, db, pid) or {}


def _fact(info: dict, prefix: str) -> str:
    for f in info.get("facts") or []:
        if f["k"].startswith(prefix):
            return f["v"]
    return ""


def _buyers_map() -> dict:
    """pid → oxirgi 30 kunda nechta chekda uchragani (YesPOS cheklari)."""
    try:
        from bot import api_recommend as R
        return dict(R._LAST_BUYERS)
    except Exception:
        return {}


async def _pick_pair(db, prods: list) -> list:
    """Ikki tovar: imkon qadar ikkalasida yorliq kartochkasi bo'lsin, turli
    oilalardan (bir xil tovarning 2,5 va 0,7 kg lik bankasi taqqoslanmaydi),
    biri «sifatli» (baho yuqori / qimmatroq), biri «arzon» (1 kg arzonroq)."""
    from bot.api_guides import size_kg, _family
    scored = []
    for p in prods:
        if float(p.get("qty") or 0) <= 0 or float(p.get("sell_price") or 0) <= 0:
            continue
        info = await _info(db, p["id"])
        kg = size_kg(p.get("name", ""))
        per = float(p["sell_price"]) / kg if kg > 0 else 0
        scored.append({"p": p, "info": info, "per": per, "kg": kg,
                       "buyers": int(_buyers_map().get(p["id"], 0)),
                       "facts": bool(info.get("facts")),
                       "rating": int(info.get("store_rating") or 0),
                       "img": bool(_img_path(p)), "fam": _family(p.get("name", ""))})
    if len(scored) < 2:
        return []
    # Faktlisi va rasmlisi oldinda
    # Rasmi bor tovarlar afzal — videoda rasmsiz kartochka zaif ko'rinadi
    scored.sort(key=lambda s: (-(s["facts"] and s["img"]), -s["facts"], -s["img"], -s["rating"]))
    best = scored[0]
    others = [s for s in scored[1:] if s["fam"] != best["fam"]] or scored[1:]
    # Ikkinchisi — eng katta narx/sifat farqi (taqqoslash ma'noli bo'lsin)
    if best["per"] > 0:
        others.sort(key=lambda s: (-s["facts"], -s["img"], -abs((s["per"] or best["per"]) - best["per"])))
    pair = [best, others[0]]
    # Chapda «sifatli» (bahosi yuqori yoki qimmatroq), o'ngda «arzon»
    pair.sort(key=lambda s: (-s["rating"], -(s["per"] or float(s["p"]["sell_price"]))))
    return pair


async def pick_topic(db, guide_id: int = 0, skip: set = None) -> dict:
    """{guide, pair, topic} yoki {} — mos mavzu yo'q."""
    from bot import api_guides
    recent = _recent_keys()
    guides = await db._in_thread(api_guides._rows, db, "WHERE status='published'")
    if guide_id:
        guides = [g for g in await db._in_thread(api_guides._rows, db, "WHERE id=?", (guide_id,))]
    for g in guides:
        if not guide_id and (g["id"], "") in recent:
            continue
        prods = await api_guides._products_for(db, g["query"], g["calc"] if not g["query"] else "")
        pair = await _pick_pair(db, prods)
        if pair and ("g", g["id"]) not in (skip or set()):
            return {"guide": g, "pair": pair, "topic": g["question"]}
    if guide_id:
        return {}
    # Zaxira: yorliq kartochkasi bor bir bo'limdagi ikki tovar
    from bot import api_labels, api_recommend as R
    idx = await R._index(db)
    rows = await db._in_thread(api_labels._all_rows, db)
    by_cat = {}
    for r in rows:
        p = idx.items.get(r["product_id"])
        if p and r.get("status") == "approved" and int(p.get("category_id") or 0):
            by_cat.setdefault(int(p["category_id"]), []).append(p)
    for cid, plist in by_cat.items():
        members = plist + [p for p in idx.items.values()
                           if int(p.get("category_id") or 0) == cid and p not in plist][:10]
        pair = await _pick_pair(db, members)
        if pair:
            key = ",".join(str(s["p"]["id"]) for s in pair)
            if (0, key) in recent or ("c", cid) in (skip or set()):
                continue
            c = await db.get_category(cid)
            return {"guide": None, "pair": pair, "cid": cid,
                    "topic": f"{(c or {}).get('name', '')}: qaysi birini olish kerak?"}
    return {}


# ── Faktlar → qatorlar (AI'siz) ────────────────────────────────────────────

def _short(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    s = re.sub(r"[«»\"]", "", s)
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


def build_rows(pair: list) -> list:
    """[{label, a, b, win}] — faqat ikkalasida ham qiymati bor qatorlar."""
    a, b = pair
    rows = []

    def add(label, va, vb, win=""):
        if va and vb and len(rows) < MAX_ROWS:
            rows.append({"label": label, "a": _short(va, 22), "b": _short(vb, 22), "win": win})

    add("Ishlab chiqarilgan", _fact(a["info"], "Ishlab chiqarilgan mamlakat"),
        _fact(b["info"], "Ishlab chiqarilgan mamlakat"))
    # Yorliq kartochkasi kam — bazadagi HAQIQIY ma'lumot ham qator bo'ladi:
    # hajm (nomdan), oxirgi 30 kunda nechta xaridda bo'lgani (YesPOS cheklari).
    ka, kb = a.get("kg") or 0, b.get("kg") or 0
    if ka > 0 and kb > 0 and ka != kb:
        add("Hajmi", f"{ka:g} kg", f"{kb:g} kg")
    if a["per"] > 0 and b["per"] > 0:
        add("1 kg narxi", f"{a['per']:,.0f}".replace(",", " ") + " so'm",
            f"{b['per']:,.0f}".replace(",", " ") + " so'm",
            "a" if a["per"] < b["per"] else ("b" if b["per"] < a["per"] else ""))
    else:
        pa, pb = float(a["p"]["sell_price"]), float(b["p"]["sell_price"])
        add("Narxi", f"{pa:,.0f}".replace(",", " ") + " so'm",
            f"{pb:,.0f}".replace(",", " ") + " so'm",
            "a" if pa < pb else ("b" if pb < pa else ""))
    add("Sarfi", _fact(a["info"], "Sarfi"), _fact(b["info"], "Sarfi"))
    add("Qurishi", _fact(a["info"], "Qurish"), _fact(b["info"], "Qurish"))
    ba, bb = a.get("buyers") or 0, b.get("buyers") or 0
    if ba > 0 and bb > 0:
        add("30 kunda olishdi", f"{ba} marta", f"{bb} marta",
            "a" if ba > bb else ("b" if bb > ba else ""))
    ra, rb = a["rating"], b["rating"]
    if ra and rb:
        add("Do'kon bahosi", "★" * ra, "★" * rb, "a" if ra > rb else ("b" if rb > ra else ""))
    add("Texnologiya", _fact(a["info"], "Texnologiya"), _fact(b["info"], "Texnologiya"))
    add("Brend", _fact(a["info"], "Brend"), _fact(b["info"], "Brend"))
    return rows


# ── Ssenariy (AI: faqat so'zlar; raqam va faktlar bazadan) ────────────────
# Video DIKTOR bilan boshqariladi: har sahna — bitta gap. Gap qancha
# davom etsa, sahna shuncha turadi. Ilgari vaqtlar qat'iy edi va diktor
# faqat 2 bo'lak gapirardi — taqqoslash qatorlari chiqayotgan 9 soniya
# davomida ovoz yo'q edi, xulosa sahnasi esa qotib turardi. Lentada bu
# "video qotib qoldi" degan taassurot berib, odam o'tkazib yuboradi.

_SYSTEM = (
    "Sen Instagram Reels uchun qisqa video ssenariy yozuvchisi va diktorisan. "
    "Xo'jalik-qurilish do'koni Chinor (Toshkent, Sergeli). O'zbek tilida (lotin). "
    "Tajribali usta do'stiga tushuntirayotgandek gapir: sodda, jonli, qisqa gaplar. "
    "Maqtov bilan emas, xaridorning MUAMMOSI bilan boshla. QOIDALAR: faqat berilgan "
    "faktlar va qiymatlardan foydalan; raqamni o'zgartirma; mamlakat/texnologiya "
    "faqat faktda bo'lsa; 'eng zo'r', 'hamma oladi' kabi tasdiqlanmagan gap yozma; "
    "do'kon bahosini 'xaridorlar bahosi' dema; emoji ishlatma. Faqat JSON."
)


def _script_prompt(topic: str, answer: str, pair: list, rows: list) -> str:
    def desc(s):
        p = s["p"]
        nm = (p.get("display_name") or "").strip() or p.get("name", "")
        facts = "; ".join(f"{f['k']}: {_short(f['v'], 80)}" for f in (s["info"].get("facts") or []))
        return (f"{nm} | narx {float(p['sell_price']):,.0f} so'm"
                + (f" | do'kon bahosi {s['rating']}/5" + (f" ({s['info'].get('store_rating_note')})"
                                                          if s['info'].get('store_rating_note') else "")
                   if s["rating"] else "")
                + (f" | {facts}" if facts else ""))
    rows_txt = "\n".join(f"  {i + 1}) {r['label']}: A = {r['a']}, B = {r['b']}"
                         + (f" (yaxshirog'i: {r['win'].upper()})" if r['win'] else "")
                         for i, r in enumerate(rows))
    return (
        f"Mavzu (savol): {topic}\n"
        + (f"Do'kon tasdiqlagan javob: {_short(answer, 700)}\n" if answer else "")
        + f"\nA: {desc(pair[0])}\nB: {desc(pair[1])}\n"
        + f"Ekranda ketma-ket chiqadigan taqqoslash qatorlari:\n{rows_txt}\n\n"
        "ILGAK QOIDASI: savolni takrorlama — xaridorning AYNAN vaziyatini yoki og'rig'ini ayt "
        "(yomon: 'Qaysi emalni tanlash kerak?'; yaxshi: 'Darvozangiz bir yilda zanglab ketdimi?', "
        "'Bo'yoq 3 oyda ko'chib tushdimi?', 'Pasta tishni achishtiryaptimi?').\n"
        "Har 'rows_say' gapi o'sha qatorni odamga o'xshab tushuntirsin va ikkala qiymatni "
        "AYNAN aytsin (masalan: 'Birinchisi Turkiyada, ikkinchisi O'zbekistonda ishlab chiqarilgan.'). "
        "Qatorlar soni bilan bir xil bo'lsin.\n"
        "Diktor gaplari JUDA QISQA bo'lsin: hook_say ≤ 7 so'z, problem_say ≤ 12, har rows_say ≤ 11, "
        "verdict_*_say ≤ 9, cta_say ≤ 9. Butun video 22 soniyadan oshmasin.\n"
        'JSON: {"hook": "ekran uchun ilgak, 3-7 so\'z, savol belgisi bilan", '
        '"hook_say": "diktor uchun ilgak, 1 qisqa gap", '
        '"problem": "ekran uchun ziddiyat, 5-10 so\'z", '
        '"problem_say": "diktor: ziddiyat va A, B ni tanishtirish, 1-2 gap", '
        '"rows_say": ["har qator uchun 1 gap", ...], '
        '"verdict_a": "ekran: A kimga mos, 3-7 so\'z", "verdict_a_say": "diktor, 1 gap", '
        '"verdict_b": "ekran: B kimga mos, 3-7 so\'z", "verdict_b_say": "diktor, 1 gap", '
        '"cta_say": "diktor: chaqiruv, 1 gap, oxiri Chinor do\'konida"}'
    )


_KEYS = ("hook", "hook_say", "problem", "problem_say", "verdict_a", "verdict_a_say",
         "verdict_b", "verdict_b_say", "cta_say")


def _parse(text: str):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(d, dict) or not all(str(d.get(k) or "").strip() for k in _KEYS):
        return None
    out = {k: (_tight(d[k], _MAX_WORDS.get(k, 99)) if k.endswith("_say") else _short(d[k], 60))
           for k in _KEYS}
    out["rows_say"] = [_tight(x, 12) for x in (d.get("rows_say") or []) if str(x).strip()]
    return out


# Diktor gaplarining so'z chegarasi. Lentada odam 1–2 soniyada qaror qiladi:
# 12 soniyalik ilgak (birinchi sinovda shunday chiqdi) — o'tkazib yuborish.
# 2026-10-23: Instagram statistikasi — 36 soniyalik videoni odamlar o'rtacha
# 3–5 soniya ko'rgan. Qisqartirildi: maqsad ~20 soniya.
_MAX_WORDS = {"hook_say": 8, "problem_say": 13, "verdict_a_say": 10,
              "verdict_b_say": 10, "cta_say": 10}


def _tight(text: str, max_words: int) -> str:
    """Uzun gap — birinchi jumlasi; u ham uzun bo'lsa so'z chegarasida kesiladi."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(t.split()) <= max_words:
        return t
    first = re.split(r"(?<=[.!?])\s+", t)[0]
    if len(first.split()) <= max_words:
        return first
    words = first.split()[:max_words]
    return " ".join(words).rstrip(",;:—-") + ("?" if first.endswith("?") else ".")


def _mentions(sent: str, value: str) -> bool:
    """Gap qiymatni aytadimi: raqamlar to'liq, so'zlar o'zagi bo'yicha."""
    s = sent.lower().replace(" ", "")
    v = value.lower()
    digits = re.findall(r"\d+", v.replace(" ", ""))
    if digits:
        return all(d in s for d in digits)
    stems = [w[:5] for w in re.findall(r"[a-zʻʼ'’]+", v) if len(w) >= 3]
    return not stems or any(st.replace("'", "") in s.replace("'", "") for st in stems)


def _row_say(r: dict, ai: str) -> str:
    """AI gapi ikkala qiymatni aytsa — o'sha; aytmasa — shablon (fakt yo'qolmasin)."""
    if ai and r["a"].strip("★") and _mentions(ai, r["a"]) and _mentions(ai, r["b"]):
        return ai
    if r["a"].startswith("★"):
        return f"Do'kon bahosi: birinchisiga {len(r['a'])}, ikkinchisiga {len(r['b'])} yulduz."
    return f"{r['label']}: birinchisi {r['a']}, ikkinchisi {r['b']}."


def _outro_say(store: dict) -> str:
    """Do'kon haqida yakuniy gap — AI emas, JONLI raqamlardan (qo'lda yozilgan
    "5000 xil" tez eskiradi yoki noto'g'ri bo'lib qoladi)."""
    n = int(store.get("count") or 0)
    parts = []
    # 2026-10-23: qisqa (~7 so'z). Ish vaqti va manzil ekranda va izohda turadi —
    # diktor ularni o'qisa yakun 8 soniyaga cho'zilib, video 33 s bo'lib qolardi.
    if n >= 100:
        # Raqam bo'shliqsiz: "5 200" ni diktor "besh, ikki yuz" deb bo'lib o'qiydi
        parts.append(f"Chinor, Sergeli: {store['count_say']} dan ortiq tovar.")
    else:
        parts.append("Narxi va buyurtma — Chinor do'konida, Sergeli.")
    return " ".join(parts)


async def store_info(db) -> dict:
    """Video oxiri uchun do'kon faktlari: tovar soni, bo'limlar, rasmlar devori."""
    from bot import api_recommend as R
    from bot.config import SHOP_LOCATION, SHOP_HOURS, SHOP_HOURS_SAY
    idx = await R._index(db)
    n = len(idx.items)
    cats = len({int(p.get("category_id") or 0) for p in idx.items.values()} - {0})
    imgs = []
    day = int(time.time() // 86400)
    ordered = sorted(idx.items.values(), key=lambda p: (p["id"] * 2654435761 + day) % 100003)
    for p in ordered:
        path = _img_path(p)
        if path:
            imgs.append(path)
        if len(imgs) >= 18:
            break
    rounded = (n // 100) * 100
    return {"count": n, "count_txt": f"{rounded:,}".replace(",", " "),
            "cats": cats if cats >= 5 else 0, "location": SHOP_LOCATION,
            "hours": SHOP_HOURS, "hours_say": SHOP_HOURS_SAY,
            "count_say": str(rounded), "images": imgs}


def make_script(topic: dict, rows: list) -> dict:
    """Bloklovchi (AI). Natija: ekran matnlari + `segments` (diktor gaplari)."""
    from bot import ai_engine
    g = topic.get("guide") or {}
    raw = ai_engine.chat(_script_prompt(topic["topic"], g.get("answer", ""), topic["pair"], rows),
                         _SYSTEM, 40, validate=lambda t: _parse(t) is not None)
    d = _parse(raw) or {
        "hook": _short(topic["topic"], 60), "hook_say": topic["topic"],
        "problem": "Ikkalasi bor — farqi nimada?",
        "problem_say": "Do'konda ikkalasi ham bor. Keling, farqini ko'ramiz.",
        "rows_say": [], "verdict_a": "Sifat muhim bo'lsa", "verdict_a_say": "Sifat muhim bo'lsa — birinchisi.",
        "verdict_b": "Tejamkor tanlov", "verdict_b_say": "Tejamoqchi bo'lsangiz — ikkinchisi.",
        "cta_say": "Narxi va buyurtma — Chinor do'konida."}
    ai_rows = d.get("rows_say") or []
    segs = [{"kind": "hook", "say": d["hook_say"]},
            {"kind": "problem", "say": d["problem_say"]}]
    for i, r in enumerate(rows):
        segs.append({"kind": "row", "i": i, "say": _row_say(r, ai_rows[i] if i < len(ai_rows) else "")})
    segs += [{"kind": "verdict", "i": 0, "say": d["verdict_a_say"]},
             {"kind": "verdict", "i": 1, "say": d["verdict_b_say"]},
             {"kind": "outro", "say": _outro_say(topic.get("store") or {})}]
    d["segments"] = segs
    return d


# ── Diktor: bitta so'rov, pauzalar bo'yicha bo'laklarga ajratish ───────────
# Har gapga alohida TTS so'rovi bepul kvotada 429 beradi (daqiqasiga bir
# necha so'rov). Shuning uchun BUTUN matn bitta so'rovda o'qitiladi (har gap
# yangi qatorda — diktor pauza qiladi), keyin ovozdagi jimlik joylari
# topilib, har gapning boshlanish-tugash vaqti aniqlanadi.

_VOICE_STYLE = ("Quyidagi matnni o'zbek tilida, tajribali usta do'stiga maslahat "
                "berayotgandek jonli, ishonarli va biroz tez sur'atda o'qing. Har bir "
                "qatordan keyin qisqa (yarim soniyacha) pauza qiling. Faqat matnning "
                "o'zini o'qing:\n")
MAX_SPEECH = 24.0     # diktor shundan uzun bo'lsa — biroz tezlashtiriladi


def _speech_spans(wav: str) -> list:
    """[(boshlanish, tugash)] — ovoz bor oraliqlar (jimlik ≥0.28 s bo'yicha)."""
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-i", wav, "-af",
                            "silencedetect=noise=-30dB:d=0.22", "-f", "null", "-"],
                           capture_output=True, timeout=60)
    except Exception:
        return []
    log = (r.stderr or b"").decode()
    starts = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", log)]
    from bot import tts
    total = tts.duration(wav)
    spans, cur = [], 0.0
    for s0, e0 in zip(starts, ends + [total] * (len(starts) - len(ends))):
        if s0 - cur > 0.12:
            spans.append((cur, s0))
        cur = e0
    if total - cur > 0.12:
        spans.append((cur, total))
    return spans


def _align(segs: list, spans: list, total: float) -> list:
    """Har gapga (boshlanish, tugash) — gap uzunligiga mutanosib kutilgan
    chegaraga ENG YAQIN jimlikka "yopishtirib". Diktor gap ichida ham
    to'xtashi mumkin, shuning uchun jimliklar soni gaplardan ko'p bo'ladi."""
    n = len(segs)
    if not spans:
        return []
    gaps = [((spans[i][1] + spans[i + 1][0]) / 2) for i in range(len(spans) - 1)]
    lens = [max(1, len(s["say"])) for s in segs]
    tot = sum(lens)
    t0, t1 = spans[0][0], spans[-1][1]
    bounds, acc, used = [], 0, -1
    for k in range(n - 1):
        acc += lens[k]
        want = t0 + (t1 - t0) * acc / tot
        cands = [j for j in range(used + 1, len(gaps)) if len(gaps) - j >= (n - 1 - k)]
        if not cands:
            bounds.append(want)
            continue
        j = min(cands, key=lambda j: abs(gaps[j] - want))
        used = j
        bounds.append(gaps[j])
    edges = [t0] + bounds + [t1]
    out = [(edges[i], edges[i + 1]) for i in range(n)]
    # Tekshiruv: diktor pauza qilmasa (yoki juda qisqa pauza), jimliklar
    # gaplarga mos kelmaydi va hamma gap boshiga yopishib qoladi — video
    # 7-soniyadan oxirgi sahnada qotib turgan (2026-10-20 sinovi). Shunday
    # bo'lsa, vaqt gap uzunligiga MUTANOSIB taqsimlanadi (nutq sur'ati
    # deyarli bir xil — sezilarli farq bo'lmaydi).
    exp = [(t1 - t0) * l / tot for l in lens]
    bad = any(not (0.4 * e <= (b - a) <= 2.5 * e) for (a, b), e in zip(out, exp))
    if bad:
        logger.info("🎬 Diktor pauzalari gaplarga mos emas — mutanosib vaqt")
        acc, out = t0, []
        for e in exp:
            out.append((acc, acc + e))
            acc += e
    return out


def build_voice(script: dict, out_wav: str) -> list:
    """Diktor yo'li + har gap vaqti [(start, end)]. Chiqmasa [] (ovozsiz rejim)."""
    try:
        from bot import tts
    except Exception:
        return []
    if not tts.available():
        return []
    text = "\n".join(s["say"] for s in script["segments"])
    if not tts.speak(text, out_wav, style=_VOICE_STYLE):
        return []
    total = tts.duration(out_wav)
    # Juda uzun chiqsa — 1.3 baravargacha tezlashtiramiz (ohang buzilmaydi)
    if total > MAX_SPEECH:
        tempo = min(1.3, total / MAX_SPEECH)
        fast = out_wav + ".fast.wav"
        r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", out_wav,
                            "-af", f"atempo={tempo:.3f}", fast], capture_output=True, timeout=60)
        if r.returncode == 0:
            os.replace(fast, out_wav)
            total = tts.duration(out_wav)
    times = _align(script["segments"], _speech_spans(out_wav), total)
    if len(times) != len(script["segments"]) or total < 5:
        return []
    return times


def silent_times(script: dict) -> list:
    """Diktor bo'lmasa — subtitr o'qishga yetadigan vaqt (sekundiga ~13 harf)."""
    t, out = 0.0, []
    for s in script["segments"]:
        d = max(2.2, min(6.0, len(s["say"]) / 13.0))
        out.append((t, t + d))
        t += d + 0.25
    return out


# ── Chizish ────────────────────────────────────────────────────────────────

LEAD = 0.35        # video boshlanishi bilan diktor orasidagi kichik nafas
TAIL = 1.4         # oxirgi gapdan keyin


class _Layers:
    def __init__(self, topic: dict, rows: list, script: dict):
        from PIL import Image, ImageDraw
        from bot import reels as R
        self.R, self.Image, self.ImageDraw = R, Image, ImageDraw
        W, H = R.W, R.H
        self.W, self.H = W, H
        self.bg = R._gradient(W, H, R.BG_TOP, R.BG_BOTTOM).convert("RGBA")
        self.glow = self._glow()
        self.head = R._text_layer((W, int(H * 0.08)), self._head)
        self.hook = R._text_layer((W, int(H * 0.30)), lambda d, im: self._big(d, script["hook"], 0.098, 3))
        self.problem = R._text_layer((W, int(H * 0.22)),
                                     lambda d, im: self._big(d, script["problem"], 0.058, 3, R.ACCENT))
        self.cards = [self._card(s, i) for i, s in enumerate(topic["pair"])]
        self.vs = self._vs()
        self.rows = [self._row(r) for r in rows]
        self.row_hi = [self._row(r, hot=True) for r in rows]
        names = [(s["p"].get("display_name") or s["p"].get("name") or "") for s in topic["pair"]]
        self.verdicts = [self._verdict(script["verdict_a"], names[0], 0),
                         self._verdict(script["verdict_b"], names[1], 1)]
        self.cta = R._text_layer((W, int(H * 0.30)), self._cta)
        store = topic.get("store") or {}
        self.outro = R._text_layer((W, int(H * 0.42)), lambda d, im: self._outro(d, im, store))
        self.wall = self._wall(store.get("images") or [])
        # Subtitr gapni 7 so'zlik bo'laklarda ko'rsatadi — uzun gap 3 qatorga
        # sig'may kesilib qolardi
        self.sub_words = [s["say"].split() for s in script["segments"]]
        self.sub_chunks = [[w[i:i + 7] for i in range(0, len(w), 7)] or [[""]]
                           for w in self.sub_words]
        self._sub_cache = {}

    # — qatlam chizuvchilar —
    def _glow(self):
        from PIL import ImageFilter
        Image, ImageDraw = self.Image, self.ImageDraw
        g = Image.new("L", (self.W, self.W), 0)
        ImageDraw.Draw(g).ellipse([self.W * 0.15, self.W * 0.15, self.W * 0.85, self.W * 0.85], fill=120)
        g = g.filter(ImageFilter.GaussianBlur(int(self.W * 0.14)))
        lay = Image.new("RGBA", g.size, (60, 140, 100, 0))
        lay.putalpha(g)
        return lay

    def _head(self, d, im):
        R = self.R
        try:
            from bot.imageproc import _logo
            lg = _logo(int(self.W * 0.10))
            if lg is not None:
                im.paste(lg, (int(self.W * 0.07), 8), lg)
        except Exception:
            pass
        d.text((int(self.W * 0.20), 18), "CHINOR · USTA MASLAHATI",
               font=R._font(int(self.W * 0.040)), fill=(255, 255, 255, 255))

    def _big(self, d, text, size_k, max_lines, color=(255, 255, 255)):
        R = self.R
        f = R._font(int(self.W * size_k))
        y = 0
        for line in R._wrap(d, R._strip_emoji(text), f, int(self.W * 0.86), max_lines):
            tw = d.textlength(line, font=f)
            d.text(((self.W - tw) / 2, y), line, font=f, fill=tuple(color) + (255,))
            y += int(self.W * size_k * 1.18)

    def _card(self, s, side):
        R, Image, ImageDraw = self.R, self.Image, self.ImageDraw
        cw, ch = int(self.W * 0.43), int(self.H * 0.34)
        card = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        d = ImageDraw.Draw(card)
        d.rounded_rectangle([0, 0, cw - 1, ch - 1], radius=36, fill=R.CARD_BG + (255,))
        name = R._strip_emoji((s["p"].get("display_name") or "").strip() or s["p"].get("name", ""))
        path = _img_path(s["p"])
        try:
            if path:
                im = Image.open(path).convert("RGBA")
                im.thumbnail((cw - 50, int(ch * 0.55)), Image.LANCZOS)
                card.alpha_composite(im, ((cw - im.width) // 2, 24 + (int(ch * 0.55) - im.height) // 2))
            else:
                # Rasm yo'q — so'roq belgisi emas, brend nomi katta harflarda
                brand = (_fact(s["info"], "Brend") or name.split()[1:2] or [name])
                brand = brand if isinstance(brand, str) else brand[0]
                f = R._font(int(cw * 0.16))
                lines = R._wrap(d, brand.upper(), f, cw - 60, 2)
                y = int(ch * 0.22)
                for line in lines:
                    tw = d.textlength(line, font=f)
                    d.text(((cw - tw) / 2, y), line, font=f, fill=(30, 110, 70, 255))
                    y += int(cw * 0.19)
        except Exception:
            pass
        f = R._font(int(cw * 0.075))
        y = int(ch * 0.62)
        for line in R._wrap(d, name, f, cw - 40, 2):
            d.text((22, y), line, font=f, fill=(14, 52, 33, 255))
            y += int(cw * 0.09)
        price = f"{float(s['p']['sell_price']):,.0f}".replace(",", " ") + " so'm"
        d.text((22, ch - int(cw * 0.15)), price, font=R._font(int(cw * 0.095)), fill=(30, 110, 70, 255))
        tag = "A" if side == 0 else "B"
        d.ellipse([cw - 78, 16, cw - 16, 78], fill=(30, 110, 70, 255))
        d.text((cw - 60, 22), tag, font=R._font(40), fill=(255, 255, 255, 255))
        return card

    def _vs(self):
        R, Image, ImageDraw = self.R, self.Image, self.ImageDraw
        s = 150
        im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.ellipse([0, 0, s - 1, s - 1], fill=R.ACCENT + (255,))
        f = R._font(64)
        tw = d.textlength("VS", font=f)
        d.text(((s - tw) / 2, 36), "VS", font=f, fill=(9, 40, 25, 255))
        return im

    def _row(self, r, hot=False):
        R, Image, ImageDraw = self.R, self.Image, self.ImageDraw
        W = self.W
        im = Image.new("RGBA", (W, 170), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        if hot:      # hozir gapirilayotgan qator — ramka bilan ajraladi
            d.rounded_rectangle([int(W * 0.03), 0, int(W * 0.97), 166], radius=34,
                                outline=(255, 255, 255, 230), width=5)
        fl = R._font(int(W * 0.032), bold=False)
        tw = d.textlength(r["label"].upper(), font=fl)
        d.text(((W - tw) / 2, 8), r["label"].upper(), font=fl, fill=R.ACCENT + (255,))
        fv = R._font(int(W * 0.045))
        for side, val in ((0, r["a"]), (1, r["b"])):
            x0 = int(W * 0.06) if side == 0 else int(W * 0.52)
            x1 = int(W * 0.48) if side == 0 else int(W * 0.94)
            win = r["win"] == ("a" if side == 0 else "b")
            d.rounded_rectangle([x0, 54, x1, 150], radius=28,
                                fill=(R.ACCENT + (255,)) if win else (255, 255, 255, 38))
            lines = R._wrap(d, val, fv, x1 - x0 - 30, 1)
            txt = lines[0] if lines else val
            tw = d.textlength(txt, font=fv)
            d.text((x0 + (x1 - x0 - tw) / 2, 76), txt, font=fv,
                   fill=(9, 40, 25, 255) if win else (255, 255, 255, 255))
        return im

    def _verdict(self, text, name, side):
        R, Image, ImageDraw = self.R, self.Image, self.ImageDraw
        W = self.W
        im = Image.new("RGBA", (W, 300), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        x0, x1 = int(W * 0.06), int(W * 0.94)
        d.rounded_rectangle([x0, 0, x1, 280], radius=40,
                            fill=(255, 255, 255, 250) if side == 0 else R.ACCENT + (255,))
        d.ellipse([x0 + 26, 26, x0 + 100, 100], fill=(30, 110, 70, 255))
        d.text((x0 + 48, 34), "A" if side == 0 else "B", font=R._font(44), fill=(255, 255, 255, 255))
        f = R._font(int(W * 0.050))
        y = 30
        for line in R._wrap(d, R._strip_emoji(text), f, x1 - x0 - 160, 2):
            d.text((x0 + 125, y), line, font=f, fill=(14, 52, 33, 255))
            y += int(W * 0.060)
        f2 = R._font(int(W * 0.036), bold=False)
        y = max(y + 14, 150)
        for line in R._wrap(d, R._strip_emoji(name), f2, x1 - x0 - 60, 2):
            d.text((x0 + 30, y), line, font=f2, fill=(40, 80, 60, 255))
            y += int(W * 0.045)
        return im

    def _cta(self, d, im):
        R, W = self.R, self.W
        f = R._font(int(W * 0.070))
        for i, line in enumerate(["Narxi va buyurtma —", "Chinor do'konida"]):
            tw = d.textlength(line, font=f)
            d.text(((W - tw) / 2, i * int(W * 0.085)), line, font=f,
                   fill=(255, 255, 255, 255) if i == 0 else R.ACCENT + (255,))
        f2 = R._font(int(W * 0.042))
        sub = "Telegram: t.me/" + _channel_handle()
        tw = d.textlength(sub, font=f2)
        d.rounded_rectangle([(W - tw) / 2 - 30, int(W * 0.205), (W + tw) / 2 + 30, int(W * 0.205) + 74],
                            radius=37, fill=(255, 255, 255, 245))
        d.text(((W - tw) / 2, int(W * 0.205) + 14), sub, font=f2, fill=(16, 60, 38, 255))

    def _outro(self, d, im, store):
        """Do'kon sahnasi: raqamlar faqat jonli bazadan."""
        R, W = self.R, self.W
        y = 0
        if int(store.get("count") or 0) >= 100:
            f = R._font(int(W * 0.115))
            big = f"{store['count_txt']}+ tovar"
            tw = d.textlength(big, font=f)
            d.text(((W - tw) / 2, y), big, font=f, fill=(255, 255, 255, 255))
            y += int(W * 0.13)
            f2 = R._font(int(W * 0.050))
            sub = "sizni kutmoqda" + (f" · {store['cats']} bo'lim" if store.get("cats") else "")
            tw = d.textlength(sub, font=f2)
            d.text(((W - tw) / 2, y), sub, font=f2, fill=R.ACCENT + (255,))
            y += int(W * 0.09)
        f3 = R._font(int(W * 0.040), bold=False)
        for line in (f"Chinor · {store.get('location', '')}", str(store.get("hours", ""))):
            if line.strip(" ·"):
                tw = d.textlength(line, font=f3)
                d.text(((W - tw) / 2, y), line, font=f3, fill=(235, 245, 238, 255))
                y += int(W * 0.055)
        y += 18
        f4 = R._font(int(W * 0.044))
        pill = "Telegram: t.me/" + _channel_handle()
        tw = d.textlength(pill, font=f4)
        d.rounded_rectangle([(W - tw) / 2 - 32, y, (W + tw) / 2 + 32, y + 78], radius=39,
                            fill=(255, 255, 255, 245))
        d.text(((W - tw) / 2, y + 15), pill, font=f4, fill=(16, 60, 38, 255))

    def _wall(self, paths):
        """Haqiqiy tovar rasmlaridan kartochkalar (devor uchun)."""
        Image, ImageDraw = self.Image, self.ImageDraw
        out = []
        side = int(self.W * 0.29)
        for pth in paths:
            try:
                im = Image.open(pth).convert("RGB")
                im.thumbnail((side - 20, side - 20), Image.LANCZOS)
                card = Image.new("RGBA", (side, side), self.R.CARD_BG + (255,))
                card.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
                m = Image.new("L", (side, side), 0)
                ImageDraw.Draw(m).rounded_rectangle([0, 0, side - 1, side - 1], radius=28, fill=255)
                card.putalpha(m)
                out.append(card)
            except Exception:
                continue
        return out

    def subtitle(self, si: int, ci: int, n_words: int):
        """Joriy gapning subtitri — so'zlar diktor bilan birga paydo bo'ladi
        (ko'pchilik lentani ovozsiz ko'radi)."""
        key = (si, ci, n_words)
        got = self._sub_cache.get(key)
        if got is not None:
            return got
        R, Image, ImageDraw = self.R, self.Image, self.ImageDraw
        W = self.W
        words = self.sub_chunks[si][ci]
        full = " ".join(words)
        im = Image.new("RGBA", (W, 260), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        f = R._font(int(W * 0.046))
        lines = R._wrap(d, full, f, int(W * 0.84), 3)
        shown = n_words
        y = 20
        box_h = 30 + len(lines) * int(W * 0.058)
        d.rounded_rectangle([int(W * 0.05), 0, int(W * 0.95), box_h], radius=30, fill=(6, 20, 13, 205))
        for line in lines:
            lw = line.split()
            vis, shown = lw[:max(0, shown)], shown - len(lw)
            x = int(W * 0.08)
            for j, w in enumerate(lw):
                col = (255, 255, 255, 255) if j < len(vis) else (255, 255, 255, 70)
                d.text((x, y), w, font=f, fill=col)
                x += d.textlength(w + " ", font=f)
            y += int(W * 0.058)
        if len(self._sub_cache) > 60:
            self._sub_cache.clear()
        self._sub_cache[key] = im
        return im


def _channel_handle() -> str:
    try:
        from bot.config import CHANNEL_ID
        ch = str(CHANNEL_ID or "")
        if ch.startswith("@"):
            return ch[1:]
    except Exception:
        pass
    return "Chinors"


def _frame(L: _Layers, t: float, segs: list, times: list, total: float, n_rows: int):
    R, Image = L.R, L.Image
    W, H = L.W, L.H
    e, seg, ab = R._ease_out, R._seg, R._ease_out_back
    tv = t - LEAD                                       # diktor vaqti
    si = 0
    for i, (a, b) in enumerate(times):
        if tv >= a - 0.05:
            si = i
    kind = segs[si]["kind"]
    s_start = times[si][0] + LEAD
    first_row = next((i for i, s in enumerate(segs) if s["kind"] == "row"), 2)
    first_ver = next((i for i, s in enumerate(segs) if s["kind"] == "verdict"), len(segs) - 3)
    cta_i = len(segs) - 1

    fr = L.bg.copy()
    # Fon doim harakatda: yorug' dog' sekin aylanadi — kadr hech qachon qotmaydi
    gx = int(W * 0.5 + W * 0.22 * math.sin(t * 0.45)) - L.glow.width // 2
    gy = int(H * 0.42 + H * 0.10 * math.cos(t * 0.33)) - L.glow.height // 2
    fr.alpha_composite(L.glow, (gx, gy))
    fr.alpha_composite(L.head, (0, int(H * 0.04)))
    # Jarayon chizig'i — qancha qolganini ko'rsatadi (oxirigacha ko'rishga undaydi)
    bar = L.ImageDraw.Draw(fr)
    bar.rectangle([0, 0, W, 10], fill=(255, 255, 255, 40))
    bar.rectangle([0, 0, int(W * min(1.0, t / total)), 10], fill=R.ACCENT + (255,))

    if si == 0:                                         # ILGAK — 1-kadrdan ko'rinadi
        pulse = 1.0 + 0.025 * math.sin(t * 2.4)
        k_in = 0.85 + 0.15 * e(seg(t, 0.0, 0.35))
        lay = L.hook.resize((int(W * pulse), int(L.hook.height * pulse)))
        fr.alpha_composite(R._alpha(lay, k_in), ((W - lay.width) // 2, int(H * 0.30) - int(30 * (1 - k_in))))
        # Ikki tovar pastdan "mo'ralaydi" — nima haqida ekani darhol ko'rinsin
        for i, card in enumerate(L.cards):
            c = card.resize((int(card.width * 0.55), int(card.height * 0.55)))
            x = (W // 2 - c.width - 25) if i == 0 else (W // 2 + 25)
            fr.alpha_composite(c, (x, int(H * 0.66) + int(10 * math.sin(t * 2 + i * 1.7))))

    elif si < first_ver:                                # ZIDDIYAT + TAQQOSLASH
        in_rows = kind == "row"
        k = e(seg(t, s_start if si == 1 else times[1][0] + LEAD, 0.6))
        shrink = e(seg(t, times[first_row][0] + LEAD, 0.6)) if n_rows and si >= first_row else 0.0
        scale = 1.0 - 0.28 * shrink
        top = int(H * (0.22 - 0.08 * shrink))
        for i, card in enumerate(L.cards):
            c = card if scale > 0.999 else card.resize((int(card.width * scale), int(card.height * scale)))
            cx = (W // 4 if i == 0 else 3 * W // 4) - c.width // 2
            off = int((1 - k) * W * 0.6) * (-1 if i == 0 else 1)
            bob = int(7 * math.sin(t * 1.8 + i * 1.6))
            fr.alpha_composite(c, (cx + off, top + bob))
        kv = ab(seg(t, times[1][0] + LEAD + 0.3, 0.5))
        if kv > 0:
            pulse = 1.0 + 0.05 * math.sin(t * 3.0)
            sz = max(8, int(L.vs.width * kv * scale * pulse))
            vs = L.vs.resize((sz, sz))
            vy = top + int(L.cards[0].height * scale * 0.42) - vs.height // 2
            fr.alpha_composite(vs, (W // 2 - vs.width // 2, vy))
        if si == 1:
            kp = e(seg(t, s_start + 0.2, 0.5))
            fr.alpha_composite(R._alpha(L.problem, kp), (0, int(H * 0.60)))
        if in_rows:
            cur = segs[si]["i"]
            for i in range(cur + 1):
                rs = times[first_row + i][0] + LEAD
                kr = e(seg(t, rs, 0.45))
                # 4 qator subtitr qutisiga (0.82H) tegmasligi uchun zichroq
                y = int(H * 0.455) + i * 160 + int(40 * (1 - kr))
                lay = L.row_hi[i] if i == cur else L.rows[i]
                fr.alpha_composite(R._alpha(lay, kr * (1.0 if i == cur else 0.75)), (0, y))

    elif kind == "verdict":                             # XULOSA
        for i, v in enumerate(L.verdicts):
            vs_t = times[first_ver + i][0] + LEAD if first_ver + i < len(times) else s_start
            k = ab(seg(t, vs_t, 0.55))
            if k <= 0:
                continue
            x = int((1 - min(1.0, k)) * W * (1 if i else -1))
            active = segs[si]["i"] == i
            pulse = 1.0 + (0.02 * math.sin(t * 3.2) if active else 0)
            lay = v if abs(pulse - 1) < 0.003 else v.resize((int(v.width * pulse), int(v.height * pulse)))
            fr.alpha_composite(R._alpha(lay, 1.0 if active else 0.7),
                               (x - (lay.width - v.width) // 2, int(H * 0.26) + i * 330))

    elif kind == "outro":                               # DO'KON SAHNASI
        k = e(seg(t, s_start, 0.5))
        if L.wall:
            # Rasmlar devori pastdan tepaga suzadi: 3 ustun, har biri o'z
            # tezligida; ustun uzunligi ekrandan katta — tepadan chiqqan
            # kartochka pastdan qaytib kiradi (uzluksiz lenta)
            side, gap = L.wall[0].width, 22
            step = side + gap
            n = H // step + 2
            col_len = n * step
            col_x = [int(W * 0.035), (W - side) // 2, W - side - int(W * 0.035)]
            for ci in range(3):
                shift = (t - s_start) * (150 + ci * 50) + ci * 140
                for j in range(n):
                    y = int((j * step - shift) % col_len) - side // 2
                    card = L.wall[(ci * 5 + j) % len(L.wall)]
                    fr.alpha_composite(R._alpha(card, 0.9 * k), (col_x[ci], y))
            shade = Image.new("RGBA", (W, H), (8, 26, 17, int(150 * k)))
            fr.alpha_composite(shade)
        ko = ab(seg(t, s_start + 0.25, 0.6))
        fr.alpha_composite(R._alpha(L.outro, min(1.0, ko)),
                           (0, int(H * 0.27) + int(60 * (1 - min(1.0, ko)))))

    else:                                               # CHAQIRUV
        k = ab(seg(t, s_start, 0.6))
        fr.alpha_composite(R._alpha(L.cta, min(1.0, k)), (0, int(H * 0.24) + int(60 * (1 - min(1.0, k)))))
        for i, card in enumerate(L.cards):
            c = card.resize((int(card.width * 0.62), int(card.height * 0.62)))
            x = (W // 2 - c.width - 20) if i == 0 else (W // 2 + 20)
            fr.alpha_composite(R._alpha(c, min(1.0, k)), (x, int(H * 0.47) + int(9 * math.sin(t * 2 + i))))

    # SUBTITR — joriy gap, so'zlar diktor bilan birga ochiladi
    a, b = times[si]
    words = len(L.sub_words[si])
    frac = 1.0 if b <= a else min(1.0, max(0.0, (tv - a) / (b - a)))
    said = max(1, min(words, int(math.ceil(frac * words + 0.3))))   # nechta so'z aytildi
    ci = min(len(L.sub_chunks[si]) - 1, (said - 1) // 7)
    nw = said - ci * 7
    fr.alpha_composite(L.subtitle(si, ci, nw), (0, int(H * 0.82)))
    return fr.convert("RGB")


def render(topic: dict, rows: list, script: dict, times: list, out_path: str,
           voice_wav: str = "") -> str:
    """Bloklovchi. '' = muvaffaqiyat, aks holda xato matni."""
    from bot import reels as R
    if not R.is_available():
        return "ffmpeg o'rnatilmagan"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    segs = script["segments"]
    total = min(45.0, times[-1][1] + LEAD + TAIL)
    L = _Layers(topic, rows, script)
    music = out_path + ".m.wav"
    try:
        from bot import reels_audio
        has_music = reels_audio.build(music, total, cuts=[a + LEAD for a, _ in times[1:]],
                                      stamp=times[-1][0] + LEAD)
    except Exception:
        has_music = False
    audio = ""
    if voice_wav and os.path.exists(voice_wav):
        ms = int(LEAD * 1000)
        mix = out_path + ".mix.wav"
        if has_music:
            # Diktor ravshanlashtiriladi, musiqa diktor gapirganda pasayadi
            fc = (f"[1:a]{R.VOICE_FX},adelay={ms}|{ms},apad,asplit=2[v][vsc];"
                  f"[0:a]volume=0.30[m0];[m0][vsc]{R.DUCK}[m];"
                  "[m][v]amix=inputs=2:normalize=0:duration=first[a]")
            cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", music, "-i", voice_wav,
                   "-filter_complex", fc, "-map", "[a]", "-ar", "48000", mix]
        else:
            cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", voice_wav,
                   "-af", f"{R.VOICE_FX},adelay={ms}|{ms},apad=whole_dur={total}", "-ar", "48000", mix]
        if subprocess.run(cmd, capture_output=True, timeout=120).returncode == 0:
            audio = mix
    if not audio and has_music:
        audio = music
    a_in = ["-i", audio] if audio else ["-f", "lavfi", "-t", str(total), "-i", "anullsrc=r=44100:cl=stereo"]
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo",
           "-pix_fmt", "rgb24", "-s", f"{R.W}x{R.H}", "-r", str(FPS), "-i", "-", *a_in,
           *R.VIDEO_ENC, *R.AUDIO_ENC, "-t", f"{total:.2f}", "-movflags", "+faststart", out_path]
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE)
        for i in range(int(total * FPS)):
            proc.stdin.write(_frame(L, i / FPS, segs, times, total, len(rows)).tobytes())
        proc.stdin.close()
        if proc.wait(timeout=900) != 0:
            return "ffmpeg: " + proc.stderr.read().decode()[-300:]
    except Exception as e:
        return f"video yozilmadi: {e}"
    finally:
        for p in (music, out_path + ".mix.wav"):
            try:
                os.remove(p)
            except OSError:
                pass
    return "" if os.path.getsize(out_path) > 20000 else "video bo'sh chiqdi"


# ── Ishlab chiqarish va tasdiq ─────────────────────────────────────────────

def _caption(topic: dict, script: dict, pair: list) -> str:
    import html
    lines = [f"<b>{html.escape(script['hook'])}</b>", "", html.escape(script["problem"]), ""]
    for tag, s, verdict in (("A", pair[0], script["verdict_a"]), ("B", pair[1], script["verdict_b"])):
        p = s["p"]
        nm = (p.get("display_name") or "").strip() or p.get("name", "")
        price = f"{float(p['sell_price']):,.0f}".replace(",", " ")
        lines.append(f"<b>{tag}.</b> {html.escape(nm)} — {price} so'm\n   ➜ {html.escape(verdict)}")
    # SAVOL — matnning oxirgi, eng ko'zga tashlanadigan chaqirig'i.
    # 2026-10-23 o'lchovi: matni keng savol bilan boshlangan rolik 1486
    # ko'rish va 32 ulashish oldi, tor-texnik savolli esa 108 va 0.
    # Odam javob yozsa — Instagram rolikni ko'proq tarqatadi.
    lines += ["", "❓ Qaysi birini tanlagan bo'lardingiz — A yoki B? "
                  "Izohda yozing 👇",
              "", "📍 Chinor, Sergeli · buyurtma pastdagi tugma orqali",
              "#chinor #ustamaslahati #qurilish #toshkent"]
    return "\n".join(lines)[:1000]


async def produce(db, guide_id: int = 0) -> dict:
    """Mavzu → ssenariy → video → adminga tasdiq uchun. {ok, id|error}."""
    from bot import api_recommend as _R
    try:
        await _R._index(db)            # YesPOS savdo hisobini yangilaydi (buyers)
    except Exception:
        pass
    # Birinchi mavzuda fakt kam bo'lsa — keyingisini sinaymiz (ilgari birinchisi
    # yaramasa butun kun video chiqmasdi, 2026-10-20…22 shunday bo'ldi).
    # Bir nechta mavzu ko'rib chiqiladi va ENG KO'P faktli (qatorli) tanlanadi:
    # 2 qatorli taqqoslash zaif, 3–4 qatorlisi videoda ishonarli ko'rinadi.
    skip, topic, rows = set(), {}, []
    for _ in range(8):
        cand = await pick_topic(db, guide_id, skip)
        if not cand:
            break
        crow = build_rows(cand["pair"])
        if len(crow) > len(rows):
            topic, rows = cand, crow
        if guide_id or len(rows) >= MAX_ROWS:
            break
        skip.add(("g", cand["guide"]["id"]) if cand.get("guide") else ("c", cand.get("cid")))
    if not topic:
        return {"ok": False, "error": "Mos mavzu yo'q: nashr qilingan maslahat yoki yorliq "
                                       "kartochkasi bor kamida 2 tovar kerak"}
    if len(rows) < 2:
        return {"ok": False, "error": "Taqqoslash uchun fakt kam — tovarlarga yorliq o'qiting"}
    pids = ",".join(str(s["p"]["id"]) for s in topic["pair"])
    c = _conn()
    try:
        rid = c.execute("INSERT INTO reels_compare(guide_id, topic, pids, status) VALUES(?,?,?,?)",
                        ((topic.get("guide") or {}).get("id", 0), topic["topic"], pids, "rendering")).lastrowid
        c.commit()
    finally:
        c.close()
    loop = asyncio.get_event_loop()
    topic["store"] = await store_info(db)
    script = await loop.run_in_executor(None, make_script, topic, rows)
    from bot import reels as R
    os.makedirs(R.REELS_DIR, exist_ok=True)
    out = os.path.join(R.REELS_DIR, f"cmp_{rid}.mp4")
    voice = out + ".voice.wav"
    times = await loop.run_in_executor(None, build_voice, script, voice)
    has_voice = bool(times)
    if not has_voice:
        times = silent_times(script)       # ovozsiz: subtitr + musiqa
    err = await loop.run_in_executor(None, render, topic, rows, script, times, out,
                                     voice if has_voice else "")
    try:
        os.remove(voice)
    except OSError:
        pass
    if err:
        _set(rid, status="failed", error=err[:400])
        return {"ok": False, "error": err}
    caption = _caption(topic, script, topic["pair"])
    _set(rid, status="awaiting", video_path=out,
         script=json.dumps({"script": script, "rows": rows, "caption": caption}, ensure_ascii=False))
    await _send_for_approval(db, rid, out, caption, has_voice)
    return {"ok": True, "id": rid, "voice": has_voice}


async def _send_for_approval(db, rid: int, video: str, caption: str, voice: bool):
    bot = getattr(db, "_bot", None)
    if not bot:
        return
    from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
    from bot.config import GLAVNIY_ADMIN_ID
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Kanal + Instagram", callback_data=f"rcmp_ok_{rid}"),
        InlineKeyboardButton(text="❌ Bekor", callback_data=f"rcmp_no_{rid}"),
    ]])
    note = "" if voice else "\n\n🔇 Diktor ovozi chiqmadi (AI kvotasi) — subtitr va musiqa bilan."
    try:
        await bot.send_video(GLAVNIY_ADMIN_ID, FSInputFile(video),
                             caption=("🎬 <b>Kunlik video — tasdiqlang</b>\n\n" + caption)[:1000] + note,
                             parse_mode="HTML", reply_markup=kb, supports_streaming=True)
    except Exception as e:
        logger.warning("🎬 Taqqoslash videosi adminga ketmadi: %s", e)


async def publish(db, rid: int) -> str:
    """Kanalga chiqaradi. '' = muvaffaqiyat."""
    row = get(rid)
    if not row or not row.get("video_path") or not os.path.exists(row["video_path"]):
        return "Video topilmadi"
    if row.get("status") == "posted":
        return "Allaqachon joylangan"
    bot = getattr(db, "_bot", None)
    from bot.config import CHANNEL_ID
    if not bot or not CHANNEL_ID:
        return "Kanal sozlanmagan"
    data = json.loads(row.get("script") or "{}")
    from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
    buttons = []
    uname = getattr(db, "_bot_username", "")
    if uname and db.is_client_orders_enabled():
        for tag, pid in zip(("A", "B"), (row.get("pids") or "").split(",")):
            if pid.isdigit():
                buttons.append(InlineKeyboardButton(text=f"🛒 {tag} ni olish",
                                                    url=f"https://t.me/{uname}?start=buy_{pid}"))
    kb = InlineKeyboardMarkup(inline_keyboard=[buttons]) if buttons else None
    try:
        msg = await bot.send_video(CHANNEL_ID, FSInputFile(row["video_path"]),
                                   caption=data.get("caption", "")[:1024], parse_mode="HTML",
                                   reply_markup=kb, supports_streaming=True)
    except Exception as e:
        return f"Kanalga chiqmadi: {e}"
    _set(rid, status="posted", channel_msg=getattr(msg, "message_id", 0))
    # Instagram videoni o'zi yuklab, 1–2 daqiqa qayta ishlaydi — tugma javobi
    # kutib qolmasin (Telegram 15 soniyadan keyin «eskirgan so'rov» deydi).
    asyncio.get_event_loop().create_task(
        _publish_instagram(db, rid, row, data.get("caption", "")))
    return ""


def _ig_caption(html_caption: str) -> str:
    import html, re as _re
    txt = _re.sub(r"<[^>]+>", "", html_caption or "")
    txt = html.unescape(txt).replace("buyurtma pastdagi tugma orqali",
                                     "buyurtma — profildagi havola orqali")
    return txt[:2200]


async def _publish_instagram(db, rid: int, row: dict, caption: str) -> str:
    """Tasdiqlangan taqqoslash videosini Instagram Reels'ga ham qo'yadi."""
    from bot import reels as R
    if not R.ig_configured() or not R._flag_on(db, "reels_auto", "0"):
        logger.info("🎬 Taqqoslash #%s Instagram'ga yuborilmadi: %s", rid,
                    "Instagram sozlanmagan" if not R.ig_configured()
                    else "reels_auto=0 (o'chiq)")
        return ""
    url = R._cfg("PUBLIC_BASE_URL").rstrip("/") + "/api/reel/" + os.path.basename(row["video_path"])
    mid, err = await asyncio.get_event_loop().run_in_executor(
        None, lambda: R.publish_instagram(url, _ig_caption(caption)))
    if mid:
        logger.info("🎬 Taqqoslash videosi Instagram'da: #%s → %s", rid, mid)
        # Lentaga chiqqan rolik story'da ham ko'rinsin (ig_story_auto)
        from bot import ig_social
        await ig_social.maybe_story(db, url)
    else:
        logger.warning("🎬 Taqqoslash videosi Instagram'ga chiqmadi (#%s): %s", rid, err)
    bot = getattr(db, "_bot", None)
    if bot:
        from bot.config import GLAVNIY_ADMIN_ID
        try:
            await bot.send_message(GLAVNIY_ADMIN_ID, "📸 Instagram: " + (
                "taqqoslash videosi joylandi ✅" if mid else f"joylanmadi — {err}"[:300]))
        except Exception:
            pass
    return "" if mid else (err or "noma'lum xato")


def cleanup(keep_videos: int = 30, label_days: int = 21) -> int:
    """Disk asta to'lib bormasin: eski taqqoslash videolari (oxirgi
    `keep_videos` tasi qoladi) va `label_days` kundan beri tasdiqlanmagan
    yorliq suratlari o'chiriladi. O'chirilganlar sonini qaytaradi."""
    import shutil
    from bot import reels as R
    from database._helpers import DB_PATH
    gone = 0
    try:
        vids = sorted((os.path.join(R.REELS_DIR, f) for f in os.listdir(R.REELS_DIR)
                       if f.startswith("cmp_") and f.endswith(".mp4")),
                      key=os.path.getmtime, reverse=True)
        for p in vids[keep_videos:]:
            os.remove(p)
            gone += 1
    except FileNotFoundError:
        pass
    lbl = os.path.join(os.path.dirname(DB_PATH), "labels")
    cutoff = time.time() - label_days * 86400
    if os.path.isdir(lbl):
        for d in os.listdir(lbl):
            p = os.path.join(lbl, d)
            if os.path.isdir(p) and os.path.getmtime(p) < cutoff:
                shutil.rmtree(p, ignore_errors=True)
                gone += 1
    return gone


async def daily_loop(hour: int = None):
    """Belgilangan soatlarda taqqoslash videosi tayyorlab adminga yuboradi.

    2026-10-23 dan KUNIGA BIR NECHTA: o'lchov ko'rsatdiki, do'kon
    uchun ishlaydigan yagona ko'rinish shu — mahsulot reklamasi
    roliklari 15-108 ko'rish olgan, taqqoslash esa 1486 (32 ulashish).
    Soatlar `REELS_COMPARE_HOURS` bilan beriladi (sukut 10 va 17).
    Har soat uchun alohida belgi qo'yiladi, shuning uchun bot qayta
    ishga tushsa ham bir soatda ikki marta yasalmaydi.
    """
    from database.channel_db import db as _db
    if hour is not None:
        hours = [int(hour)]
    else:
        hours = [int(h) for h in os.getenv("REELS_COMPARE_HOURS", "10,17").split(",")
                 if h.strip().isdigit()] or [10]
    await asyncio.sleep(60)
    while True:
        try:
            now = time.localtime()
            today = time.strftime("%Y-%m-%d", now)
            on = str(_db.get_setting("reels_compare_enabled", "1")).strip() in ("1", "true", "on")
            if on and _db.is_reels_enabled():
                for h in hours:
                    key = "reels_compare_day_%d" % h
                    if now.tm_hour >= h and _db.get_setting(key, "") != today:
                        _db.set_setting(key, today)
                        res = await produce(_db)
                        logger.info("🎬 Taqqoslash videosi (%s:00): %s", h, res)
                        n = await asyncio.get_event_loop().run_in_executor(
                            None, cleanup)
                        if n:
                            logger.info("🧹 Eski fayllar o'chirildi: %d", n)
                        break          # bir siklda bittadan ortiq yasamaymiz
        except Exception:
            logger.exception("🎬 Kunlik video xatosi")
        await asyncio.sleep(600)
