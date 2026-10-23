"""«Gapiradigan tovarlar» — ikki tovar (ko'z, qosh, og'iz) o'zaro bahslashadi.

G'oya (do'kon egasi, 2026-10-23): qimmatroq/sifatliroq tovar ko'proq
sotiladigan arzonrog'iga qovog'ini solib «nega odamlar seni ko'p oladi,
men sifatliroqman-ku?» deydi, arzoni kulib «sifatlisan, lekin arzon emassan,
narxingni tushir» deb javob beradi.

HALOLLIK: hazil — faqat haqiqiy raqamlar ustida. Juftlik shunday tanlanadi:
  * A — qimmatroq (narxi ≥ 1.2 × B), B — oxirgi 30 kunda KO'PROQ xaridda
    bo'lgan (YesPOS cheklari, `api_recommend._LAST_BUYERS`);
  * dialogdagi har bir raqam bazadan beriladi va tekshiriladi — AI o'zidan
    raqam qo'shsa, gap rad etilib zaxira matn ishlatiladi;
  * hech bir tovar yomonlanmaydi: ikkalasi ham o'z xaridoriga mos.

Video: foni kesilgan ikki tovar, ustiga multfilm yuz (ko'zlar bir-biriga
qaraydi, ko'z pirpiratadi, qosh kayfiyatga qarab, og'iz diktor ovoziga qarab
ochiladi), narx va «30 kunda N marta» belgisi, subtitr, oxirida do'kon.
Ovoz — Gemini TTS ikki xil ovozda. Tasdiq va joylash — `reels_compare`
oqimi orqali (Telegram tugmasi → kanal + Instagram).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import subprocess
import time

logger = logging.getLogger(__name__)

FPS = 30
LEAD = 0.4
OUTRO = 2.2
MAX_SPEECH = 22.0
NAMES = {"A": "Sifatli", "B": "Arzon"}
VOICES = {"Sifatli": os.getenv("TALK_VOICE_A", "Charon"),
          "Arzon": os.getenv("TALK_VOICE_B", "Puck")}
MOODS = ("angry", "sad", "laugh", "proud", "neutral")
COL = {"A": (196, 74, 52), "B": (36, 128, 74)}


# ── Raqamlar o'zbekcha (diktor ham, subtitr ham bir xil o'qisin) ──────────

def price_say(v: float) -> str:
    v = int(round(float(v or 0)))
    if v >= 1000:
        th, rest = divmod(v, 1000)
        return f"{th} ming" + (f" {rest}" if rest else "") + " so'm"
    return f"{v} so'm"


def _nums(text: str) -> set:
    return set(re.findall(r"\d+", text or ""))


# ── Juftlik ───────────────────────────────────────────────────────────────

def _orig_path(p: dict) -> str:
    from bot.web_api import UPLOAD_DIR
    for key in ("image_orig_url", "image_url"):
        u = (p.get(key) or "").strip()
        if u:
            path = os.path.join(UPLOAD_DIR, os.path.basename(u.split("?")[0]))
            if os.path.isfile(path):
                return path
    return ""


def short_name(p: dict) -> str:
    nm = (p.get("display_name") or "").strip() or p.get("name", "")
    words = re.sub(r"[^\w' -]", " ", nm).split()
    return " ".join(words[:3])


async def pick_pair(db) -> list:
    """[A, B] (dict: p, buyers, info) yoki []."""
    from bot import api_recommend as R
    from bot.api_guides import _family
    from bot import api_labels
    idx = await R._index(db)
    buyers = dict(R._LAST_BUYERS)
    try:
        recent = set(json.loads(db.get_setting("reels_talk_recent", "[]") or "[]"))
    except ValueError:
        recent = set()
    by_cat = {}
    for p in idx.items.values():
        if float(p.get("sell_price") or 0) <= 0 or not int(p.get("category_id") or 0):
            continue
        if p["id"] in recent or not _orig_path(p):
            continue
        by_cat.setdefault(int(p["category_id"]), []).append(p)
    best, best_s = None, -1e9
    for plist in by_cat.values():
        plist = sorted(plist, key=lambda p: -buyers.get(p["id"], 0))[:40]
        for b in plist:
            bb = buyers.get(b["id"], 0)
            if bb < 3:
                continue
            for a in plist:
                if a["id"] == b["id"] or _family(a["name"]) == _family(b["name"]):
                    continue
                pa, pb = float(a["sell_price"]), float(b["sell_price"])
                ba = buyers.get(a["id"], 0)
                if pa < 1.2 * pb or ba >= bb:
                    continue
                s = (bb - ba) + min(pa / pb, 3) * 2
                if s > best_s:
                    best, best_s = (a, b), s
    if not best:
        return []
    out = []
    for p in best:
        info = await db._in_thread(api_labels.public_info, db, p["id"]) or {}
        out.append({"p": p, "buyers": int(buyers.get(p["id"], 0)), "info": info})
    return out


def remember(db, pair: list) -> None:
    try:
        rec = json.loads(db.get_setting("reels_talk_recent", "[]") or "[]")
    except ValueError:
        rec = []
    rec = ([s["p"]["id"] for s in pair] + rec)[:40]
    db.set_setting("reels_talk_recent", json.dumps(rec))


# ── Ssenariy ──────────────────────────────────────────────────────────────

_SYS = ("Sen Instagram Reels uchun KULGILI qisqa sahna yozasan: ikki tovar "
        "multfilm qahramonidek gaplashadi. O'zbek tilida (lotin), sodda, jonli, "
        "ko'cha tilida, lekin hurmat bilan. Hech bir tovarni yomonlama — ikkalasi ham "
        "o'z xaridoriga mos. FAQAT berilgan raqamlardan foydalan, yangi raqam, "
        "mamlakat yoki xususiyat o'ylab topma. Emoji ishlatma. Faqat JSON.")


def _facts_txt(s: dict) -> str:
    f = "; ".join(f"{x['k']}: {x['v'][:60]}" for x in (s["info"].get("facts") or [])[:3])
    r = s["info"].get("store_rating")
    return ((f" | do'kon bahosi {r}/5" if r else "") + (f" | {f}" if f else ""))


def _prompt(pair: list) -> str:
    a, b = pair
    return (
        f"A ({NAMES['A']}) — {short_name(a['p'])}: narxi {price_say(a['p']['sell_price'])}, "
        f"oxirgi 30 kunda {a['buyers']} marta olishgan{_facts_txt(a)}\n"
        f"B ({NAMES['B']}) — {short_name(b['p'])}: narxi {price_say(b['p']['sell_price'])}, "
        f"oxirgi 30 kunda {b['buyers']} marta olishgan{_facts_txt(b)}\n\n"
        "Sahna: A qovog'ini solib B ga «nega odamlar seni ko'proq oladi, men "
        "sifatliroqman-ku» deb arazlaydi; B kulib «sifatlisan, lekin arzon emassan» "
        "deydi; oxirida yarashadilar va ikkalasi Chinor do'konida ekanini aytadilar.\n"
        "5–6 gap, navbatma-navbat, A boshlaydi. Har gap ≤ 11 so'z, hammasi ≤ 55 so'z. "
        "Raqamlarni AYNAN shunday yoz: narx '30 ming so'm' kabi, sotuv '12 marta'.\n"
        'JSON: {"hook": "ekran tepasidagi 3–6 so\'zli kulgili savol", '
        '"lines": [{"who": "A" yoki "B", "say": "gap", '
        '"mood": "angry|sad|laugh|proud|neutral"}]}'
    )


def _allowed_nums(pair: list) -> set:
    out = set()
    for s in pair:
        out |= _nums(price_say(s["p"]["sell_price"])) | {str(s["buyers"]), "30"}
        r = s["info"].get("store_rating")
        if r:
            out.add(str(r))
    return out


def fallback_script(pair: list) -> dict:
    a, b = pair
    pa, pb = price_say(a["p"]["sell_price"]), price_say(b["p"]["sell_price"])
    return {"hook": "Qimmat yoki arzon — kim yutadi?", "lines": [
        {"who": "A", "mood": "angry", "say": "Nega odamlar seni ko'proq oladi? Men sifatliroqman-ku!"},
        {"who": "B", "mood": "laugh", "say": f"Do'stim, sen sifatlisan, lekin {pa}!"},
        {"who": "B", "mood": "proud", "say": f"Men esa {pb}. Shu oyda {b['buyers']} marta olishdi."},
        {"who": "A", "mood": "sad", "say": f"Meni esa atigi {a['buyers']} marta..."},
        {"who": "A", "mood": "neutral", "say": "Mayli, sifat kerak bo'lsa — men, tejash kerak bo'lsa — sen."},
        {"who": "B", "mood": "laugh", "say": "Ikkalamiz ham Chinor do'konida, Sergelida!"},
    ]}


def make_script(pair: list) -> dict:
    from bot import ai_engine
    allowed = _allowed_nums(pair)
    try:
        raw = ai_engine.chat(_prompt(pair), system=_SYS,
                             validate=lambda t: '"lines"' in (t or ""))
        m = re.search(r"\{.*\}", raw or "", re.S)
        d = json.loads(m.group(0)) if m else {}
        lines = []
        for ln in d.get("lines") or []:
            who = str(ln.get("who", "")).upper()[:1]
            say = " ".join(str(ln.get("say", "")).split())
            mood = str(ln.get("mood", "neutral")).lower()
            if who not in ("A", "B") or not say or len(say.split()) > 14:
                raise ValueError("qator shakli")
            if not _nums(say) <= allowed:
                raise ValueError(f"o'ylab topilgan raqam: {say}")
            lines.append({"who": who, "say": say, "mood": mood if mood in MOODS else "neutral"})
        if not (4 <= len(lines) <= 7) or lines[0]["who"] != "A":
            raise ValueError("qatorlar soni")
        hook = " ".join(str(d.get("hook", "")).split())[:48] or "Qimmat yoki arzon?"
        if not _nums(hook) <= allowed:
            hook = "Qimmat yoki arzon?"
        return {"hook": hook, "lines": lines, "source": "ai"}
    except Exception as e:
        logger.info("🗣 AI dialogi rad etildi (%s) — zaxira matn", e)
        d = fallback_script(pair)
        d["source"] = "fallback"
        return d


# ── Ovoz va vaqt ──────────────────────────────────────────────────────────

def build_voice(script: dict, out_wav: str) -> list:
    from bot import tts
    from bot.reels_compare import _speech_spans, _align
    if not tts.available():
        return []
    lines = [(NAMES[l["who"]], l["say"]) for l in script["lines"]]
    if not tts.speak_dialog(lines, out_wav, VOICES):
        return []
    total = tts.duration(out_wav)
    if total > MAX_SPEECH:
        tempo = min(1.25, total / MAX_SPEECH)
        fast = out_wav + ".fast.wav"
        r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", out_wav,
                            "-af", f"atempo={tempo:.3f}", fast], capture_output=True, timeout=60)
        if r.returncode == 0:
            os.replace(fast, out_wav)
            total = tts.duration(out_wav)
    times = _align(script["lines"], _speech_spans(out_wav), total)
    return times if len(times) == len(script["lines"]) else []


def envelope(wav: str, n_frames: int) -> list:
    """Har kadr uchun ovoz balandligi 0..1 (og'iz ochilishi shunga qarab)."""
    import wave
    import numpy as np
    try:
        with wave.open(wav, "rb") as w:
            sr = w.getframerate()
            x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype("float32")
    except Exception:
        return [0.0] * n_frames
    hop = max(1, sr // FPS)
    out = []
    for i in range(n_frames):
        seg = x[i * hop:(i + 1) * hop]
        out.append(float(np.sqrt(np.mean(seg * seg))) if len(seg) else 0.0)
    top = float(np.percentile([v for v in out if v > 0] or [1.0], 95)) or 1.0
    env, prev = [], 0.0
    for v in out:
        cur = min(1.0, v / top)
        prev = 0.55 * cur + 0.45 * prev           # silliq, lekin tez
        env.append(prev)
    return env


# ── Chizish ───────────────────────────────────────────────────────────────

class _Stage:
    def __init__(self, pair, script, store):
        from PIL import Image, ImageDraw, ImageFilter
        from bot import reels as R, imageproc
        self.W, self.H = R.W, R.H
        W, H = self.W, self.H
        bg = Image.new("RGB", (W, H))
        top, bot = (236, 246, 238), (203, 228, 210)
        d = ImageDraw.Draw(bg)
        for y in range(H):
            k = y / H
            d.line([(0, y), (W, y)], fill=tuple(int(top[i] + (bot[i] - top[i]) * k) for i in range(3)))
        self.bg = bg.convert("RGBA")
        self.prod, self.box = {}, {}
        for tag, s, cx in (("A", pair[0], W * 0.27), ("B", pair[1], W * 0.73)):
            im = Image.open(_orig_path(s["p"])).convert("RGB")
            cut = imageproc.cutout(im) if imageproc.has_cutout() else None
            if cut is None:
                cut = im.convert("RGBA")
            bbox = cut.getchannel("A").getbbox() or (0, 0, cut.width, cut.height)
            cut = cut.crop(bbox)
            k = min(440 / cut.width, 620 / cut.height)
            cut = cut.resize((max(1, int(cut.width * k)), max(1, int(cut.height * k))))
            self.prod[tag] = cut
            self.box[tag] = (int(cx - cut.width / 2), int(1180 - cut.height), cut.width, cut.height)
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow)
        for tag in "AB":
            x, y, w, h = self.box[tag]
            sd.ellipse([x + w * 0.1, y + h - 18, x + w * 0.9, y + h + 26], fill=(20, 60, 35, 90))
        self.bg.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(14)))
        # Statik matnlar: ilgak, narx, sotuv belgisi, nomlar
        F = R._font
        st = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(st)
        self._center(d, script["hook"], 210, F(74), (24, 52, 34), W - 120)
        for tag, s in (("A", pair[0]), ("B", pair[1])):
            x, y, w, h = self.box[tag]
            cx = x + w / 2
            self._pill(d, cx, 1225, price_say(s["p"]["sell_price"]), F(46), COL[tag])
            self._center(d, short_name(s["p"]), 1300, F(34, False), (40, 60, 48), 460, cx)
            self._center(d, f"30 kunda: {s['buyers']} marta", 1350, F(32), COL[tag], 460, cx)
        self._center(d, "CHINOR · Sergeli", 1830, F(36), (36, 100, 60), W)
        self.static = st
        self.F = F
        # Yakuniy sahna
        out = Image.new("RGBA", (W, H), (19, 38, 27, 235))
        od = ImageDraw.Draw(out)
        self._center(od, "Ikkalasi ham", 760, F(70, False), (220, 240, 226), W - 100)
        self._center(od, "CHINOR do'konida", 860, F(92), (255, 255, 255), W - 100)
        n = int(store.get("count") or 0)
        sub = f"Sergeli · {store.get('count_txt', '')}+ tovar" if n >= 100 else "Sergeli"
        self._center(od, sub, 1000, F(48, False), (170, 220, 186), W - 100)
        self._center(od, str(store.get("hours", "")), 1075, F(40, False), (170, 220, 186), W - 100)
        self.outro = out

    @staticmethod
    def _center(d, text, y, font, fill, max_w, cx=540):
        words, lines, cur = text.split(), [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if d.textlength(t, font=font) <= max_w or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        for i, ln in enumerate(lines[:3]):
            tw = d.textlength(ln, font=font)
            d.text((cx - tw / 2, y + i * font.size * 1.15), ln, font=font, fill=fill)

    @staticmethod
    def _pill(d, cx, y, text, font, col):
        tw = d.textlength(text, font=font)
        d.rounded_rectangle([cx - tw / 2 - 26, y - 8, cx + tw / 2 + 26, y + font.size + 14],
                            radius=34, fill=col)
        d.text((cx - tw / 2, y), text, font=font, fill=(255, 255, 255))

    # Yuz: ko'zlar, qoshlar, og'iz
    def _face(self, d, tag, x, y, w, h, mood, talk, t, look):
        cx = x + w / 2
        r = max(30, int(min(w * 0.13, h * 0.09)))
        ey = y + h * 0.36
        blink = ((t + (0 if tag == "A" else 1.7)) % 3.4) < 0.13
        for side in (-1, 1):
            ex = cx + side * w * 0.17
            if blink:
                d.line([ex - r, ey, ex + r, ey], fill=(30, 30, 30), width=6)
            else:
                d.ellipse([ex - r, ey - r * 1.15, ex + r, ey + r * 1.15], fill=(255, 255, 255),
                          outline=(30, 30, 30), width=5)
                px = ex + look * r * 0.38
                d.ellipse([px - r * 0.48, ey - r * 0.48, px + r * 0.48, ey + r * 0.48], fill=(25, 25, 25))
                d.ellipse([px - r * 0.18 + r * 0.15, ey - r * 0.32, px + r * 0.12 + r * 0.15, ey - r * 0.02],
                          fill=(255, 255, 255))
            # Qosh: ichki uchi (yuz markaziga yaqin) kayfiyatga qarab
            by = ey - r * 1.55
            inner, outer = ex - side * r * 1.0, ex + side * r * 1.0
            dy_in = {"angry": r * 0.55, "sad": -r * 0.45, "laugh": -r * 0.3, "proud": -r * 0.35}.get(mood, 0)
            dy_out = {"laugh": -r * 0.3, "proud": -r * 0.1}.get(mood, 0)
            d.line([inner, by + dy_in, outer, by + dy_out], fill=(30, 30, 30), width=8)
        # Og'iz
        my = y + h * 0.6
        mw = w * 0.34
        if talk > 0.08:
            oh = 8 + talk * w * 0.2
            if mood in ("laugh", "proud"):
                d.chord([cx - mw / 2, my - oh * 0.4, cx + mw / 2, my + oh], 0, 180,
                        fill=(110, 20, 30), outline=(30, 30, 30), width=5)
            else:
                d.ellipse([cx - mw * 0.32, my - oh / 2, cx + mw * 0.32, my + oh / 2],
                          fill=(110, 20, 30), outline=(30, 30, 30), width=5)
        elif mood in ("angry", "sad"):
            d.arc([cx - mw / 2, my, cx + mw / 2, my + mw * 0.5], 200, 340, fill=(30, 30, 30), width=7)
        else:
            d.arc([cx - mw / 2, my - mw * 0.35, cx + mw / 2, my + mw * 0.15], 20, 160,
                  fill=(30, 30, 30), width=7)

    def frame(self, t, cur, env, lines, total):
        from PIL import Image, ImageDraw
        fr = self.bg.copy()
        moods = {"A": "neutral", "B": "neutral"}
        # Har tovarning kayfiyati — o'zining oxirgi gapi bo'yicha
        for i, ln in enumerate(lines):
            if i <= max(cur, 0):
                moods[ln["who"]] = ln["mood"]
        speaker = lines[cur]["who"] if 0 <= cur < len(lines) else ""
        layer = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        for tag in "AB":
            x, y, w, h = self.box[tag]
            prod = self.prod[tag]
            k = 1.0
            dy = 0
            if tag == speaker:
                k = 1.0 + 0.05 * env
                dy = -int(10 * abs(math.sin(t * 9)) * env)
            else:
                dy = int(3 * math.sin(t * 2.2 + (0 if tag == "A" else 1.5)))
            if k != 1.0:
                nw, nh = int(w * k), int(h * k)
                prod = prod.resize((nw, nh))
                x, y, w, h = x - (nw - w) // 2, y - (nh - h), nw, nh
            y += dy
            layer.alpha_composite(prod, (x, y))
            d = ImageDraw.Draw(layer)
            self._face(d, tag, x, y, w, h, moods[tag], env if tag == speaker else 0.0,
                       t, 1 if tag == "A" else -1)
        fr.alpha_composite(layer)
        fr.alpha_composite(self.static)
        # Subtitr
        if 0 <= cur < len(lines):
            ln = lines[cur]
            d = ImageDraw.Draw(fr)
            font = self.F(54)
            name = NAMES[ln["who"]].upper()
            words, rows, row = ln["say"].split(), [], ""
            for wd in words:
                tt = (row + " " + wd).strip()
                if d.textlength(tt, font=font) <= 900 or not row:
                    row = tt
                else:
                    rows.append(row)
                    row = wd
            rows.append(row)
            hbox = 70 + len(rows) * 66
            y0 = 1440
            d.rounded_rectangle([60, y0, 1020, y0 + hbox], radius=30, fill=(255, 255, 255, 240))
            d.rounded_rectangle([60, y0, 78, y0 + hbox], radius=8, fill=COL[ln["who"]])
            d.text((104, y0 + 14), name, font=self.F(30), fill=COL[ln["who"]])
            for i, rw in enumerate(rows[:3]):
                d.text((104, y0 + 54 + i * 66), rw, font=font, fill=(25, 35, 30))
        # Yakun
        if t > total - OUTRO:
            from bot import reels as R
            k = min(1.0, (t - (total - OUTRO)) / 0.35)
            fr.alpha_composite(R._alpha(self.outro, k))
        return fr.convert("RGB")


def render(pair, script, times, voice_wav, store, out_path) -> str:
    from bot import reels as R
    total = (times[-1][1] if times else 14.0) + LEAD + OUTRO
    n = int(total * FPS)
    try:
        stage = _Stage(pair, script, store)
    except Exception as e:
        return f"sahna tayyorlanmadi: {e}"
    shifted = [(a + LEAD, b + LEAD) for a, b in times] if times else []
    env = [0.0] * int(LEAD * FPS) + (envelope(voice_wav, n) if voice_wav else [0.0] * n)
    # Musiqa + diktor (musiqa diktor ostida pasayadi), yakuniy mastering — reels'da
    music = out_path + ".music.wav"
    try:
        from bot import reels_audio
        has_music = reels_audio.build(music, total, cuts=[a for a, _ in shifted[1:]],
                                      stamp=total - OUTRO)
    except Exception:
        has_music = False
    audio = ""
    if voice_wav and os.path.exists(voice_wav):
        ms = int(LEAD * 1000)
        mix = out_path + ".mix.wav"
        if has_music:
            fc = (f"[1:a]{R.VOICE_FX},adelay={ms}|{ms},apad,asplit=2[v][vsc];"
                  f"[0:a]volume=0.28[m0];[m0][vsc]{R.DUCK}[m];"
                  "[m][v]amix=inputs=2:normalize=0:duration=first[a]")
            cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", music,
                   "-i", voice_wav, "-filter_complex", fc, "-map", "[a]", "-ar", "48000", mix]
        else:
            cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", voice_wav, "-af",
                   f"{R.VOICE_FX},adelay={ms}|{ms},apad=whole_dur={total}", "-ar", "48000", mix]
        if subprocess.run(cmd, capture_output=True, timeout=120).returncode == 0:
            audio = mix
    if not audio and has_music:
        audio = music
    a_in = (["-i", audio] if audio else
            ["-f", "lavfi", "-t", str(total), "-i", "anullsrc=r=48000:cl=stereo"])
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo",
           "-pix_fmt", "rgb24", "-s", f"{R.W}x{R.H}", "-r", str(FPS), "-i", "-", *a_in,
           *R.VIDEO_ENC, *R.AUDIO_ENC, "-t", f"{total:.2f}", "-movflags", "+faststart", out_path]
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE)
        for i in range(n):
            t = i / FPS
            cur = -1
            for j, (a, b) in enumerate(shifted):
                if a <= t < b + 0.25:
                    cur = j
            proc.stdin.write(stage.frame(t, cur, env[i] if i < len(env) else 0.0,
                                         script["lines"], total).tobytes())
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


