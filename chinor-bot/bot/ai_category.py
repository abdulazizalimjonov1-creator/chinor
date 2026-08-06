"""Tovar kategoriyasini RASMGA qarab aniqlash.

NIMA UCHUN: ilgari kategoriya faqat NOMDAN topilardi. Nomlar esa do'kon
ichki tili — "Krakadil knobkali", "SHayton 60sm", "801 ПВА mashhad". Ularning
yarmiga qoida ham, matnli AI ham ojiz. RASM esa tovarni to'g'ridan-to'g'ri
ko'rsatadi: quti, kran, kabel, elim — ko'rish bilan ma'lum.

Shuning uchun tartib quyidagicha:
  1. rasm bor bo'lsa  → rasmli model (ai_engine.describe)
  2. rasm yo'q/model yiqildi → nomdan matnli model (ai_engine.chat)
  3. ikkalasi ham ishonchsiz → '' (kategoriya qo'yilmaydi)

MAXFIYLIK: AI ga faqat RASM va TOVAR NOMI boradi — narx, qoldiq, tannarx,
mijoz ma'lumotlari hech qachon yuborilmaydi.

AI adashadi, shuning uchun u FAQAT berilgan ro'yxatdan tanlaydi; ishonchsiz
bo'lsa "Boshqalar" deyishi so'raladi va biz kategoriyani umuman qo'ymaymiz —
noto'g'ri bo'limdan ko'ra bo'limsiz qolgani yaxshiroq (admin qo'lda tuzatadi).
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

OTHER = "Boshqalar"

# Bazada birorta kategoriya bo'lmasa shu ro'yxatdan tanlanadi (va tanlangani
# o'sha zahoti yaratiladi). Ro'yxat `marketplace/categorize.py` dagi bilan bir
# xil — ikkalasi bir do'konning bo'limlari.
DEFAULT_CATEGORIES = [
    "Elektr asboblar", "Elektr mollari", "Santexnika", "Bo'yoq va lak",
    "Qurilish aralashmasi", "Sochma material", "Mahkamlagichlar", "Asboblar",
    "Eshik va deraza", "Isitish va gaz", "Maishiy texnika", "Oshxona buyumlari",
    "Uy-ro'zg'or", "Tozalash vositalari", "Shaxsiy parvarish",
    "Ish kiyimi va himoya",
]

# Chalkash bo'limlar uchun qisqa izoh. AI "elim" ni rasmga qarab "Bo'yoq va
# lak"ka qo'yib yuborgani uchun qo'shildi — izoh bilan chegara aniq bo'ladi.
# Bazada boshqa nomli bo'lim bo'lsa izohsiz, faqat nomi bilan beriladi.
HINTS = {
    "Elektr asboblar": "drel, bolgarka, perforator, svarka — tokda ishlaydigan asbob",
    "Elektr mollari": "sim, kabel, rozetka, lampa, avtomat, schyotchik",
    "Santexnika": "kran, truba, unitaz, smesitel, shlanka, sifon",
    "Bo'yoq va lak": "kraska, emal, lak, gruntovka, rastvoritel, valik, cho'tka",
    "Qurilish aralashmasi": "sement, gips, shpaklovka, ELIM/kley, plitka yelimi, penoplast, montaj penasi",
    "Sochma material": "qum, shag'al, g'isht, keramzit",
    "Mahkamlagichlar": "mix, shurup, bolt, gayka, dyubel, xomut, anker",
    "Asboblar": "qo'l asboblari: klyuch, bolg'a, arra, otvertka, ruletka, sverlo",
    "Eshik va deraza": "qulf, ruchka, petlya, oyna, profil, dovodchik",
    "Isitish va gaz": "gaz balloni, gorelka, radiator, kotel, obogrevatel",
    "Maishiy texnika": "dazmol, chaynik, ventilyator, batareyka, zaryadka, pult",
    "Oshxona buyumlari": "qozon, kastryul, tarelka, stakan, pichoq, termos",
    "Uy-ro'zg'or": "shvabra, chelak, veshalka, arqon, parda, skotch",
    "Tozalash vositalari": "poroshok, sovun, gel, salfetka, paket, qo'lqop",
    "Shaxsiy parvarish": "shampun, krem, tish pastasi, dezodorant, britva",
    "Ish kiyimi va himoya": "kombinezon, kaska, ochki, respirator, qo'lqop-himoya",
}

_SYSTEM = (
    "Sen qurilish mollari do'koni katalogini tartiblovchi yordamchisan. "
    "Javobing FAQAT bitta kategoriya nomi bo'lsin — izohsiz, raqamsiz, "
    "belgisiz."
)


def _prompt(categories: list, name: str, with_image: bool) -> str:
    lines = []
    for c in categories:
        hint = HINTS.get(c)
        lines.append(f"- {c}" + (f" ({hint})" if hint else ""))
    lst = "\n".join(lines) + f"\n- {OTHER}"
    head = (
        "Rasmda do'kondagi bitta tovar tasvirlangan. Rasmga QARAB, tovar "
        "qanday buyum ekanini aniqla va unga eng mos bo'limni tanla."
        if with_image else
        "Quyidagi tovar nomiga eng mos bo'limni tanla."
    )
    return (
        f"{head}\n\n"
        f"BO'LIMLAR:\n{lst}\n\n"
        f"TOVAR NOMI (yordamchi ma'lumot, xato yozilgan bo'lishi mumkin): "
        f"{name or '—'}\n\n"
        "QOIDALAR:\n"
        "1. Faqat yuqoridagi ro'yxatdan bitta nomni yoz, yangisini o'ylab topma.\n"
        f"2. Ishonching komil bo'lmasa '{OTHER}' deb yoz.\n"
        "3. Boshqa hech narsa yozma — faqat bo'lim nomi."
    )


def match(raw: str, categories: list) -> str:
    """AI qaytargan matnni ro'yxatdagi aniq nomga moslaydi. Topilmasa ''."""
    c = (raw or "").strip().strip("*_#•-. ").lower()
    c = re.sub(r"^\d+[.)]\s*", "", c)
    if not c:
        return ""
    for known in categories:
        if c == known.lower():
            return known
    for known in categories:
        if known.lower() in c or c in known.lower():
            return known
    return OTHER if "boshqa" in c else ""


