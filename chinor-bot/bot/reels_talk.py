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


def real_photo(p: dict) -> str:
    """Faqat SHU tovarning O'Z surati bo'lsa — yo'lini qaytaradi.

    NEGA KERAK (2026-10-25, do'kon egasi aytdi): bazada 2162 tovar
    «rasmli» ko'rinadi, lekin ular atigi 138 ta faylga tayanadi —
    `type_udlinitel.jpg` kabi umumiy rasmlar hali suratga olinmagan
    tovarlarga AI tomonidan qo'yilgan. Natijada rolikda ikki qahramon
    bir xil shishada chiqib qolardi. O'z surati `prod_<id>_...` deb
    saqlanadi, shuning uchun faqat shunaqasini olamiz.
    """
    path = _orig_path(p)
    return path if os.path.basename(path).startswith("prod_") else ""


def short_name(p: dict) -> str:
    nm = (p.get("display_name") or "").strip() or p.get("name", "")
    words = re.sub(r"[^\w' -]", " ", nm).split()
    return " ".join(words[:3])


async def pick_pair(db) -> list:
    """[A, B] (dict: p, buyers, info) yoki [].

    2026-10-25 da kengaytirildi. Avval juftlik faqat «qimmatroq + arzonroq,
    arzoni ko'proq sotilgan» shartiga mos kelsa olinardi — shuning uchun
    doim tozalash vositalari chiqardi, klyuch, disk, stameska, bo'yoq kabi
    2000 dan ortiq rasmli tovar esa umuman ishlatilmasdi. Endi juftlik
    TURDOSHLIK bo'yicha olinadi («Klyuch oddiy 17mm» ↔ «Klyuch G 19mm»),
    sahna turi esa juftlikning o'z ma'lumotidan kelib chiqib tanlanadi.

    Tezlik: 2000+ tovarni ikkitalab solishtirish sekin, shuning uchun
    avval nomning birinchi uzun so'zi bo'yicha guruhlanadi va juft faqat
    guruh ichidan olinadi.
    """
    import random
    from bot import api_recommend as R
    from bot import api_labels
    idx = await R._index(db)
    buyers = dict(R._LAST_BUYERS)
    try:
        recent = set(json.loads(db.get_setting("reels_talk_recent", "[]") or "[]"))
    except ValueError:
        recent = set()

    pool = [p for p in idx.items.values()
            if float(p.get("sell_price") or 0) > 0 and p["id"] not in recent
            and real_photo(p)]
    groups = {}
    for p in pool:
        key = ""
        for tok in re.sub(r"[^\w' -]", " ", (p.get("display_name") or p["name"])).split():
            t = tok.lower().strip("'-")
            if len(t) >= 4 and not t[0].isdigit():
                key = t
                break
        if key:
            groups.setdefault(key, []).append(p)

    rnd = random.Random(time.strftime("%Y-%m-%d-%H"))
    cands = []
    for items in groups.values():
        if len(items) < 2:
            continue
        rnd.shuffle(items)
        for a in items[:9]:
            for b in items[:9]:
                if a["id"] >= b["id"]:
                    continue
                na = (a.get("display_name") or a["name"]).strip().lower()
                nb = (b.get("display_name") or b["name"]).strip().lower()
                if na == nb or real_photo(a) == real_photo(b):
                    continue
                ta, tb = idx.toks.get(a["id"], set()), idx.toks.get(b["id"], set())
                kin = len(ta & tb) / max(1, len(ta | tb))
                if kin < 0.2:
                    continue
                ba, bb = buyers.get(a["id"], 0), buyers.get(b["id"], 0)
                pa, pb = float(a["sell_price"]), float(b["sell_price"])
                olcham = bool(size_of(na)) and size_of(na) != size_of(nb)
                sc = (3 * kin + min(ba + bb, 8) * 0.6 + (1.5 if olcham else 0)
                      + (1.0 if audience(a) else 0)
                      + (0.8 if max(pa, pb) >= 1.25 * max(1.0, min(pa, pb)) else 0)
                      + rnd.random() * 1.2)
                cands.append((sc, a, b))
    if not cands:
        return []
    cands.sort(key=lambda x: -x[0])
    _, a, b = cands[0]
    if float(b["sell_price"]) > float(a["sell_price"]):      # A — qimmatrog'i
        a, b = b, a
    out = []
    for p in (a, b):
        info = await db._in_thread(api_labels.public_info, db, p["id"]) or {}
        out.append({"p": p, "buyers": int(buyers.get(p["id"], 0)), "info": info})
    return out


def remember(db, pair: list) -> None:
    try:
        rec = json.loads(db.get_setting("reels_talk_recent", "[]") or "[]")
    except ValueError:
        rec = []
    # 12 ta: o'z surati bor tovar atigi ~70 ta, 40 tasini "yaqinda ishlatilgan"
    # deb chetlab tursak juftlik qolmay, rolik umuman yasalmay qolardi.
    rec = ([s["p"]["id"] for s in pair] + rec)[:12]
    db.set_setting("reels_talk_recent", json.dumps(rec))


# ── Kim kim bilan gaplashadi ──────────────────────────────────────────────
#
# Do'kon egasi (2026-10-25): «faqat narx haqida gapiradigan bo'lmasin —
# klyuchlar, instrumentlar, bo'yoqlar ham suratga olinadi; tovar
# "meni tanimaysanmi", "hamma uy bekalarining tanlovi", "ustalarning
# tanlovi" degandek maqtansin».
#
# Shuning uchun sahna turi (ANGLE) tovarning O'Z ma'lumotidan kelib
# chiqib tanlanadi: nomidagi o'lcham, turi (asbob/uy/bo'yoq/avto),
# sotuv soni, narx farqi, birga olinishi. Har kuni boshqa tur chiqadi.

_TOOL = {"klyuch", "disk", "drel", "bolgarka", "stameska", "ugolnik", "qaychi",
         "bolt", "gayka", "otvertka", "bolga", "molotok", "arra", "pila",
         "mikser", "perforator", "sverlo", "ruletka", "kusachka", "napilnik",
         "plaskagubts", "zubilo", "domkrat", "svarka", "shurup", "dyubel",
         "lom", "tiski", "shtangen", "keska", "tros", "lenta"}