def _caption(pair, script) -> str:
    import html
    a, b = pair
    lines = [f"<b>{html.escape(script['hook'])}</b>", ""]
    for tag, s in (("😤", a), ("😄", b)):
        lines.append(f"{tag} {html.escape(short_name(s['p']))} — {price_say(s['p']['sell_price'])}"
                     f" (30 kunda {s['buyers']} marta olishdi)")
    lines += ["", "Sizchi — sifatmi yoki narx? Izohda yozing 👇", "",
              "📍 Chinor, Sergeli · buyurtma pastdagi tugma orqali",
              "#chinor #qurilish #toshkent #sergeli"]
    return "\n".join(lines)[:1000]


async def produce(db) -> dict:
    """Juftlik → dialog → ovoz → video → adminga tasdiq (reels_compare oqimi)."""
    from bot import reels as R, reels_compare as C
    pair = await pick_pair(db)
    if not pair:
        return {"ok": False, "error": "Mos juftlik yo'q (qimmatroq va ko'proq sotilgan arzonrog'i)"}
    loop = asyncio.get_running_loop()
    script = await loop.run_in_executor(None, make_script, pair)
    store = await C.store_info(db)
    os.makedirs(R.REELS_DIR, exist_ok=True)
    c = C._conn()
    try:
        rid = c.execute("INSERT INTO reels_compare(guide_id, topic, pids, status) VALUES(?,?,?,?)",
                        (0, "🗣 " + script["hook"], ",".join(str(s["p"]["id"]) for s in pair),
                         "rendering")).lastrowid
        c.commit()
    finally:
        c.close()
    out = os.path.join(R.REELS_DIR, f"talk_{rid}.mp4")
    voice = out + ".voice.wav"
    times = await loop.run_in_executor(None, build_voice, script, voice)
    err = await loop.run_in_executor(None, render, pair, script, times,
                                     voice if times else "", store, out)
    try:
        os.remove(voice)
    except OSError:
        pass
    if err:
        C._set(rid, status="failed", error=err[:400])
        return {"ok": False, "error": err}
    caption = _caption(pair, script)
    C._set(rid, status="awaiting", video_path=out,
           script=json.dumps({"script": script, "caption": caption}, ensure_ascii=False))
    remember(db, pair)
    await C._send_for_approval(db, rid, out, caption, bool(times))
    return {"ok": True, "id": rid, "voice": bool(times), "source": script.get("source"),
            "pair": [short_name(s["p"]) for s in pair]}
