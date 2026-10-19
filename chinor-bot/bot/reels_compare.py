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

DURATION = 23.0
FPS = 30
T_VS, T_ROWS, T_VERDICT, T_CTA = 3.0, 6.0, 15.0, 20.0
MAX_ROWS = 4
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
        scored.append({"p": p, "info": info, "per": per,
                       "facts": bool(info.get("facts")),
                       "rating": int(info.get("store_rating") or 0),
                       "img": bool(_img_path(p)), "fam": _family(p.get("name", ""))})
    if len(scored) < 2:
        return []
    # Faktlisi va rasmlisi oldinda
    scored.sort(key=lambda s: (-s["facts"], -s["img"], -s["rating"]))
    best = scored[0]
    others = [s for s in scored[1:] if s["fam"] != best["fam"]] or scored[1:]
    # Ikkinchisi — eng katta narx/sifat farqi (taqqoslash ma'noli bo'lsin)
    if best["per"] > 0:
        others.sort(key=lambda s: (-s["facts"], -abs((s["per"] or best["per"]) - best["per"])))
    pair = [best, others[0]]
    # Chapda «sifatli» (bahosi yuqori yoki qimmatroq), o'ngda «arzon»
    pair.sort(key=lambda s: (-s["rating"], -(s["per"] or float(s["p"]["sell_price"]))))
    return pair


async def pick_topic(db, guide_id: int = 0) -> dict:
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
        if pair:
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
            if (0, key) in recent:
                continue
            c = await db.get_category(cid)
            return {"guide": None, "pair": pair, "topic": f"{(c or {}).get('name', '')}: qaysi birini olish kerak?"}
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
    ra, rb = a["rating"], b["rating"]
    if ra and rb:
        add("Do'kon bahosi", "★" * ra, "★" * rb, "a" if ra > rb else ("b" if rb > ra else ""))
    add("Texnologiya", _fact(a["info"], "Texnologiya"), _fact(b["info"], "Texnologiya"))
    return rows


# ── Ssenariy (AI: faqat ilgak, xulosa, diktor) ─────────────────────────────

_SYSTEM = (
    "Sen Instagram Reels va Telegram uchun qisqa video ssenariy yozuvchisisan. "
    "Xo'jalik-qurilish do'koni Chinor (Toshkent, Sergeli). O'zbek tilida (lotin). "
    "Maqsad — odamni birinchi 2 soniyada to'xtatish: uning MUAMMOSI yoki qiziq "
    "savol bilan boshla (maqtov bilan emas). QOIDALAR: faqat berilgan "
    "faktlardan foydalan; mamlakat/texnologiya faqat faktda bo'lsa; 'eng zo'r', "
    "'hamma oladi' kabi tasdiqlanmagan gap yozma; do'kon bahosini 'xaridorlar "
    "bahosi' dema; emoji ishlatma. Faqat JSON."
)


def _script_prompt(topic: str, answer: str, pair: list, rows: list) -> str:
    def desc(s):
        p = s["p"]
        nm = (p.get("display_name") or "").strip() or p.get("name", "")
        facts = "; ".join(f"{f['k']}: {_short(f['v'], 80)}" for f in (s["info"].get("facts") or []))
        return (f"{nm} | narx {float(p['sell_price']):,.0f} so'm"
                + (f" | 1kg ≈ {s['per']:,.0f}" if s["per"] else "")
                + (f" | do'kon bahosi {s['rating']}/5" + (f" ({s['info'].get('store_rating_note')})"
                                                          if s['info'].get('store_rating_note') else "")
                   if s["rating"] else "")
                + (f" | {facts}" if facts else ""))
    return (
        f"Mavzu (savol): {topic}\n"
        + (f"Do'kon tasdiqlagan javob: {_short(answer, 700)}\n" if answer else "")
        + f"\nA (chapda): {desc(pair[0])}\nB (o'ngda): {desc(pair[1])}\n"
        + "Ekrandagi taqqoslash qatorlari: "
        + "; ".join(f"{r['label']}: A={r['a']}, B={r['b']}" for r in rows) + "\n\n"
        "ILGAK QOIDASI: savolni takrorlama — xaridorning AYNAN vaziyatini yoki og'rig'ini ayt "
        "(yomon: 'Qaysi emalni tanlash kerak?'; yaxshi: 'Darvozangiz bir yilda zanglab ketdimi?', "
        "'Bo'yoq 3 oyda ko'chib tushdimi?', 'Pasta tishni achishtiryaptimi?').\n"
        'JSON: {"hook": "ilgak — vaziyat/og\'riq savoli, 3-7 so\'z, savol belgisi bilan", '
        '"problem": "ziddiyat — 5-10 so\'z (masalan: Narxi 2 barobar farq qiladi — qaysi biri?)", '
        '"verdict_a": "A kimga/qaysi ishga mos — 3-7 so\'z", '
        '"verdict_b": "B kimga/qaysi ishga mos — 3-7 so\'z", '
        '"voice_1": "ilgak+ziddiyatni diktor o\'qiydi — 1-2 gap", '
        '"voice_2": "xulosa+chaqiruv — 2-3 gap, oxiri: Chinor do\'konida"}'
    )


