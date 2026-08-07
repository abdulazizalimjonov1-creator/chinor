"""«Qanday ishlatiladi» — tovar haqidagi ma'lumotni INTERNETDAN topib beradi.

NIMA UCHUN: kanal postida narx, qoldiq va rasm bor, lekin xaridor ko'pincha
boshqa narsani so'raydi — "bu nimaga kerak, qanday ishlataman?". Do'konda bu
savolga sotuvchi og'zaki javob beradi; kanalda esa hech kim javob bermaydi va
odam so'ramasdan ketadi.

QAYERDAN OLINADI: tovar nomi bo'yicha GOOGLE'dan izlanadi (Gemini'ning
qidiruv/grounding imkoniyati), topilgani esa qisqa ikki tilli qo'llanmaga
aylantiriladi. Qidiruv ishlamasa — DuckDuckGo natijalari matn sifatida modelga
beriladi.

MUHIM: internetdan HECH NARSA topilmasa bo'sh qaytadi va postga hech narsa
qo'shilmaydi. Modelning o'z xotirasidan yozishiga YO'L QO'YILMAYDI: do'kondagi
nomlar mahalliy ("Newcoss Antijir"), model ularni bilmaydi va bilmagan
tovariga ishonch bilan yolg'on qo'llanma yozib beradi. Noto'g'ri ko'rsatma
esa kimningdir narsasini buzadi — bo'sh qolgani afzal.

Natija bir marta hisoblanadi va `products.usage_info` da saqlanadi.

Bloklovchi (tarmoqqa chiqadi): aiohttp ichida `run_in_executor` bilan chaqiring.
"""

from __future__ import annotations

import html
import logging
import os
import re
import time
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120 Safari/537.36")

MAX_LINE = 200         # bitta qator uzunligi (Telegram'da chiroyli tursin)
MAX_TOTAL = 2600       # izoh xabari — Telegram chegarasi 4096, zaxira bilan

# Model javobi SHU shaklda so'raladi. Erkin matn so'ralsa har model o'z
# uslubida (markdown sarlavha, raqamlangan ro'yxat, ***) yozadi va Telegram
# HTML'ida chalkash chiqadi. Yorliqli qatorlarni esa o'zimiz qayta teramiz.
_FORMAT = (
    "UZ_NIMA: <bir jumla — bu tovar nima va nimaga kerak>\n"
    "UZ_QADAM: <1-qadam — aniq harakat>\n"
    "UZ_QADAM: <2-qadam>\n"
    "UZ_QADAM: <3-qadam>\n"
    "UZ_QADAM: <4-qadam>\n"
    "UZ_MASLAHAT: <amaliy maslahat: miqdor, vaqt yoki tejash>\n"
    "UZ_MASLAHAT: <yana bir maslahat: qaysi yuzada/holatda yaxshi ishlaydi>\n"
    "UZ_MASLAHAT: <ehtiyot chorasi: nimaga ISHLATILMAYDI yoki xavfsizlik>\n"
    "RU_NIMA: <o'sha birinchi jumlaning ruschasi>\n"
    "RU_QADAM: <1-qadamning ruschasi>\n"
    "RU_QADAM: <2-qadamning ruschasi>\n"
    "RU_QADAM: <3-qadamning ruschasi>\n"
    "RU_QADAM: <4-qadamning ruschasi>\n"
    "RU_MASLAHAT: <1-maslahatning ruschasi>\n"
    "RU_MASLAHAT: <2-maslahatning ruschasi>\n"
    "RU_MASLAHAT: <3-maslahatning ruschasi>\n"
)

# QADAMSIZ shakl — qidiruv natijalari kam bo'lganda ishlatiladi.
# Nima uchun: do'kon e'lonlaridan tovar NIMAGA kerakligi aniq bo'ladi, lekin
# qadamlar yozilmagan bo'ladi va model ularni o'ylab topadi. Sinovda kir
# yuvish kapsulasi uchun "ichimlik suvga bir kapsula soling" deb yozib qo'ydi.
# Shuning uchun manba kambag'al bo'lsa faqat "nimaga kerak" chiqadi.
_FORMAT_SHORT = (
    "UZ_NIMA: <bir-ikki jumla — bu tovar nima va nimaga kerak>\n"
    "RU_NIMA: <o'shaning ruschasi>\n"
)