def full_list(db_categories: list) -> list:
    """Bazadagi bo'limlar + standart 16 talik (takrorlanmasdan).

    Nima uchun qo'shiladi: baza hali to'lmagan bo'lsa (masalan atigi 2 ta
    bo'lim bor) AI tanlash imkoniyati bo'lmagani uchun hamma tovarni
    "Boshqalar" deb yuboradi. Standart ro'yxat CHEKLANGAN (16 ta) — shuning
    uchun bo'limlar cheksiz ko'payib ketmaydi.
    """
    out, seen = [], set()
    for c in list(db_categories or []) + DEFAULT_CATEGORIES:
        c = (c or "").strip()
        if c and c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    return out


def from_text(text: str, categories: list) -> str:
    """Erkin matndan (masalan rasm tavsifidan) bo'limni topadi — AI'siz.

    `HINTS` dagi kalit so'zlar bo'yicha sanaladi. Nima uchun kerak: kuchsiz
    rasm modellari (llava) "bo'limni tanla" topshirig'ini uddalay olmaydi,
    lekin rasmda NIMA borligini yaxshi aytadi ("a can of paint…"). O'sha
    tavsifni shu yerda tekinga bo'limga aylantiramiz — qo'shimcha so'rovsiz.
    """
    low = " " + (text or "").lower() + " "
    if not low.strip():
        return ""
    best, score = "", 0
    for cat in categories:
        # Izohdagi kalit so'zlar vergul BILAN ham, qiyshiq chiziq bilan ham
        # ajratilgan ("ELIM/kley") — ikkalasini ham bo'lamiz.
        words = [w.strip() for w in re.split(r"[,/]", HINTS.get(cat, "") or "")]
        # SO'Z chegarasi bilan: "lak" so'zi "flakoni" ichidan topilib,
        # shampunni "Bo'yoq va lak"ka tiqib qo'ymasin.
        n = sum(1 for w in words
                if w and re.search(rf"\b{re.escape(w.lower())}\b", low))
        if cat.lower() in low:
            n += 2                      # bo'lim nomining o'zi aytilgan bo'lsa
        if n > score:
            best, score = cat, n
    # Ingliz tilidagi keng tarqalgan so'zlar (llava inglizcha javob beradi)
    if not score:
        en = {
            "paint": "Bo'yoq va lak", "varnish": "Bo'yoq va lak",
            "glue": "Qurilish aralashmasi", "cement": "Qurilish aralashmasi",
            "faucet": "Santexnika", "pipe": "Santexnika", "sink": "Santexnika",
            "wire": "Elektr mollari", "cable": "Elektr mollari",
            "lamp": "Elektr mollari", "socket": "Elektr mollari",
            "drill": "Elektr asboblar", "grinder": "Elektr asboblar",
            "hammer": "Asboblar", "wrench": "Asboblar", "screwdriver": "Asboblar",
            "screw": "Mahkamlagichlar", "nail": "Mahkamlagichlar",
            "bolt": "Mahkamlagichlar", "detergent": "Tozalash vositalari",
            "cleaner": "Tozalash vositalari", "soap": "Tozalash vositalari",
            "shampoo": "Shaxsiy parvarish", "toothpaste": "Shaxsiy parvarish",
            "bowl": "Oshxona buyumlari", "plate": "Oshxona buyumlari",
            "cup": "Oshxona buyumlari", "bottle": "Oshxona buyumlari",
            "gloves": "Ish kiyimi va himoya", "helmet": "Ish kiyimi va himoya",
            "lock": "Eshik va deraza", "hinge": "Eshik va deraza",
        }
        for word, cat in en.items():
            if cat in categories and re.search(rf"\b{word}\w*\b", low):
                return cat
    return best if score else ""