def _parse(text: str):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    need = ("hook", "problem", "verdict_a", "verdict_b", "voice_1", "voice_2")
    if not isinstance(d, dict) or not all(str(d.get(k) or "").strip() for k in need):
        return None
    return {k: _short(d[k], 260 if k.startswith("voice") else 60) for k in need}


def make_script(topic: dict, rows: list) -> dict:
    """Bloklovchi (AI). AI ishlamasa — mavzudan oddiy zaxira ssenariy."""
    from bot import ai_engine
    g = topic.get("guide") or {}
    raw = ai_engine.chat(_script_prompt(topic["topic"], g.get("answer", ""), topic["pair"], rows),
                         _SYSTEM, 40, validate=lambda t: _parse(t) is not None)
    d = _parse(raw)
    if d:
        return d
    a = topic["pair"][0]["p"]; b = topic["pair"][1]["p"]
    return {"hook": _short(topic["topic"], 60), "problem": "Ikkalasi bor — farqi nimada?",
            "verdict_a": "Sifat muhim bo'lsa", "verdict_b": "Tejamkor tanlov",
            "voice_1": topic["topic"],
            "voice_2": "Farqini ko'rdingiz. Narxi va buyurtma — Chinor do'konida."}


# ── Chizish ────────────────────────────────────────────────────────────────