_RULES = (
    "QOIDALAR:\n"
    "1. Faqat yuqoridagi yorliqlar bilan yoz — sarlavha, izoh, markdown yo'q.\n"
    "2. Har qator 8-16 so'z. ANIQ gapir: miqdor, vaqt, harorat, yuza turi.\n"
    "3. «Sifatli mahsulot», «ishonch bilan foydalaning» kabi bo'sh maqtov "
    "YOZMA — xaridor buni allaqachon o'qigan, unga AMALIY foyda kerak.\n"
    "4. Narx, do'kon nomi, telefon, havola yozma.\n"
    "5. Topilgan ma'lumotga TAYAN. Tovar nimaligi aniq bo'lmasa, faqat "
    "«BILMAYMAN» deb yoz — o'ylab topma.\n"
)


# ─── Rasmdan tovarni tanish ────────────────────────────────────────────────
# Do'kondagi nomlar ichki jargon: "Shlanka uchun perexadnik nabor 182787",
# "Bardak stakan komplekt XP-69-1". Bunday nom bilan internetdan izlash befoyda
# — hech narsa topilmaydi yoki butunlay boshqa narsa topiladi. RASM esa tovar
# nimaligini to'g'ridan-to'g'ri ko'rsatadi va qadoqdagi brend yozuvini ham
# o'qib bo'ladi. Shuning uchun avval "bu nima?" degan savolga rasm javob
# beradi, izlash esa o'sha javob bo'yicha ketadi.

_ID_PROMPT = (
    "Rasmda do'kondagi bitta mahsulot bor. Bu QANDAY mahsulot?\n"
    "Qadoqdagi brend nomini va yozuvni ham o'qi (lotin yoki kirill).\n"
    "Javob: BITTA qator, 3-9 so'z. Masalan:\n"
    "«NK Antijir — oshxona uchun yog' ketkazuvchi sprey»\n"
    "«suv shlangi uchun plastik o'tkazgich (perexodnik)»\n"
    "Boshqa hech narsa yozma."
)


# Rasm modeli o'rniga TIZIM javobini qaytarib yuborishi mumkin. Jonli sinovda
# bitta bepul model «User Safety: safe» deb javob berdi va u "tovar nomi"
# bo'lib qidiruvga ketib qolardi. Shunga o'xshash axlat javoblar rad etiladi.
_ID_JUNK = ("user safety", "safety:", "policy", "moderation", "flagged",
            "unsafe", "assistant", "response:", "output:", "n/a", "none",
            "cannot", "can't", "unable", "sorry", "as an ai", "i don't",
            "bilmayman", "aniqlab bo'lmadi", "rasm yo'q", "no image",
            "kechirasiz", "ko'ra olmay", "korolmay", "ko'rinmayapti")


def _id_ok(text: str) -> bool:
    t = (text or "").strip()
    if not (8 <= len(t) <= 160) or len(t.splitlines()) > 2:
        return False
    if not _script_ok(t):
        return False
    low = t.lower()
    if any(w in low for w in _ID_JUNK):
        return False
    words = [w for w in re.split(r"[^\w']+", t) if w]
    # Haqiqiy tavsifda kamida uch so'z va bitta "to'la" so'z bo'ladi
    # («sprey», «perexodnik»). Ikki so'zli yorliq — tavsif emas.
    return len(words) >= 3 and any(len(w) >= 5 for w in words)