def suggest(categories: list, name: str = "", image_path: str = "") -> str:
    """(kategoriya nomi | '') — '' = ishonchsiz, kategoriya qo'yilmasin.

    Bloklovchi (tarmoqqa chiqadi): aiohttp ichida executor'da chaqiring.
    """
    from bot import ai_engine

    cats = full_list(categories)
    # Javobda tanish bo'lim nomi bo'lishi shart — bo'lmasa model bema'ni
    # gapiryapti va zanjir keyingisiga o'tishi kerak.
    def _ok(text: str) -> bool:
        low = (text or "").lower()
        return any(c.lower() in low for c in cats) or "boshqa" in low

    raw = ""
    if image_path:
        # 1-urinish: rasm modeli to'g'ridan-to'g'ri bo'limni tanlasin.
        # describe() rasm modellari yiqilsa o'zi nomdan matnli modelga o'tadi.
        raw = ai_engine.describe(image_path, _prompt(cats, name, True), name)
        if raw and not _ok(raw):
            # 2-urinish: model bo'lim tanlay olmadi (kuchsiz model shunday
            # bo'ladi) — undan RASMDA NIMA BORLIGINI so'raymiz, buni esa
            # kalit so'zlar orqali o'zimiz bo'limga aylantiramiz (tekin).
            got = from_text(raw, cats)
            if got:
                logger.info("🏷 tavsifdan topildi: %s", got)
                return got
            desc = ai_engine.describe(
                image_path,
                "Rasmda qanday buyum bor? Faqat buyum turini ayt, "
                "1-2 so'z bilan (masalan: bo'yoq bankasi, kran, kabel).",
                name)
            got = from_text(desc, cats)
            if got:
                logger.info("🏷 rasm tavsifidan topildi: %s", got)
                return got
            raw = ""
    if not raw:
        raw = ai_engine.chat(_prompt(cats, name, False), _SYSTEM, validate=_ok)
    if not raw:
        logger.warning("🏷 AI kategoriya bermadi: %s", name[:40])
        return ""

    # Model ba'zan bir-ikki gap yozib yuboradi — birinchi mos nomni qidiramiz.
    cat = match(raw, cats)
    if not cat:
        low = raw.lower()
        for known in cats:
            if known.lower() in low:
                cat = known
                break
    if cat == OTHER:
        return ""
    return cat