_HOME = {"tozalash", "yuvish", "kukun", "pasta", "sovun", "gel", "shampun",
         "idish", "savat", "qozon", "choynak", "salfetka", "gubka", "tarelka",
         "vanish", "fairy", "jit", "antijir", "nalyot", "krot", "yumosh",
         "kir", "oshxona", "hammom", "vanna", "polotence", "chelak"}
_PAINT = {"bo'yoq", "boyoq", "emal", "emultsiya", "lak", "gruntovka", "grunt",
          "shpaklyovka", "shpatlyovka", "kraska", "valik", "moyqalam", "kist",
          "pigment", "rastvoritel", "akril", "fasad", "shpatel"}
_AUTO = {"avto", "moshina", "salon", "shina", "antifriz", "maslo", "polish"}

_ROLE = {"usta": "ustalarning tanlovi", "bekasi": "uy bekalarining tanlovi",
         "boyoqchi": "pardozchilarning tanlovi", "haydovchi": "haydovchilarning tanlovi"}
_WORK = {"usta": "ishxonada", "bekasi": "uyda", "boyoqchi": "devorda", "haydovchi": "moshinada"}


def _words(*parts) -> set:
    return set(re.sub(r"[^\w']+", " ", " ".join(parts).lower()).split())


def audience(p: dict) -> str:
    """Tovar kimning qo'lida ishlaydi — nomi va teglaridan."""
    w = _words(p.get("display_name") or p.get("name", ""), p.get("usage_tags") or "")
    for key, bag in (("usta", _TOOL), ("boyoqchi", _PAINT),
                     ("haydovchi", _AUTO), ("bekasi", _HOME)):
        if w & bag:
            return key
    return ""


def size_of(name: str) -> str:
    """Nomidagi o'lcham: «Klyuch oddiy 17mm» → «17 mm»."""
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(mm|sm|cm|kg|gr|g|ml|l|m)\b", (name or "").lower())
    return f"{m.group(1)} {m.group(2)}" if m else ""


def brand_of(p: dict) -> str:
    """Nomidagi brend so'zi — raqam va o'lchov emas, birinchi uzun so'z."""
    nm = (p.get("display_name") or p.get("name", "")).strip()
    for w in re.sub(r"[^\w' -]", " ", nm).split():
        lw = w.lower().strip("'-")
        if len(lw) >= 3 and not lw.isdigit() and not re.match(r"^\d", lw) \
                and lw not in _TOOL and lw not in _HOME and lw not in _PAINT:
            return w.upper()
    return short_name(p).upper()


# ── Sahna turlari ─────────────────────────────────────────────────────────
# Har biri: qachon mos kelishi, ekran tepasidagi ilgak, AI uchun sahna
# tavsifi va AI ishlamay qolsa ishlatiladigan zaxira dialog.