def identify(image_path: str, name: str = "") -> str:
    """Rasmga qarab tovar turini aniqlaydi (bitta qator) yoki ''.

    Rasm modeli yiqilsa bo'sh qaytadi — u holda izlash eski usulda, tovar
    nomi bo'yicha ketaveradi. Bloklovchi.
    """
    from bot import ai_engine
    if not image_path or not os.path.isfile(image_path):
        return ""
    try:
        # allow_text_fallback=False — RASMNI ko'rmagan model "taniy" olmaydi,
        # u faqat nomdan taxmin qiladi va bizga aynan shundan qochish kerak.
        out = ai_engine.describe(image_path, _ID_PROMPT, name,
                                 validate=_id_ok, allow_text_fallback=False)
    except Exception as e:
        logger.info("📖 Rasmdan tanib bo'lmadi: %s", str(e)[:120])
        return ""
    out = re.sub(r"\s+", " ", (out or "").strip().strip("*_`\"' .")).strip()
    if not _id_ok(out):
        return ""
    logger.info("📖 Rasmdan: %s → %s", (name or "?")[:30], out[:70])
    return out


def _search_prompt(name: str, category: str = "", tags=None,
                   seen: str = "") -> str:
    hint = []
    if seen:
        # Rasmdan aniqlangani ENG ishonchli belgi — do'kon nomi noto'g'ri
        # yozilgan bo'lsa ham rasm yolg'on gapirmaydi.
        hint.append(f"rasmda ko'ringani: {seen}")
    if category:
        hint.append(f"bo'lim: {category}")
    if tags:
        hint.append("qayerda ishlatiladi: " + ", ".join(tags))
    extra = f" ({'; '.join(hint)})" if hint else ""
    return (
        f"Internetdan izlab top: «{name}»{extra}.\n"
        f"Do'kondagi nom noto'g'ri yoki qisqartma bo'lishi mumkin — "
        f"rasmda ko'ringaniga ishon.\n"
        f"Shu tovar (yoki aynan shu turdagi mahsulot) haqida quyidagilarni "
        f"aniqla: nima ekani, qaysi ishlar uchun, qanday tartibda "
        f"ishlatilishi, qancha miqdor/qancha vaqt kerakligi, qaysi yuza yoki "
        f"materialda yaxshi ishlashi, nimaga ishlatib BO'LMASLIGI va "
        f"xavfsizlik qoidasi.\n"
        f"Keyin xaridor uchun HAQIQIY, amaliy maslahat yoz — sotuvchi "
        f"do'konda og'zaki aytadigan darajada aniq.\n"
        f"Javob AYNAN shu shaklda bo'lsin:\n\n{_FORMAT}\n{_RULES}"
    )


def _rewrite_prompt(name: str, found: str, category: str = "") -> str:
    cat = f" (do'kon bo'limi: {category})" if category else ""
    return (
        f"Quyida «{name}»{cat} tovari haqida internetdan topilgan matn bor.\n\n"
        f"--- TOPILGAN MATN ---\n{found[:3000]}\n--- TUGADI ---\n\n"
        f"Shu matnga TAYANIB tovar nimaligini va nimaga kerakligini yoz. "
        f"Matnda yo'q narsani qo'shma, ishlatish qadamlarini O'YLAB TOPMA. "
        f"Matndan tovar nimaligi aniq bo'lmasa, faqat «BILMAYMAN» deb yoz.\n"
        f"Javob AYNAN shu shaklda bo'lsin:\n\n{_FORMAT_SHORT}\n{_RULES}"
    )


# ─── DuckDuckGo (Gemini qidiruvi ishlamaganda zaxira) ──────────────────────

_TAG_RE = re.compile(r"<[^>]+>")

# Tovar nomidagi «ma'nosiz» so'zlar — qidiruvda kalit bo'la olmaydi (o'lcham,
# qadoq, rang). Ular bo'yicha moslik tekshirilsa har qanday e'lon "mos"
# bo'lib chiqadi.
_NAME_STOP = {
    "dona", "gram", "gramm", "kg", "litr", "ltr", "sht", "pachka", "upak",
    "komplekt", "original", "yangi", "katta", "kichik", "oq", "qora",
    "sariq", "yashil", "qizil", "kok", "rangli", "size", "plus", "pro",
}