# ─── "Qayerda ishlatiladi" teglari ─────────────────────────────────────────
# Rasmda tovarning O'ZI ko'rinadi, lekin xaridor "bu menga keraKMI?" degan
# savolga javob izlaydi. Shu uch so'z ("Oshxona · Hammom · Avtomobil") aynan
# shu savolga javob beradi va rasmning pastidagi lentaga chiqadi.

# Tanlash uchun TAYYOR ro'yxat. Erkin javob so'ralganda kuchsiz model "Yes"
# deb javob bergan (2026-08-05, jonli sinov) — bo'limlardagi kabi, tayyor
# ro'yxatdan tanlash ancha ishonchli va natija bir xil uslubda chiqadi.
USAGE_PLACES = [
    "Oshxona", "Hammom", "Hojatxona", "Yotoqxona", "Mehmonxona", "Balkon",
    "Bog'", "Hovli", "Garaj", "Avtomobil", "Ofis", "Do'kon", "Ustaxona",
    "Qurilish", "Ta'mirlash", "Devor", "Shift", "Pol", "Eshik", "Deraza",
    "Yog'och", "Metall", "Beton", "Plitka", "Suv", "Elektr", "Isitish",
    "Tozalash", "Kir yuvish", "Idish yuvish", "Bolalar", "Sport", "Dala",
]

# Bo'limdan kelib chiqadigan zaxira teglar — AI hech narsa bermasa ham
# lenta bo'sh qolmasin (bo'lim allaqachon aniq bo'ladi).
CATEGORY_PLACES = {
    "Elektr asboblar": ["Ustaxona", "Qurilish", "Ta'mirlash"],
    "Elektr mollari": ["Elektr", "Uy", "Ofis"],
    "Santexnika": ["Hammom", "Oshxona", "Suv"],
    "Bo'yoq va lak": ["Devor", "Yog'och", "Metall"],
    "Qurilish aralashmasi": ["Qurilish", "Devor", "Pol"],
    "Sochma material": ["Qurilish", "Hovli", "Beton"],
    "Mahkamlagichlar": ["Qurilish", "Ta'mirlash", "Yog'och"],
    "Asboblar": ["Ustaxona", "Ta'mirlash", "Uy"],
    "Eshik va deraza": ["Eshik", "Deraza", "Ta'mirlash"],
    "Isitish va gaz": ["Isitish", "Oshxona", "Hovli"],
    "Maishiy texnika": ["Uy", "Oshxona", "Ofis"],
    "Oshxona buyumlari": ["Oshxona", "Dasturxon", "Uy"],
    "Uy-ro'zg'or": ["Uy", "Hovli", "Balkon"],
    "Tozalash vositalari": ["Tozalash", "Oshxona", "Hammom"],
    "Shaxsiy parvarish": ["Hammom", "Kundalik", "Yo'lda"],
    "Ish kiyimi va himoya": ["Qurilish", "Ustaxona", "Ish"],
}

MAX_TAG_LEN = 13          # lentaga sig'ishi kerak

# "Bu joymi?" degan savolga javob emas, shunchaki gap bo'lagi bo'lgan so'zlar.
_TAG_JUNK = {"so'm", "som", "sum", "narx", "dona", "sht", "ha", "yo'q", "yes",
             "no", "ok", "okay", "sure", "image", "photo", "picture", "product",
             "tovar", "rasm", "mahsulot", "bu", "shu", "the", "a", "an", "it"}

_TAG_PROMPT = (
    "Bu tovar QAYERDA ishlatiladi?\n"
    "Quyidagi ro'yxatdan 1-3 tasini tanla va vergul bilan yoz:\n"
    + ", ".join(USAGE_PLACES) + "\n"
    "Faqat tanlangan so'zlarni yoz — boshqa hech narsa."
)