def _angles(pair: list) -> list:
    a, b = pair
    pa, pb = float(a["p"]["sell_price"]), float(b["p"]["sell_price"])
    na, nb = a["buyers"], b["buyers"]
    an, bn = short_name(a["p"]), short_name(b["p"])
    ba, bb = brand_of(a["p"]), brand_of(b["p"])
    sa, sb = size_of(a["p"].get("display_name") or a["p"]["name"]), \
        size_of(b["p"].get("display_name") or b["p"]["name"])
    ra, rb = audience(a["p"]), audience(b["p"])
    out = []

    # 1. Brend g'ururi — «meni tanimaysanmi?»
    out.append({
        "id": "brend", "w": 3, "fit": True,
        "roles": (ba, bb),
        "hook": "Meni tanimaysanmi?",
        "scene": (f"A maqtanadi: «Meni tanimaysanmi? Men {ba}man» deydi va o'zini "
                  f"javonning eng mashhuri deb biladi. B kulib «tanidim, lekin odamlar "
                  f"meni ham olib ketishadi» deb javob qaytaradi. Oxirida ikkalasi "
                  f"bir-birining o'rni borligini tan oladi."),
        "lines": [
            {"who": "A", "mood": "proud", "say": f"Meni tanimaysanmi? Men {ba}man!"},
            {"who": "B", "mood": "laugh", "say": "Tanidim. Usta seni ko'rsa, hamyonini mahkam ushlaydi."},
            {"who": "A", "mood": "angry", "say": "Hazilni qo'y, men ishni puxta bajaraman."},
            {"who": "B", "mood": "neutral", "say": "To'g'ri. Men esa tez tugaydigan ishga yaxshiman."},
            {"who": "A", "mood": "laugh", "say": "Demak urushmaymiz — ish boshqa, hamyon boshqa."},
            {"who": "B", "mood": "proud", "say": "Ikkalamiz ham Chinorda, Sergelida!"},
        ]})

    # 2. Kimning tanlovi — usta / uy bekasi / pardozchi / haydovchi
    if ra and rb:
        out.append({
            "id": "tanlov", "w": 5 if ra != rb else 3,
            "fit": True, "roles": (ba, bb),
            "hook": "Kimning tanlovi kuchli?",
            "scene": (f"A o'zini {_ROLE[ra]} deb maqtaydi, B esa {_ROLE[rb]} ekanini "
                      f"aytadi. Ikkalasi kimning qo'li ko'p tegishini hazillashib "
                      f"bahslashadi, oxirida ikkala ish ham kerakligiga kelishadi."),
            "lines": [
                {"who": "A", "mood": "proud", "say": f"Men {_ROLE[ra]}man!"},
                {"who": "B", "mood": "laugh", "say": f"Men esa {_ROLE[rb]}man — menga har kuni qo'l tegadi."},
                {"who": "A", "mood": "angry", "say": "Menga ham tegadi... ta'mirdan ta'mirgacha."},
                {"who": "B", "mood": "neutral", "say": "Demak sen bayramchisan, men kunlikman."},
                {"who": "A", "mood": "laugh", "say": "Mayli: ish bo'lsa — men, uy bo'lsa — sen."},
                {"who": "B", "mood": "proud", "say": "Ikkalamiz Chinorda turibmiz!"},
            ]})

    # 3. O'lcham — klyuch, disk, ugolnik uchun eng tabiiy bahs
    if sa and sb and sa != sb:
        out.append({
            "id": "olcham", "w": 6, "fit": True, "roles": (ba, bb),
            "hook": "Qaysi o'lcham kerak?",
            "scene": (f"A — {sa}, B — {sb}. Ikkalasi «qaysi birimiz ko'proq ish "
                      f"qilamiz» deb bahslashadi: biri kichik ish uchun, biri "
                      f"kattasi uchun. Oxirida ustaning sumkasida ikkalasi ham "
                      f"yonma-yon turishini aytishadi."),
            "lines": [
                {"who": "A", "mood": "proud", "say": f"Men {sa}man — ishni men bitiraman."},
                {"who": "B", "mood": "laugh", "say": f"Men {sb}man. Kecha bitta boltga uch marta urinding."},
                {"who": "A", "mood": "angry", "say": "U bolt meniki emas edi, aybni menga to'nkama!"},
                {"who": "B", "mood": "neutral", "say": "Mana shuning uchun ikkalamiz ham kerakmiz."},
                {"who": "A", "mood": "laugh", "say": "Mayli, sumkada yonma-yon turamiz."},
                {"who": "B", "mood": "proud", "say": "Chinorda butun o'lcham bor!"},
            ]})

    # 4. Narx — faqat farq sezilarli bo'lsa
    if pa >= 1.25 * pb > 0:
        out.append({
            "id": "narx", "w": 4 if nb > na else 2, "fit": True, "roles": (ba, bb),
            "hook": "Qimmat yoki arzon?",
            "scene": ("A qimmatroq, B arzonroq. A «nega odamlar seni ko'proq oladi, "
                      "men sifatliroqman-ku» deb arazlaydi, B kulib «sifatlisan, lekin "
                      "arzon emassan» deydi. Oxirida yarashadilar."),
            "lines": [
                {"who": "A", "mood": "angry", "say": "Nega odamlar seni ko'proq oladi?"},
                {"who": "B", "mood": "laugh", "say": f"Chunki sen {price_say(pa)}san. Odam senga qarab o'ylanadi."},
                {"who": "B", "mood": "proud", "say": f"Men esa {price_say(pb)}."},
                {"who": "A", "mood": "sad", "say": "Men sifatliroqman-ku..."},
                {"who": "A", "mood": "neutral", "say": "Mayli: sifat kerakka — men, tejashga — sen."},
                {"who": "B", "mood": "laugh", "say": "Ikkalamiz Chinorda, Sergelida!"},
            ]})

    # 5. Sotuv — «meni necha marta olishdi»
    if nb >= 3 and nb > na:
        out.append({
            "id": "sotuv", "w": 4, "fit": True, "roles": (ba, bb),
            "hook": "Nega uni ko'p olishadi?",
            "scene": (f"A hayron: B ni oxirgi 30 kunda {nb} marta olishgan, A ni esa "
                      f"{na} marta. A sababini so'raydi, B hazil bilan tushuntiradi, "
                      f"oxirida A ham o'z xaridori borligiga ishonadi."),
            "lines": [
                {"who": "A", "mood": "sad", "say": f"Seni bu oyda {nb} marta olishdi."},
                {"who": "B", "mood": "laugh", "say": "Men tez ishlayman, usta meni sog'inadi."},
                {"who": "A", "mood": "angry", "say": f"Meni esa atigi {na} marta... hisobni yuritib o'tiribman."},
                {"who": "B", "mood": "neutral", "say": "Qo'rqma, sening xaridoring yo'lda."},
                {"who": "A", "mood": "laugh", "say": "Kutaman. Men sabrli tovarman."},
                {"who": "B", "mood": "proud", "say": "Ikkalamiz Chinorda kutamiz!"},
            ]})

    # 6. Hamkor — urushmaydi, birga ishlaydi
    out.append({
        "id": "hamkor", "w": 2, "fit": True, "roles": (ba, bb),
        "hook": "Ikkisi birga ishlaydi",
        "scene": (f"A va B urushmaydi: biri ikkinchisisiz ish bitmasligini aytadi. "
                  f"{an} va {bn} bir-birini maqtaydi va birga olinsa ish tez "
                  f"tugashini hazil bilan tushuntiradi."),
        "lines": [
            {"who": "A", "mood": "neutral", "say": "Sensiz mening ishim bitmaydi."},
            {"who": "B", "mood": "laugh", "say": "Men ham sensiz devorga qarab turaveraman."},
            {"who": "A", "mood": "proud", "say": "Birga olinsak, ish ikki barobar tez."},
            {"who": "B", "mood": "neutral", "say": "Usta buni allaqachon biladi."},
            {"who": "A", "mood": "laugh", "say": "Shuning uchun bitta paketda ketamiz."},
            {"who": "B", "mood": "proud", "say": "Chinorda, Sergelida — ikkalamiz!"},
        ]})
    return out


def pick_angle(pair: list, used: list) -> dict:
    """Mos sahnalardan birini tanlaydi — yaqinda ishlatilgani oxirida."""
    opts = _angles(pair)
    opts.sort(key=lambda a: (-(used.index(a["id"]) if a["id"] in used else 99), -a["w"]))
    return opts[0]


# ── Ssenariy ──────────────────────────────────────────────────────────────

_SYS = ("Sen Instagram Reels uchun KULGILI qisqa sahna yozasan: ikki tovar "
        "multfilm qahramonidek gaplashadi, bir-biriga hazil-huzul qiladi, "
        "latifa aytadi, arazlaydi va yarashadi. O'zbek tilida (lotin), sodda, "
        "jonli, ko'cha tilida, lekin hurmat bilan. Hech bir tovarni yomonlama — ikkalasi ham "
        "o'z xaridoriga mos. FAQAT berilgan raqamlardan foydalan, yangi raqam, "
        "mamlakat yoki xususiyat o'ylab topma. Emoji ishlatma. Faqat JSON.")


def _facts_txt(s: dict) -> str:
    f = "; ".join(f"{x['k']}: {x['v'][:60]}" for x in (s["info"].get("facts") or [])[:3])
    r = s["info"].get("store_rating")
    return ((f" | do'kon bahosi {r}/5" if r else "") + (f" | {f}" if f else ""))