MIN_FOUND = 250        # topilgan matn shundan qisqa bo'lsa — ishonchsiz
DDG_TIMEOUT = 8
DDG_REST = 600         # javob bermasa shuncha soniya umuman bezovta qilinmaydi

_ddg_dead_until = 0.0  # DuckDuckGo qachongacha "o'lik" hisoblanadi


def _fetch(url: str, timeout: int = DDG_TIMEOUT) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def keywords(name: str) -> list:
    """Nomdagi «ushlab turadigan» so'zlar — natijani shu bo'yicha filtrlaymiz."""
    out = []
    for w in re.split(r"[^\w']+", (name or "").lower()):
        w = w.strip("'")
        if len(w) >= 4 and not w.isdigit() and w not in _NAME_STOP:
            out.append(w)
    return out[:4]


def ddg_snippets(query: str, keys=None, limit: int = 8) -> str:
    """DuckDuckGo natijalarining qisqa tavsiflari (bitta matn bo'lib).

    `keys` berilsa — faqat SHU so'zlar uchraydigan natijalar qoladi. Bu shart
    bo'lmasa qidiruv butunlay begona sahifalarni ham qaytaradi ("Newcoss
    Antijir" so'rovi CSS haqidagi maqolani ham berdi) va model o'sha
    aralashmadan noto'g'ri qo'llanma yozib qo'yadi.

    Kalitsiz ishlaydi. Bloklansa yoki shakli o'zgarsa — bo'sh matn.
    """
    global _ddg_dead_until
    if time.time() < _ddg_dead_until:
        return ""          # yaqinda javob bermagan — bekorga kutmaymiz
    q = urllib.parse.quote_plus(query)
    for url in (f"https://html.duckduckgo.com/html/?q={q}",
                f"https://lite.duckduckgo.com/lite/?q={q}"):
        try:
            page = _fetch(url)
        except Exception as e:
            # Bir marta yiqilsa — hammasi yiqiladi (IP bloklangan yoki tarmoq
            # yopiq). Har tovarga 8 marta 8 soniya kutish behuda: bir necha
            # daqiqaga butunlay tinch qo'yamiz.
            _ddg_dead_until = time.time() + DDG_REST
            logger.info("🔎 DuckDuckGo javob bermadi (%d daqiqa tinch "
                        "qo'yiladi): %s", DDG_REST // 60, str(e)[:100])
            continue
        parts = re.findall(
            r'class="result__snippet"[^>]*>(.*?)</a>', page, re.S)
        if not parts:            # lite ko'rinishi — tavsif oddiy <td> ichida
            parts = re.findall(r'class="result-snippet"[^>]*>(.*?)</td>',
                               page, re.S)
        out = []
        for p in parts[:limit]:
            t = html.unescape(_TAG_RE.sub(" ", p))
            t = re.sub(r"\s+", " ", t).strip()
            if len(t) <= 40:
                continue
            if keys and not any(k in t.lower() for k in keys):
                continue          # bu natija tovarimiz haqida emas
            out.append(t)
        if out:
            return "\n".join(out)
    return ""


# ─── Javobni tekshirish va terish ──────────────────────────────────────────

MAX_STEPS = 4          # nechta qadam ko'rsatiladi
MAX_TIPS = 3           # nechta maslahat


def _parse(raw: str) -> dict:
    """Yorliqli javobni bo'laklarga ajratadi.

    {uz, ru — nima uchun kerak; uz_steps, ru_steps — qadamlar;
     uz_tips, ru_tips — maslahat va ehtiyot choralari}
    """
    got = {"uz": "", "ru": "", "uz_steps": [], "ru_steps": [],
           "uz_tips": [], "ru_tips": []}
    limits = {"QADAM": (MAX_STEPS, "steps"), "MASLAHAT": (MAX_TIPS, "tips")}
    for line in (raw or "").splitlines():
        line = line.strip().strip("*_` ")
        # Model yorliqni qalin qilib yozishi mumkin ("**UZ_NIMA**: …") yoki
        # ro'yxat belgisi qo'yishi — ikkalasi ham qabul qilinadi.
        m = re.match(
            r"^[\s*#>\-•]*(UZ|RU)[ _-]*(NIMA|QADAM|MASLAHAT)[\s*]*[:：-]\s*(.+)$",
            line, re.I)
        if not m:
            continue
        lang, kind, text = m.group(1).lower(), m.group(2).upper(), m.group(3)
        # Model qadamni o'zi ham raqamlab yuborishi mumkin ("1. Silkiting") —
        # raqamni biz qo'yamiz, aks holda "1. 1. Silkiting" bo'lib chiqadi.
        text = re.sub(r"^\s*(?:\d{1,2}\s*[.)-]|[-*•])\s*", "", text)
        text = text.strip(" .;*_`\"'<>").strip()
        text = re.sub(r"\s+", " ", text)
        if not text or len(text) > MAX_LINE:
            continue
        if kind == "NIMA":
            if not got[lang]:
                got[lang] = text
            continue
        cap, suffix = limits[kind]
        key = f"{lang}_{suffix}"
        if len(got[key]) < cap and text.lower() not in [
                x.lower() for x in got[key]]:
            got[key].append(text)
    return got


def _cyrillic(s: str) -> int:
    return sum(1 for ch in s if "Ѐ" <= ch <= "ӿ")


def _script_ok(s: str) -> bool:
    """Matn faqat lotin/kirill (+ raqam, tinish belgisi) dan iboratmi.

    Kuchsiz bepul modellar ba'zan tillarni aralashtiradi — sinovda ruscha
    qatorga vetnamcha so'z («Не cần добавлять») tushib qoldi. Bunday matnni
    kanalga chiqarib bo'lmaydi.
    """
    for ch in s or "":
        if ch.isalpha() and not ("a" <= ch.lower() <= "z"
                                 or "Ѐ" <= ch <= "ӿ"):
            return False
    return True


def _ok(raw: str, steps: bool = True) -> bool:
    """Javob ishlatishga yaroqlimi — zanjir keyingi modelga o'tsinmi, shuni hal
    qiladi. Ikkala til ham bo'lishi shart: bitta tilli post yarim ishlangan
    ko'rinadi. `steps=False` — qadamlar talab qilinmaydi (qisqa shakl)."""
    if "BILMAYMAN" in (raw or "").upper():
        return False
    g = _parse(raw)
    if not (g["uz"] and g["ru"]):
        return False
    # To'liq shakl uchun kamida 3 qadam + 1 maslahat: xaridorga «haqiqiy»
    # foyda beradigan hajm shu. Ikki qatorlik javob buni uddalamaydi.
    if steps and not (len(g["uz_steps"]) >= 3 and len(g["ru_steps"]) >= 3
                      and g["uz_tips"] and g["ru_tips"]):
        return False
    if _cyrillic(g["ru"]) < 5:
        return False
    return all(_script_ok(t) for t in
               [g["uz"], g["ru"]] + g["uz_steps"] + g["ru_steps"]
               + g["uz_tips"] + g["ru_tips"])


def _ok_short(raw: str) -> bool:
    return _ok(raw, steps=False)


def _render(g: dict) -> str:
    """Telegram HTML bloki — post ostiga izoh bo'lib yoziladi."""
    def esc(s):
        return html.escape(s, quote=False)

    # Qadamlar bo'lmasa sarlavha ham boshqacha: "qanday ishlatiladi" deb
    # va'da berib, faqat tovar nimaligini yozish xaridorni aldagan bo'lardi.
    head = ("📖 <b>Qanday ishlatiladi / Как использовать</b>"
            if g["uz_steps"] else
            "📖 <b>Bu nima uchun kerak / Для чего это нужно</b>")
    lines = [head, ""]
    lines.append(f"🇺🇿 <b>{esc(g['uz'])}</b>")
    lines += [f"{i}. {esc(s)}" for i, s in enumerate(g["uz_steps"], 1)]
    lines += [f"💡 {esc(s)}" for s in g["uz_tips"]]
    lines.append("")
    lines.append(f"🇷🇺 <b>{esc(g['ru'])}</b>")
    lines += [f"{i}. {esc(s)}" for i, s in enumerate(g["ru_steps"], 1)]
    lines += [f"💡 {esc(s)}" for s in g["ru_tips"]]
    # Uzun bo'lsa BUTUN qatorlar tashlanadi. Belgilab kesilsa `&#39;` kabi
    # HTML belgisi yoki <b> tegi o'rtasidan kesilib, Telegram xabarni
    # umuman qabul qilmaydi.
    while len("\n".join(lines)) > MAX_TOTAL and len(lines) > 3:
        lines.pop()
    return "\n".join(lines).rstrip()


# ─── Asosiy funksiya ───────────────────────────────────────────────────────

def usage_guide(name: str, category: str = "", tags=None,
                image_path: str = "") -> str:
    """Tovar uchun «qanday ishlatiladi» bloki (Telegram HTML) yoki ''.

    Tartib: 0) RASMDAN tovar nimaligini aniqlash (nom jargon bo'lishi mumkin)
            1) Google qidiruvi bilan AI  2) DuckDuckGo matni + AI
            3) hech narsa topilmasa — bo'sh (post shunchaki qo'llanmasiz ketadi)
    """
    from bot import ai_engine

    name = (name or "").strip()
    if len(name) < 3 and not image_path:
        return ""
    tags = [t for t in (tags or []) if t]

    # 0) Rasm bo'lsa — avval "bu nima?" degan savolga rasm javob beradi.
    seen = identify(image_path, name) if image_path else ""

    # 1) Google qidiruvi — eng ishonchlisi, chunki model AYNAN shu tovarni
    #    izlab ko'radi va topganini yozadi.
    raw = ai_engine.web_search(_search_prompt(name, category, tags, seen),
                               validate=_ok)

    # 2) Qidiruv yo'q/ishlamadi (kvota!) — o'zimiz izlaymiz va TOPILGAN
    #    matnni modelga beramiz. Natijalar nom/rasm kalit so'zlari bo'yicha
    #    filtrlanadi.
    if not raw:
        keys = keywords(name) + keywords(seen)
        found = ""
        # Ikkitadan ortiq so'rov qilinmaydi: har biri ikki manzilga uriladi
        # va DuckDuckGo tez charchaydi. Rasmdan aniqlangani BIRINCHI bo'ladi —
        # do'kon nomi ("XP-2-5") bilan hech qachon hech narsa topilmaydi.
        queries = [f"{seen} qanday ishlatiladi"] if seen else []
        queries.append(f"{name} nima uchun ishlatiladi")
        for q in queries[:2]:
            part = ddg_snippets(q, keys)
            if part and part not in found:
                found = (found + "\n" + part).strip()
            if len(found) >= MIN_FOUND * 2:
                break
        # Kam ma'lumot = ko'p to'qima. Bunday holda qo'llanma yozilmaydi:
        # xato ko'rsatma xaridorning narsasini buzadi, bo'sh esa hech kimga
        # zarar qilmaydi.
        if len(found) < MIN_FOUND:
            logger.info("📖 «%s» haqida internetda yetarli ma'lumot yo'q "
                        "(%d belgi)", name[:40], len(found))
            return ""
        raw = ai_engine.chat(
            _rewrite_prompt(seen or name, found, category), validate=_ok_short)
        if not raw or not _ok_short(raw):
            return ""
        g = _parse(raw)
        # Model so'ralmagan bo'lsa ham qadam yozib yuborishi mumkin — u
        # manbadan emas, o'zidan chiqqan bo'ladi, shuning uchun tashlanadi.
        g["uz_steps"], g["ru_steps"] = [], []
        g["uz_tips"], g["ru_tips"] = [], []
        logger.info("📖 Qisqa ma'lumot (qidiruvsiz): %s", name[:40])
        return _render(g)

    if not _ok(raw):
        return ""

    text = _render(_parse(raw))
    logger.info("📖 Qo'llanma tayyor: %s (%d belgi)", name[:40], len(text))
    return text


# ─── Qisqa shakl: keyinroq to'ldirish uchun ────────────────────────────────
# Google qidiruvining bepul kvotasi tugagan paytda saqlangan matn faqat
# «bu nima uchun kerak» bo'ladi. Kvota tiklangach uni to'liq qo'llanmaga
# almashtirish kerak — shuning uchun qaysi biri qisqa ekanini bilib olamiz.

_SHORT_MARK = "Bu nima uchun kerak"


def is_short(text: str) -> bool:
    """Saqlangan matn qisqa (qidiruvsiz) shaklmi — qayta urinsa bo'ladimi."""
    return _SHORT_MARK in (text or "")


def backfill(limit: int = 20) -> int:
    """Rasmli tovarlarga qo'llanma yozadi/yangilaydi. Nechtasi yozilgani.

    Qo'llanmasi YO'Q va QISQA (kvota tugagan paytda yozilgan) tovarlar
    olinadi; to'liq qo'llanmasi borlariga tegilmaydi. Qo'lda ishga
    tushiriladi (kvota tiklangan kuni):

        cd /opt/chinor/chinor-bot
        DATA_DIR=/var/lib/chinor python -m bot.product_info --backfill 20

    Kanalga hech narsa yubormaydi — faqat bazaga yozadi. Post qayta e'lon
    qilinganda izoh o'sha yangilangan matn bilan chiqadi.
    """
    import sqlite3
    data_dir = os.getenv("DATA_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "data")
    conn = sqlite3.connect(os.path.join(data_dir, "pos.db"), timeout=20)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT p.id, p.name, p.display_name, COALESCE(p.usage_tags,'') tags, "
        "COALESCE(p.usage_info,'') info, COALESCE(c.name,'') cat, "
        "COALESCE(p.image_orig_url, p.image_url, '') img "
        "FROM products p LEFT JOIN categories c ON c.id = p.category_id "
        "WHERE p.is_active=1 AND COALESCE(p.image_url,'') <> '' "
        "ORDER BY p.id DESC").fetchall()
    uploads = os.path.join(data_dir, "uploads")
    done = 0
    for r in rows:
        if done >= limit:
            break
        info = r["info"].strip()
        if info and not is_short(info):
            continue           # to'liq qo'llanma bor — bekorga kvota yemaymiz
        name = (r["display_name"] or "").strip() or r["name"]
        tags = [t.strip() for t in r["tags"].split(",") if t.strip()]
        img = os.path.join(uploads, os.path.basename(r["img"].split("?")[0]))
        txt = usage_guide(name, r["cat"], tags,
                          img if os.path.isfile(img) else "")
        if not txt or (info and is_short(txt)):
            continue          # yaxshilanmadi — eskisi qolaveradi
        conn.execute("UPDATE products SET usage_info=? WHERE id=?",
                     (txt, r["id"]))
        conn.commit()
        done += 1
        logger.info("📖 #%s yangilandi (%s)", r["id"], name[:40])
    conn.close()
    return done


if __name__ == "__main__":
    import argparse
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO)
    # AI kalitlari `.env` da. Botning o'zi uni `bot.config` orqali o'qiydi;
    # bu skript alohida jarayon bo'lgani uchun o'zi o'qishi kerak — aks holda
    # hamma provayder "kalit yo'q" deb jim o'tkazib yuboriladi.
    from bot import config as _config       # noqa: F401  (load_dotenv)
    ap = argparse.ArgumentParser(
        description="Qo'llanmasi yo'q va qisqa tovarlarga qo'llanma yozadi")
    ap.add_argument("--backfill", type=int, default=20,
                    help="ko'pi bilan nechta tovarga (standart 20)")
    a = ap.parse_args()
    print("yozildi:", backfill(a.backfill))