class _Layers:
    def __init__(self, topic: dict, rows: list, script: dict):
        from PIL import Image, ImageDraw
        from bot import reels as R
        self.R, self.Image, self.ImageDraw = R, Image, ImageDraw
        W, H = R.W, R.H
        self.W, self.H = W, H
        self.bg = R._gradient(W, H, R.BG_TOP, R.BG_BOTTOM).convert("RGBA")
        self.head = R._text_layer((W, int(H * 0.08)), self._head)
        self.hook = R._text_layer((W, int(H * 0.30)), lambda d, im: self._big(d, script["hook"], 0.098, 3))
        self.problem = R._text_layer((W, int(H * 0.22)), lambda d, im: self._big(d, script["problem"], 0.058, 3, R.ACCENT))
        self.cards = [self._card(s, i) for i, s in enumerate(topic["pair"])]
        self.vs = self._vs()
        self.rows = [self._row(r) for r in rows]
        names = [(s["p"].get("display_name") or s["p"].get("name") or "") for s in topic["pair"]]
        self.verdicts = [self._verdict(script["verdict_a"], names[0], 0),
                         self._verdict(script["verdict_b"], names[1], 1)]
        self.cta = R._text_layer((W, int(H * 0.30)), self._cta)

    # — qatlam chizuvchilar —
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
        box = cw - 50
        path = _img_path(s["p"])
        try:
            if path:
                im = Image.open(path).convert("RGBA")
                im.thumbnail((box, int(ch * 0.55)), Image.LANCZOS)
                card.alpha_composite(im, ((cw - im.width) // 2, 24 + (int(ch * 0.55) - im.height) // 2))
            else:
                f = R._font(int(cw * 0.30))
                d.text((cw // 2 - int(cw * 0.12), int(ch * 0.15)), "?", font=f, fill=(170, 200, 180, 255))
        except Exception:
            pass
        name = R._strip_emoji((s["p"].get("display_name") or "").strip() or s["p"].get("name", ""))
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

    def _row(self, r):
        R, Image, ImageDraw = self.R, self.Image, self.ImageDraw
        W = self.W
        h = 170
        im = Image.new("RGBA", (W, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        fl = R._font(int(W * 0.032), bold=False)
        tw = d.textlength(r["label"].upper(), font=fl)
        d.text(((W - tw) / 2, 0), r["label"].upper(), font=fl, fill=R.ACCENT + (255,))
        fv = R._font(int(W * 0.045))
        for side, val in ((0, r["a"]), (1, r["b"])):
            x0 = int(W * 0.05) if side == 0 else int(W * 0.52)
            x1 = int(W * 0.48) if side == 0 else int(W * 0.95)
            win = r["win"] == ("a" if side == 0 else "b")
            d.rounded_rectangle([x0, 50, x1, 150], radius=28,
                                fill=(R.ACCENT + (255,)) if win else (255, 255, 255, 38))
            lines = R._wrap(d, val, fv, x1 - x0 - 30, 1)
            txt = lines[0] if lines else val
            tw = d.textlength(txt, font=fv)
            d.text((x0 + (x1 - x0 - tw) / 2, 74), txt, font=fv,
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
        for line in R._wrap(d, R._strip_emoji(name), f2, x1 - x0 - 60, 2):
            d.text((x0 + 30, max(y + 14, 150)), line, font=f2, fill=(40, 80, 60, 255))
            y = max(y + 14, 150) + int(W * 0.045) - 14
        return im

    def _cta(self, d, im):
        R, W = self.R, self.W
        f = R._font(int(W * 0.070))
        for i, line in enumerate(["Narxi va buyurtma —", "Chinor do'konida"]):
            tw = d.textlength(line, font=f)
            d.text(((W - tw) / 2, i * int(W * 0.085)), line, font=f,
                   fill=(255, 255, 255, 255) if i == 0 else R.ACCENT + (255,))
        f2 = R._font(int(W * 0.036), bold=False)
        sub = "Sergeli · Telegram: Chinor Mini App"
        tw = d.textlength(sub, font=f2)
        d.text(((W - tw) / 2, int(W * 0.21)), sub, font=f2, fill=(220, 240, 228, 255))


def _frame(L: _Layers, t: float):
    R, Image = L.R, L.Image
    W, H = L.W, L.H
    e, seg, ab = R._ease_out, R._seg, R._ease_out_back
    fr = L.bg.copy()
    # Fon sekin "nafas oladi" — kadr hech qachon qotib qolmasin
    glow = int(18 + 10 * math.sin(t * 1.3))
    fr.alpha_composite(Image.new("RGBA", (W, H), (46, 106, 79, glow)))
    fr.alpha_composite(R._alpha(L.head, e(seg(t, 0.1, 0.5))), (0, int(H * 0.04)))

    if t < T_VS + 0.4:                                    # ILGAK
        k_in, k_out = ab(seg(t, 0.15, 0.6)), seg(t, T_VS - 0.2, 0.5)
        y = int(H * 0.30) + int(80 * (1 - k_in)) - int(120 * k_out)
        s = 1.0 + 0.03 * math.sin(t * 2.2)
        lay = L.hook if abs(s - 1) < 0.004 else L.hook.resize((int(W * s), int(L.hook.height * s)))
        fr.alpha_composite(R._alpha(lay, min(1.0, k_in) * (1 - k_out)), ((W - lay.width) // 2, y))
        kp = e(seg(t, 1.3, 0.5)) * (1 - k_out)
        if kp > 0:
            fr.alpha_composite(R._alpha(L.problem, kp), (0, int(H * 0.62)))

    if T_VS - 0.2 <= t < T_VERDICT:                       # KARTOCHKALAR
        k = e(seg(t, T_VS - 0.2, 0.7))
        shrink = e(seg(t, T_ROWS, 0.6))                   # qatorlar uchun tepaga ko'tariladi
        scale = 1.0 - 0.28 * shrink
        top = int(H * (0.25 - 0.10 * shrink))
        for i, card in enumerate(L.cards):
            c = card if scale > 0.999 else card.resize((int(card.width * scale), int(card.height * scale)))
            cx = (W // 4 if i == 0 else 3 * W // 4) - c.width // 2
            off = int((1 - k) * W * 0.6) * (-1 if i == 0 else 1)
            bob = int(6 * math.sin(t * 1.8 + i * 1.6))
            fr.alpha_composite(c, (cx + off, top + bob))
        kv = ab(seg(t, T_VS + 0.4, 0.5))
        if kv > 0:
            vs = L.vs.resize((max(8, int(L.vs.width * kv * scale)), max(8, int(L.vs.height * kv * scale))))
            vy = top + int(L.cards[0].height * scale * 0.42) - vs.height // 2
            fr.alpha_composite(vs, (W // 2 - vs.width // 2, vy))
        # Qatorlar birin-ketin
        n = max(1, len(L.rows))
        step = (T_VERDICT - T_ROWS - 0.6) / n
        for i, row in enumerate(L.rows):
            kr = e(seg(t, T_ROWS + 0.5 + i * step, 0.45))
            if kr <= 0:
                continue
            y = int(H * 0.52) + i * 190 + int(40 * (1 - kr))
            fr.alpha_composite(R._alpha(row, kr), (0, y))

    if T_VERDICT - 0.1 <= t < T_CTA + 0.3:                # XULOSA
        k_out = seg(t, T_CTA - 0.1, 0.4)
        for i, v in enumerate(L.verdicts):
            k = ab(seg(t, T_VERDICT + i * 0.9, 0.55))
            if k <= 0:
                continue
            x = int((1 - min(1.0, k)) * W * (1 if i else -1))
            fr.alpha_composite(R._alpha(v, (1 - k_out)), (x, int(H * 0.30) + i * 330))

    if t >= T_CTA:                                        # CHAQIRUV
        k = ab(seg(t, T_CTA, 0.6))
        fr.alpha_composite(R._alpha(L.cta, min(1.0, k)), (0, int(H * 0.36) + int(60 * (1 - min(1.0, k)))))
        for i, card in enumerate(L.cards):
            c = card.resize((int(card.width * 0.55), int(card.height * 0.55)))
            x = (W // 2 - c.width - 20) if i == 0 else (W // 2 + 20)
            fr.alpha_composite(R._alpha(c, min(1.0, k)), (x, int(H * 0.60) + int(8 * math.sin(t * 2 + i))))
    return fr.convert("RGB")


def _voice(script: dict, out_wav: str, tmp: str) -> bool:
    """Ikki bo'lak diktor: ilgak (0.4 s) va xulosa (T_VERDICT)."""
    try:
        from bot import tts
    except Exception:
        return False
    if not tts.available():
        return False
    parts = []
    for i, (at, txt) in enumerate(((0.4, script["voice_1"]), (T_VERDICT + 0.2, script["voice_2"]))):
        p = f"{tmp}.v{i}.wav"
        if i:
            time.sleep(2)
        if tts.speak(txt, p):
            parts.append((at, p, tts.duration(p)))
    if not parts:
        return False
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    chains, labels = [], []
    prev_end = 0.0
    for i, (at, p, dur) in enumerate(parts):
        start = max(at, prev_end + 0.15)
        room = DURATION - 0.2 - start
        tempo = min(1.35, dur / room) if room > 0 and dur > room else 1.0
        prev_end = start + dur / tempo
        cmd += ["-i", p]
        ms = int(start * 1000)
        sp = f"atempo={tempo:.3f}," if tempo > 1.001 else ""
        chains.append(f"[{i}:a]{sp}aresample=44100,adelay={ms}|{ms}[v{i}]")
        labels.append(f"[v{i}]")
    fc = ";".join(chains) + ";" + "".join(labels) + \
        f"amix=inputs={len(parts)}:normalize=0:duration=longest,apad,atrim=0:{DURATION}[a]"
    cmd += ["-filter_complex", fc, "-map", "[a]", "-ac", "1", "-ar", "44100", out_wav]
    try:
        ok = subprocess.run(cmd, capture_output=True, timeout=120).returncode == 0
    except Exception:
        ok = False
    for _, p, _ in parts:
        try:
            os.remove(p)
        except OSError:
            pass
    return ok and os.path.exists(out_wav)


def render(topic: dict, rows: list, script: dict, out_path: str, voice_wav: str = "") -> str:
    """Bloklovchi. '' = muvaffaqiyat, aks holda xato matni."""
    from bot import reels as R
    if not R.is_available():
        return "ffmpeg o'rnatilmagan"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    L = _Layers(topic, rows, script)
    music = out_path + ".m.wav"
    try:
        from bot import reels_audio
        has_music = reels_audio.build(music, DURATION,
                                      cuts=[T_VS, T_ROWS, T_VERDICT, T_CTA], stamp=T_CTA + 0.1)
    except Exception:
        has_music = False
    audio = ""
    if voice_wav and os.path.exists(voice_wav) and has_music:
        audio = out_path + ".mix.wav"
        r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", music,
                            "-i", voice_wav, "-filter_complex",
                            "[0:a]volume=0.20[m];[m][1:a]amix=inputs=2:normalize=0:duration=first[a]",
                            "-map", "[a]", "-ar", "44100", audio], capture_output=True, timeout=120)
        if r.returncode != 0:
            audio = voice_wav
    elif voice_wav and os.path.exists(voice_wav):
        audio = voice_wav
    elif has_music:
        audio = music
    a_in = ["-i", audio] if audio else ["-f", "lavfi", "-t", str(DURATION), "-i", "anullsrc=r=44100:cl=stereo"]
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo",
           "-pix_fmt", "rgb24", "-s", f"{R.W}x{R.H}", "-r", str(FPS), "-i", "-", *a_in,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", out_path]
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE)
        for i in range(int(DURATION * FPS)):
            proc.stdin.write(_frame(L, i / FPS).tobytes())
        proc.stdin.close()
        if proc.wait(timeout=600) != 0:
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
    lines += ["", "📍 Chinor, Sergeli · buyurtma pastdagi tugma orqali",
              "#chinor #ustamaslahati #qurilish #toshkent"]
    return "\n".join(lines)[:1000]


async def produce(db, guide_id: int = 0) -> dict:
    """Mavzu → ssenariy → video → adminga tasdiq uchun. {ok, id|error}."""
    topic = await pick_topic(db, guide_id)
    if not topic:
        return {"ok": False, "error": "Mos mavzu yo'q: nashr qilingan maslahat yoki yorliq "
                                       "kartochkasi bor kamida 2 tovar kerak"}
    rows = build_rows(topic["pair"])
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
    script = await loop.run_in_executor(None, make_script, topic, rows)
    from bot import reels as R
    os.makedirs(R.REELS_DIR, exist_ok=True)
    out = os.path.join(R.REELS_DIR, f"cmp_{rid}.mp4")
    voice = out + ".voice.wav"
    has_voice = await loop.run_in_executor(None, _voice, script, voice, out)
    err = await loop.run_in_executor(None, render, topic, rows, script, out, voice if has_voice else "")
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
        InlineKeyboardButton(text="✅ Kanalga joylash", callback_data=f"rcmp_ok_{rid}"),
        InlineKeyboardButton(text="❌ Bekor", callback_data=f"rcmp_no_{rid}"),
    ]])
    note = "" if voice else "\n\n🔇 Diktor ovozi chiqmadi (AI kvotasi) — faqat musiqa."
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
    return ""


async def daily_loop(hour: int = None):
    """Har kuni belgilangan soatda bitta video tayyorlab adminga yuboradi."""
    from database.channel_db import db as _db
    hour = int(os.getenv("REELS_COMPARE_HOUR", "10") if hour is None else hour)
    await asyncio.sleep(60)
    while True:
        try:
            now = time.localtime()
            today = time.strftime("%Y-%m-%d", now)
            done = _db.get_setting("reels_compare_day", "")
            on = str(_db.get_setting("reels_compare_enabled", "1")).strip() in ("1", "true", "on")
            if on and _db.is_reels_enabled() and done != today and now.tm_hour >= hour:
                _db.set_setting("reels_compare_day", today)
                res = await produce(_db)
                logger.info("🎬 Kunlik taqqoslash videosi: %s", res)
        except Exception:
            logger.exception("🎬 Kunlik video xatosi")
        await asyncio.sleep(600)