def _prompt(pair: list, angle: dict) -> str:
    a, b = pair
    def one(tag, s):
        p = s["p"]
        bits = [f"narxi {price_say(p['sell_price'])}"]
        if s["buyers"]:
            bits.append(f"oxirgi 30 kunda {s['buyers']} marta olishgan")
        sz = size_of(p.get("display_name") or p["name"])
        if sz:
            bits.append(f"o'lchami {sz}")
        au = audience(p)
        if au:
            bits.append(_ROLE[au])
        return (f"{tag} ({brand_of(p)}) — {short_name(p)}: " + ", ".join(bits)
                + _facts_txt(s))
    return (one("A", a) + "\n" + one("B", b) + "\n\n"
            f"Sahna: {angle['scene']}\n"
            "5–6 gap, navbatma-navbat, A boshlaydi. Har gap ≤ 11 so'z, hammasi ≤ 55 so'z.\n"
            "SHART: gaplardan bittasi — qisqa HAZIL (latifa kabi, ustaning yoki "
            "uy bekasining kundalik hayotidan), lekin hech kimni kamsitmasin; "
            "hazil tovarning O'ZI ustidan bo'lsin. Kayfiyatlar xilma-xil bo'lsin: "
            "biri arazlasin, biri kulsin, oxirida yarashsin. "
            "Raqamlarni AYNAN shunday yoz: narx '30 ming so'm' kabi, sotuv '12 marta'.\n"
            'JSON: {"hook": "ekran tepasidagi 3–6 so\'zli kulgili savol", '
            '"lines": [{"who": "A" yoki "B", "say": "gap", '
            '"mood": "angry|sad|laugh|proud|neutral"}]}')


# «... uchun» gapida ruxsat etilgan umumiy so'zlar — bular tovar
# xususiyati emas, oddiy gap bo'laklari.
_SAFE_UCHUN = {"ish", "ishi", "siz", "sizlar", "sizning", "men", "mening",
               "sen", "sening", "bu", "shu", "o'sha", "uy", "uyda", "usta",
               "ustalar", "hamma", "kim", "nima", "pul", "tejash", "sifat",
               "bugun", "ertaga", "bayram", "mehmon", "bola", "bolalar"}

_MAMLAKAT = ("rossiya", "turkiya", "xitoy", "koreya", "germaniya", "yaponiya",
             "eron", "amerika", "polsha", "italiya", "fransiya", "hindiston")


def product_vocab(pair: list) -> set:
    """Tovarlar haqida bazada BOR so'zlar: nom, teglar, yorliq ma'lumoti."""
    vocab = set()
    for s in pair:
        p = s["p"]
        vocab |= _words(p.get("display_name") or p.get("name", ""),
                        p.get("usage_tags") or "",
                        re.sub(r"<[^>]+>", " ", p.get("usage_info") or ""),
                        p.get("usage_comment") or "",
                        p.get("description") or "")
        for f in (s["info"].get("facts") or []):
            vocab |= _words(str(f.get("k", "")), str(f.get("v", "")))
    return vocab


def unsupported_claim(vocab: set, text: str) -> str:
    """Bazada tasdig'i yo'q DA'VO bo'lsa — sababini qaytaradi, aks holda "".

    NEGA KERAK (2026-10-25): raqamlar allaqachon tekshirilardi, lekin
    XUSUSIYAT tekshirilmasdi — AI «Grass Azelit pro oshxona uchun» tovariga
    «Men grill uchun» dedi va bu Instagram'ga chiqib ketishiga oz qoldi.
    Shuning uchun «<nima> uchun» shaklidagi maqsad va mamlakat nomi faqat
    tovarning o'z ma'lumotida bo'lsa o'tadi.
    """
    low = (text or "").lower()
    for m in re.finditer(r"([\w']{3,})\s+uchun", low):
        w = m.group(1).strip("'")
        if w not in vocab and w not in _SAFE_UCHUN:
            return f"tasdiqlanmagan maqsad: «{w} uchun»"
    for c in _MAMLAKAT:
        if c in low and c not in vocab:
            return f"tasdiqlanmagan mamlakat: {c}"
    return ""


def _allowed_nums(pair: list) -> set:
    """AI o'ylab topgan raqam o'tmasin. Ruxsat: narx, sotuv soni, 30 kun
    va tovar NOMIDAGI raqamlar (klyuch 17mm, disk 125mm kabi)."""
    out = set()
    for s in pair:
        p = s["p"]
        out |= _nums(price_say(p["sell_price"])) | {str(s["buyers"]), "30"}
        out |= _nums(p.get("display_name") or p.get("name", ""))
        r = s["info"].get("store_rating")
        if r:
            out.add(str(r))
    return out


def _speaker_names(pair: list, angle: dict) -> dict:
    """Subtitr va diktor uchun ism.

    Brend nomi olinadi, lekin ikkala tovar bir brend bo'lsa («Grass azelit
    qozon» va «Grass Azelit gold») ikkalasi bir xil chiqib qolardi —
    shunda nomlarini FARQLAYDIGAN so'z qo'shiladi: GRASS QOZON / GRASS GOLD.
    """
    pa, pb = pair[0]["p"], pair[1]["p"]
    a, b = brand_of(pa), brand_of(pb)
    if a.lower() == b.lower():
        wa = short_name(pa).split()
        wb = short_name(pb).split()
        la = {w.lower() for w in wa}
        lb = {w.lower() for w in wb}
        da = next((w for w in wa if w.lower() not in lb), "")
        dbb = next((w for w in wb if w.lower() not in la), "")
        if not da or not dbb:                    # farq o'lchamda bo'lishi mumkin
            da = da or size_of(pa.get("display_name") or pa["name"])
            dbb = dbb or size_of(pb.get("display_name") or pb["name"])
        a, b = f"{a} {da}".strip(), f"{b} {dbb}".strip()
    a, b = a[:14].strip() or NAMES["A"], b[:14].strip() or NAMES["B"]
    if a.lower() == b.lower():
        a, b = NAMES["A"], NAMES["B"]
    return {"A": a, "B": b}