def _match_place(t: str) -> str:
    """So'zni ro'yxatdagi joyga moslaydi ('oshxonada' → 'Oshxona')."""
    low = t.lower()
    for place in USAGE_PLACES:
        pl = place.lower()
        if low == pl or low.startswith(pl) or pl.startswith(low[:6]) and len(low) > 4:
            return place
    return ""


def _clean_tags(raw: str, strict: bool = True) -> list:
    """AI javobini toza teglar ro'yxatiga aylantiradi.

    `strict` — faqat `USAGE_PLACES` ro'yxatidagi so'zlar qabul qilinadi
    (kuchsiz model "Yes" yoki tasodifiy so'z qaytarishi mumkin).
    """
    parts = re.split(r"[,\n;·|/]+", raw or "")
    out = []
    for p in parts:
        # Ro'yxat belgisi (1. / 2) / - / •) olib tashlanadi — lekin uzun raqam
        # emas: u narx bo'lishi mumkin va bunday bo'lak butunlay rad etiladi.
        m = re.match(r"^\s*(?:\d{1,2}[.)]|[-*•])\s*", p)
        t = (p[m.end():] if m else p).strip(" .:\"'`*")
        t = re.sub(r"\s+", " ", t)
        if not t or len(t) > MAX_TAG_LEN or any(ch.isdigit() for ch in t):
            continue
        if t.lower() in _TAG_JUNK or len(t.split()) > 2:
            continue
        if strict:
            t = _match_place(t)
            if not t:
                continue
        else:
            t = t[0].upper() + t[1:]
        if t.lower() not in [x.lower() for x in out]:
            out.append(t)
    return out[:3]


def usage_tags(name: str = "", image_path: str = "",
               category: str = "") -> list:
    """Tovar qayerda ishlatilishi — 1-3 ta qisqa so'z.

    Tartib: rasm → nom → bo'limdan zaxira. Hech biri chiqmasa bo'sh ro'yxat
    (u holda rasmga lenta qo'yilmaydi — yolg'on yozgandan ko'ra yaxshi).

    Bloklovchi (tarmoq): executor'da chaqiring.
    """
    from bot import ai_engine

    def _ok(text: str) -> bool:
        return len(_clean_tags(text)) >= 1

    raw = ""
    if image_path:
        # Teg ham RASMDAN bo'lishi kerak — nomdan taxmin qilish "0 shina"ni
        # oshxonaga yuborgan edi. Rasm modeli yiqilsa bo'limdan zaxira olinadi.
        raw = ai_engine.describe(image_path, _TAG_PROMPT, name, validate=_ok,
                                 allow_text_fallback=False)
    if not raw:
        raw = ai_engine.chat(f"Tovar nomi: {name}\n\n{_TAG_PROMPT}",
                             validate=_ok)
    tags = _clean_tags(raw)
    if not tags and category:
        tags = CATEGORY_PLACES.get(category, [])[:3]
        if tags:
            logger.info("🏷 teglar bo'limdan olindi (%s): %s",
                        category, ", ".join(tags))
            return tags
    if tags:
        logger.info("🏷 qayerda ishlatiladi: %s → %s", name[:30], ", ".join(tags))
    return tags


async def apply_to_product(db, pid: int, name: str = "", image_path: str = "") -> str:
    """Kategoriyani AI bilan aniqlab, mahsulotga biriktiradi. Nomini qaytaradi.

    Kategoriya bazada bo'lmasa yaratiladi (bo'sh bazada birinchi tovarlar ham
    o'z bo'limini olsin). Aniqlanmasa hech narsa o'zgarmaydi va '' qaytadi.
    """
    import asyncio

    rows = await db.get_all_categories()
    by_name = {(r.get("name") or "").lower(): r["id"] for r in rows}
    cats = [r.get("name") for r in rows if r.get("name")]

    loop = asyncio.get_running_loop()
    cat = await loop.run_in_executor(None, suggest, cats, name, image_path)
    if not cat:
        return ""

    cid = by_name.get(cat.lower())
    if not cid:
        cid = await db.add_category(cat)
    if not cid:
        return ""
    await db.set_product_category(pid, cid)
    logger.info("🏷 #%s → %s (%s)", pid, cat, "rasm" if image_path else "nom")
    return cat