def fallback_script(pair: list, angle: dict = None) -> dict:
    angle = angle or _angles(pair)[0]
    return {"hook": angle["hook"], "lines": [dict(x) for x in angle["lines"]],
            "names": _speaker_names(pair, angle), "angle": angle["id"]}


def make_script(pair: list, angle: dict = None) -> dict:
    from bot import ai_engine
    angle = angle or _angles(pair)[0]
    names = _speaker_names(pair, angle)
    allowed = _allowed_nums(pair)
    vocab = product_vocab(pair)
    try:
        raw = ai_engine.chat(_prompt(pair, angle), system=_SYS,
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
            bad = unsupported_claim(vocab, say)
            if bad:
                raise ValueError(f"{bad} — «{say}»")
            lines.append({"who": who, "say": say, "mood": mood if mood in MOODS else "neutral"})
        if not (4 <= len(lines) <= 7) or lines[0]["who"] != "A":
            raise ValueError("qatorlar soni")
        hook = " ".join(str(d.get("hook", "")).split())[:48] or angle["hook"]
        if not _nums(hook) <= allowed or unsupported_claim(vocab, hook):
            hook = angle["hook"]
        return {"hook": hook, "lines": lines, "names": names,
                "angle": angle["id"], "source": "ai"}
    except Exception as e:
        logger.info("🗣 AI dialogi rad etildi (%s) — zaxira matn", e)
        d = fallback_script(pair, angle)
        d["source"] = "fallback"
        return d


# ── Ovoz va vaqt ──────────────────────────────────────────────────────────

def build_voice(script: dict, out_wav: str) -> list:
    from bot import tts
    from bot.reels_compare import _speech_spans, _align
    if not tts.available():
        return []
    nm = script.get("names") or NAMES
    voices = {nm["A"]: VOICES[NAMES["A"]], nm["B"]: VOICES[NAMES["B"]]}
    lines = [(nm[l["who"]], l["say"]) for l in script["lines"]]
    if not tts.speak_dialog(lines, out_wav, voices):
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
#
# NEGA BUNCHA HARAKAT. 2026-10-25 o'lchovi: statik taqqoslash roliklari
# 4–110 ko'rish olgan, gapiradigan tovarlar esa 1531 ko'rish va 32
# ulashish. Ya'ni Instagram'da ishlaydigan yagona narsa — tovarning
# JONLANIB qolishi. Shuning uchun har kadrda: nafas, qo'l harakati,
# gapirganda cho'zilish, gap boshida sakrash, kayfiyat belgilari (jahl
# bug'i, ko'z yoshi, uchqun) va so'zma-so'z yonadigan subtitr chiziladi.

PAD = 170                     # qo'l/effekt/aylanish uchun zaxira joy
ARM = (38, 40, 38)
GLOVE = (252, 252, 250)
GROUND = 1195                 # oyoqlar turadigan chiziq
FOOT = 64                     # tovar tagidagi oyoq balandligi
ENTER = 0.55                  # sahnaga chiqish


def _ease_out_back(k: float, s: float = 1.9) -> float:
    k = max(0.0, min(1.0, k)) - 1.0
    return k * k * ((s + 1) * k + s) + 1.0


def _lerp(a: float, b: float, k: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, k))


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
        bg = bg.convert("RGBA")
        # Sahna poli — yuqoridan nur tushgandek yorug' ellips
        floor = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(floor).ellipse([-140, 940, W + 140, 1430], fill=(255, 255, 255, 80))
        bg.alpha_composite(floor.filter(ImageFilter.GaussianBlur(70)))
        self.bg = bg

        self.prod, self.box, self.shadow, self.edge = {}, {}, {}, {}
        for tag, s, cx in (("A", pair[0], W * 0.27), ("B", pair[1], W * 0.73)):
            im = Image.open(_orig_path(s["p"])).convert("RGB")
            cut = imageproc.cutout(im) if imageproc.has_cutout() else None
            if cut is None:
                cut = im.convert("RGBA")
            bbox = cut.getchannel("A").getbbox() or (0, 0, cut.width, cut.height)
            cut = cut.crop(bbox)
            k = min(500 / cut.width, 690 / cut.height)
            cut = cut.resize((max(1, int(cut.width * k)), max(1, int(cut.height * k))))
            self.prod[tag] = cut
            px = cut.getchannel("A").load()
            prof = []
            for yy in range(cut.height):
                lo = hi = None
                for xx in range(cut.width):
                    if px[xx, yy] > 40:
                        if lo is None:
                            lo = xx
                        hi = xx
                prof.append((cut.width // 2 if lo is None else lo,
                             cut.width // 2 if hi is None else hi))
            self.edge[tag] = prof
            self.box[tag] = (int(cx - cut.width / 2), int(GROUND - FOOT - cut.height),
                             cut.width, cut.height)
            # Soya oldindan chiziladi: har kadrda blur qilish qimmat
            sw, sh = cut.width + 80, 150
            sd = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
            ImageDraw.Draw(sd).ellipse([40, 46, sw - 40, sh - 46], fill=(20, 60, 35, 110))
            self.shadow[tag] = sd.filter(ImageFilter.GaussianBlur(16))

        # Gapirayotganning orqasidagi yorug'lik
        g = Image.new("RGBA", (620, 620), (0, 0, 0, 0))
        ImageDraw.Draw(g).ellipse([70, 70, 550, 550], fill=(255, 255, 255, 135))
        self.glow = g.filter(ImageFilter.GaussianBlur(60))

        F = R._font
        self.F = F
        self.names = script.get("names") or NAMES
        hk = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        self._center(ImageDraw.Draw(hk), script["hook"], 196, F(78), (24, 52, 34), W - 120)
        self.hook = hk

        lb = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(lb)
        for tag, s in (("A", pair[0]), ("B", pair[1])):
            x, y, w, h = self.box[tag]
            cx = x + w / 2
            self._pill(d, cx, 1225, price_say(s["p"]["sell_price"]), F(46), COL[tag])
            self._center(d, short_name(s["p"]), 1300, F(34, False), (40, 60, 48), 460, cx)
            self._center(d, f"30 kunda: {s['buyers']} marta", 1350, F(32), COL[tag], 460, cx)
        self._center(d, "CHINOR · Sergeli", 1830, F(36), (36, 100, 60), W)
        self.labels = lb

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

    # ── Fon: sekin ko'tariladigan pufakchalar (ekran hech qachon qotib qolmaydi)
    def _bubbles(self, fr, t):
        from PIL import Image, ImageDraw
        lay = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        d = ImageDraw.Draw(lay)
        for i in range(7):
            r = 26 + (i * 13) % 48
            x = 70 + (i * 317) % (self.W - 140)
            y = self.H - ((t * (26 + i * 7) + i * 290) % (self.H + 300))
            x += 18 * math.sin(t * 0.7 + i)
            d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, 52))
        fr.alpha_composite(lay)

    # ── Oyoqlar: tovar tagida turadi, sakraganda yig'iladi
    def _legs(self, d, tag, x, y, w, h, t, lift):
        ph = 0.0 if tag == "A" else 1.1
        for i, side in enumerate((-1, 1)):
            lx = x + w / 2 + side * w * 0.20
            hip = y + h - 10
            step = math.sin(t * 5.2 + ph + i * math.pi) * (6 + 10 * lift)
            fy = y + h + FOOT - abs(step) * 0.5
            d.line([lx, hip, lx + step * 0.35, fy - 18], fill=ARM, width=24)
            fx = lx + step * 0.35
            d.ellipse([fx - 54, fy - 34, fx + 42, fy + 10],
                      fill=(32, 34, 32), outline=(12, 12, 12), width=4)

    # ── Qo'llar: yelka → tirsak → qo'lqop
    def _arms(self, d, tag, x, y, w, h, mood, talk, t):
        ph = 0.0 if tag == "A" else 1.4
        row = max(0, min(h - 1, int(h * 0.44)))
        lo, hi = self.edge[tag][row]
        for side in (-1, 1):
            sx = x + (lo - 2 if side < 0 else hi + 2)
            sy = y + h * 0.44
            near = (side > 0) if tag == "A" else (side < 0)    # sherigiga qaragan qo'l
            if mood == "laugh":
                hx, hy = 0.34, -0.20
            elif mood == "angry":
                hx, hy = 0.13, 0.26                            # beliga qo'l
            elif mood == "sad":
                hx, hy = 0.10, 0.44
            elif mood == "proud" and near:
                hx, hy = 0.30, -0.22
            elif near and talk > 0.05:
                hx, hy = 0.30 + 0.05 * talk, 0.06 - 0.26 * talk
            else:
                hx, hy = 0.22, 0.32
            hy += 0.016 * math.sin(t * 1.9 + ph + side)
            hand = (sx + side * hx * w, sy + hy * h)
            elbow = ((sx + hand[0]) / 2 + side * 0.10 * w,
                     (sy + hand[1]) / 2 + 0.07 * h)
            d.line([sx, sy, elbow[0], elbow[1]], fill=ARM, width=23)
            d.line([elbow[0], elbow[1], hand[0], hand[1]], fill=ARM, width=23)
            d.ellipse([elbow[0] - 11, elbow[1] - 11, elbow[0] + 11, elbow[1] + 11], fill=ARM)
            r = 34
            d.ellipse([hand[0] - r, hand[1] - r, hand[0] + r, hand[1] + r],
                      fill=GLOVE, outline=ARM, width=5)

    # ── Yuz: ko'zlar, qoshlar, og'iz
    def _face(self, d, tag, x, y, w, h, mood, talk, t, look):
        cx = x + w / 2
        r = max(34, int(min(w * 0.15, h * 0.10)))
        ey = y + h * 0.26
        blink = ((t + (0 if tag == "A" else 1.7)) % 3.4) < 0.13
        for side in (-1, 1):
            ex = cx + side * w * 0.17
            if mood == "laugh":
                d.arc([ex - r, ey - r * 0.7, ex + r, ey + r * 0.9], 200, 340,
                      fill=(30, 30, 30), width=8)
            elif blink:
                d.line([ex - r, ey, ex + r, ey], fill=(30, 30, 30), width=6)
            else:
                d.ellipse([ex - r, ey - r * 1.15, ex + r, ey + r * 1.15], fill=(255, 255, 255),
                          outline=(30, 30, 30), width=5)
                px = ex + look * r * 0.38
                py = ey + (r * 0.3 if mood == "sad" else 0)
                d.ellipse([px - r * 0.48, py - r * 0.48, px + r * 0.48, py + r * 0.48],
                          fill=(25, 25, 25))
                d.ellipse([px - r * 0.03, py - r * 0.32, px + r * 0.27, py - r * 0.02],
                          fill=(255, 255, 255))
                if mood == "sad":                       # yuqori qovoq tushadi
                    d.chord([ex - r, ey - r * 1.15, ex + r, ey + r * 1.15], 180, 360,
                            fill=(236, 240, 236), outline=(30, 30, 30), width=5)
            by = ey - r * 1.55
            inner, outer = ex - side * r * 1.0, ex + side * r * 1.0
            dy_in = {"angry": r * 0.55, "sad": -r * 0.45, "laugh": -r * 0.3,
                     "proud": -r * 0.35}.get(mood, 0)
            dy_out = {"laugh": -r * 0.3, "proud": -r * 0.1}.get(mood, 0)
            d.line([inner, by + dy_in, outer, by + dy_out], fill=(30, 30, 30), width=8)
        my = ey + r * 2.2
        mw = max(w * 0.30, r * 2.6)
        if talk > 0.08:
            oh = 22 + talk * r * 1.9
            box = ([cx - mw / 2, my - oh * 0.4, cx + mw / 2, my + oh]
                   if mood in ("laugh", "proud") else
                   [cx - mw * 0.36, my - oh / 2, cx + mw * 0.36, my + oh / 2])
            # Oq hoshiya — og'iz har qanday yorliq ustida ko'rinsin
            d.ellipse([box[0] - 5, box[1] - 5, box[2] + 5, box[3] + 5], fill=(252, 252, 250))
            if mood in ("laugh", "proud"):
                d.chord(box, 0, 180, fill=(132, 26, 38), outline=(30, 30, 30), width=6)
            else:
                d.ellipse(box, fill=(132, 26, 38), outline=(30, 30, 30), width=6)
        elif mood in ("angry", "sad"):
            d.arc([cx - mw / 2, my - 4, cx + mw / 2, my + mw * 0.55], 200, 340,
                  fill=(255, 255, 255), width=13)
            d.arc([cx - mw / 2, my - 4, cx + mw / 2, my + mw * 0.55], 200, 340,
                  fill=(30, 30, 30), width=8)
        else:
            d.arc([cx - mw / 2, my - mw * 0.35, cx + mw / 2, my + mw * 0.2], 20, 160,
                  fill=(255, 255, 255), width=13)
            d.arc([cx - mw / 2, my - mw * 0.35, cx + mw / 2, my + mw * 0.2], 20, 160,
                  fill=(30, 30, 30), width=8)

    # ── Kayfiyat belgilari: jahl bug'i, ko'z yoshi, kulgi, uchqun
    def _marks(self, d, tag, x, y, w, h, mood, t):
        cx = x + w / 2
        hy = y + h * 0.16
        if mood == "angry":
            bx, by = cx + w * 0.42, hy - 6
            s = 34 + 6 * math.sin(t * 9)
            col = (212, 56, 40)
            for ang in (0, 45, 90, 135, 180, 225, 270, 315):
                a = math.radians(ang)
                d.line([bx + math.cos(a) * s * 0.3, by + math.sin(a) * s * 0.3,
                        bx + math.cos(a) * s, by + math.sin(a) * s], fill=col, width=10)
        elif mood == "sad":
            k = (t * 1.2) % 1.0
            tx = cx + w * 0.17
            ty = y + h * 0.44 + k * h * 0.22
            d.ellipse([tx - 16, ty - 22, tx + 16, ty + 22], fill=(86, 160, 226),
                      outline=(46, 110, 180), width=4)
        elif mood == "laugh":
            for i in range(3):
                k = ((t * 0.9 + i * 0.33) % 1.0)
                mx = cx + w * (0.30 + 0.06 * i) + 14 * math.sin(t * 2 + i)
                my = hy - k * 120
                a = int(220 * (1 - k))
                d.arc([mx - 20, my - 16, mx + 20, my + 16], 200, 340,
                      fill=(240, 170, 60, a), width=6)
        elif mood == "proud":
            for i in range(4):
                k = ((t * 1.1 + i * 0.25) % 1.0)
                sx = cx + w * (0.26 if i % 2 else -0.30) + 20 * math.sin(t * 1.7 + i)
                sy2 = hy - 20 - k * 90
                s = 15 + 9 * math.sin(t * 6 + i)
                col = (246, 198, 60)
                d.line([sx - s, sy2, sx + s, sy2], fill=col, width=8)
                d.line([sx, sy2 - s, sx, sy2 + s], fill=col, width=8)

    def _character(self, tag, mood, talk, t, look):
        from PIL import Image, ImageDraw
        prod = self.prod[tag]
        w, h = prod.size
        im = Image.new("RGBA", (w + 2 * PAD, h + 2 * PAD + FOOT), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        self._legs(d, tag, PAD, PAD, w, h, t, 1.0 if mood == "laugh" else talk)
        self._arms(d, tag, PAD, PAD, w, h, mood, talk, t)     # qo'llar tovar ORQASIDAN
        im.alpha_composite(prod, (PAD, PAD))
        d = ImageDraw.Draw(im)
        self._face(d, tag, PAD, PAD, w, h, mood, talk, t, look)
        self._marks(d, tag, PAD, PAD, w, h, mood, t)
        return im

    # ── Subtitr: aytilayotgan so'z yonib turadi
    def _subtitle(self, fr, ln, k):
        from PIL import Image, ImageDraw
        lay = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        d = ImageDraw.Draw(lay)
        font = self.F(54)
        words = ln["say"].split()
        wt = [len(w) + 1 for w in words]
        tot = float(sum(wt)) or 1.0
        acc, cur_i = 0.0, -1
        for i, x in enumerate(wt):
            if acc / tot <= k < (acc + x) / tot:
                cur_i = i
            acc += x
        rows, row, idx = [], [], 0
        for i, wd in enumerate(words):
            trial = " ".join([w for _, w in row] + [wd])
            if d.textlength(trial, font=font) <= 880 or not row:
                row.append((i, wd))
            else:
                rows.append(row)
                row = [(i, wd)]
        if row:
            rows.append(row)
        rows = rows[:3]
        hbox = 70 + len(rows) * 66
        y0 = 1440
        col = COL[ln["who"]]
        d.rounded_rectangle([60, y0, 1020, y0 + hbox], radius=30, fill=(255, 255, 255, 242))
        d.rounded_rectangle([60, y0, 78, y0 + hbox], radius=8, fill=col)
        d.text((104, y0 + 14), self.names[ln["who"]].upper()[:16],
               font=self.F(30), fill=col)
        for r, rw in enumerate(rows):
            x = 104
            for i, wd in rw:
                fill = col if i == cur_i else ((25, 35, 30) if i < cur_i else (148, 158, 152))
                d.text((x, y0 + 54 + r * 66), wd, font=font, fill=fill)
                x += d.textlength(wd + " ", font=font)
        fr.alpha_composite(lay)

    def frame(self, t, cur, env, lines, total, spans):
        from PIL import Image, ImageEnhance
        fr = self.bg.copy()
        self._bubbles(fr, t)
        moods = {"A": "neutral", "B": "neutral"}
        since = {"A": -9.0, "B": -9.0}
        if cur >= 0:
            for i, ln in enumerate(lines[:cur + 1]):
                moods[ln["who"]] = ln["mood"]
                if i < len(spans):
                    since[ln["who"]] = spans[i][0]
        speaker = lines[cur]["who"] if 0 <= cur < len(lines) else ""
        ep = _ease_out_back(t / ENTER) if t < ENTER else 1.0

        if speaker:
            x, y, w, h = self.box[speaker]
            fr.alpha_composite(self.glow, (int(x + w / 2 - self.glow.width / 2),
                                           int(y + h * 0.34 - self.glow.height / 2)))
        for tag in "AB":
            x, y, w, h = self.box[tag]
            cx = x + w / 2
            mood = moods[tag]
            talking = (tag == speaker)
            talk = env if talking else 0.0
            ph = 0.0 if tag == "A" else 1.7
            br = 0.013 * math.sin(t * 2.1 + ph)
            sx = 1.0 + br - 0.035 * talk
            sy = 1.0 - br + 0.055 * talk
            k = (1.055 if talking else 1.0) * (_lerp(0.84, 1.0, t / ENTER) if t < ENTER else 1.0)
            dy = (1.0 - ep) * 430
            el = t - since[tag]
            if talking and 0 <= el < 0.5:
                dy -= 46 * math.sin(math.pi * el / 0.5)
            if not talking:
                dy += 3 * math.sin(t * 2.2 + ph)
            rot = (-5.0 if tag == "A" else 5.0) * (1.0 if talking else 0.3)
            if mood == "laugh":
                rot += 7 * math.sin(t * 7.5)
                dy -= abs(math.sin(t * 7.5)) * 11
            elif mood == "angry":
                rot += 1.8 * math.sin(t * 26)
            elif mood == "sad":
                rot += 5.0 if tag == "A" else -5.0
                dy += 9

            sd = self.shadow[tag]
            hop = max(0.0, -dy)
            ssx = max(0.45, 1.0 - hop / 230.0)
            sw2, sh2 = max(1, int(sd.width * ssx)), max(1, int(sd.height * ssx))
            fr.alpha_composite(sd.resize((sw2, sh2), Image.BILINEAR),
                               (int(cx - sw2 / 2), int(GROUND - sh2 / 2 + 24)))

            im = self._character(tag, mood, talk, t, 1 if tag == "A" else -1)
            if rot:
                im = im.rotate(rot, resample=Image.BICUBIC,
                               center=(im.width / 2, PAD + h + FOOT))
            kx, ky = k * sx, k * sy
            im = im.resize((max(1, int(im.width * kx)), max(1, int(im.height * ky))),
                           Image.BILINEAR)
            if speaker and not talking:
                im = ImageEnhance.Brightness(im).enhance(0.9)
            fr.alpha_composite(im, (int(cx - (PAD + w / 2) * kx),
                                    int(GROUND + dy - (PAD + h + FOOT) * ky)))

        from bot import reels as R
        hk = self.hook
        if t < 0.35:
            hk = R._alpha(hk, t / 0.35)
        fr.alpha_composite(hk, (0, int(5 * math.sin(t * 1.5))))
        if t > 0.45:
            fr.alpha_composite(self.labels if t > 0.8 else R._alpha(self.labels, (t - 0.45) / 0.35))

        if 0 <= cur < len(lines):
            a, b = spans[cur] if cur < len(spans) else (t, t + 1)
            self._subtitle(fr, lines[cur], (t - a) / max(0.4, b - a))
        if t > total - OUTRO:
            fr.alpha_composite(R._alpha(self.outro, min(1.0, (t - (total - OUTRO)) / 0.35)))
        return fr.convert("RGB")


def render(pair, script, times, voice_wav, store, out_path) -> str:
    from bot import reels as R
    total = (times[-1][1] if times else 14.0) + LEAD + OUTRO
    n = int(total * FPS)
    try:
        stage = _Stage(pair, script, store)
    except Exception as e:
        return f"sahna tayyorlanmadi: {e}"
    # Ovoz chiqmasa ham rolik "soqov" bo'lib qolmasin: gaplarni teng
    # bo'lib, subtitr va og'iz harakatini baribir ko'rsatamiz.
    if times:
        shifted = [(a + LEAD, b + LEAD) for a, b in times]
    else:
        step = (total - LEAD - OUTRO) / max(1, len(script["lines"]))
        shifted = [(LEAD + i * step, LEAD + (i + 1) * step - 0.12)
                   for i in range(len(script["lines"]))]
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
            ev = env[i] if i < len(env) else 0.0
            if not times and cur >= 0:          # ovozsiz holatda og'iz o'zi qimirlaydi
                ev = 0.45 + 0.4 * abs(math.sin(t * 11))
            proc.stdin.write(stage.frame(t, cur, ev, script["lines"],
                                         total, shifted).tobytes())
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
    q = {"brend": "Siz qaysi brendni tanlaysiz?",
         "tanlov": "Siz qaysi tomondasiz — usta yoki uy bekasi?",
         "olcham": "Sizga qaysi o'lcham kerak?",
         "narx": "Sizchi — sifatmi yoki narx?",
         "sotuv": "Siz qaysi birini olgansiz?",
         "hamkor": "Ikkisini birga olib ko'rganmisiz?"}.get(
             script.get("angle"), "Sizchi, qaysi biri?")
    lines = [f"<b>{html.escape(script['hook'])}</b>", ""]
    for tag, s2 in (("😤", a), ("😄", b)):
        extra = f" (30 kunda {s2['buyers']} marta olishdi)" if s2["buyers"] else ""
        lines.append(f"{tag} {html.escape(short_name(s2['p']))} — "
                     f"{price_say(s2['p']['sell_price'])}{extra}")
    lines += ["", f"{q} Izohda yozing 👇", "",
              "📍 Chinor, Sergeli · buyurtma pastdagi tugma orqali",
              "#chinor #qurilish #toshkent #sergeli #usta"]
    return "\n".join(lines)[:1000]


async def produce(db) -> dict:
    """Juftlik → dialog → ovoz → video → adminga tasdiq (reels_compare oqimi)."""
    from bot import reels as R, reels_compare as C
    pair = await pick_pair(db)
    if not pair:
        return {"ok": False, "error": "Mos juftlik yo'q (qimmatroq va ko'proq sotilgan arzonrog'i)"}
    loop = asyncio.get_running_loop()
    try:
        used = json.loads(db.get_setting("reels_talk_angles", "[]") or "[]")
    except ValueError:
        used = []
    angle = pick_angle(pair, used)
    script = await loop.run_in_executor(None, make_script, pair, angle)
    db.set_setting("reels_talk_angles", json.dumps(([angle["id"]] + used)[:6]))
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
            "angle": script.get("angle"),
            "pair": [short_name(s["p"]) for s in pair]}
