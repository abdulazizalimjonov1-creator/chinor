"""Santexnika quvurlari: NOMDAN qoida bilan ma'no chiqarish (AI'siz).

Nega AI emas, QOIDA: do'kondagi quvur-fitinglar nomi qat'iy tartibda
yoziladi — avval tizim (RTR/RTP), keyin o'lcham, keyin buyum turi:

    «RTR 25 Mufta»        → toza suv, 25 mm, mufta
    «RTP 110 Atvot»       → kanalizatsiya, 110 mm, burchak (otvod)
    «Rtr 32 Amerikanka  tashqi rezba 32/25» → toza suv, 32 mm, amerikanka,
                                               tashqi rezba 25

Shu uch narsani rasm hech qachon to'liq ayta olmaydi:
  • RASM turni (mufta/burchak/troynik) va tizimni (RTR oq payvandli,
    RTP kulrang rezinkali) KO'RSATADI;
  • lekin O'LCHAMNI ko'rsatmaydi — yonida o'lchov etaloni yo'q, qolaversa
    do'konda bir xil surat bir necha o'lchamga qo'yilgan (20/25/40 mufta
    bitta rasm). Shuning uchun o'lcham FAQAT nomdan olinadi.

Shuning uchun bu modul AI'dan OLDIN ishlaydi: nom qoidaga tushsa, AI
umuman chaqirilmaydi (tekin, tez va xatosiz), tushmasa — eski yo'l
(rasm + AI) o'z ishini qiladi.

Qayerda ishlatiladi:
  • bot/ai_category.py — bo'limni aniqlash (toza suv / kanalizatsiya);
  • tovar kartochkasi va kanal posti uchun qisqa fakt satri (`facts`).

Nomlar imlosi turlicha yozilgan (Atvod/Atvot, Mufta/Muftra, Amerikanka/
Amerkanka, Perexadnik/Perehadnik) — qoida shu variantlarning hammasini
taniydi, nomlarni O'ZGARTIRMAYDI: kassada xodimlar o'sha nom bilan
ishlaydi.
"""

from __future__ import annotations

import re

# ─── Tizimlar ──────────────────────────────────────────────────────────────
# RTR — oq PPR quvur, payvandlab ulanadi, BOSIMLI toza suv uchun.
# RTP — kulrang PVX quvur, rezinka halqali rozetkaga kiradi, OQAVA uchun.
SYSTEMS = {
    "rtr": {
        "label": "Toza suv (RTR, PPR)",
        "category": "Toza suv quvurlari",
        "about": "Oq PPR quvur — payvandlab ulanadi, bosimli ichimlik/issiq suv uchun",
    },
    # MPP — O'zbekiston mahsuloti (Miramax). RTR/RTP dan farqi: bitta belgi
    # ostida HAM toza suv, HAM kanalizatsiya bor, ular DIAMETR bilan
    # ajraladi (do'kon egasi aytgan qoida): 20/25/32/40 — suv, 50/75/100/150
    # — kanalizatsiya. Shuning uchun MPP uchun bo'lim `category()` da
    # o'lchamga qarab tanlanadi.
    "mpp": {
        "label": "Miramax (MPP, O'zbekiston)",
        "category": "",          # o'lchamga qarab — category() hal qiladi
        "about": "O'zbekistonda ishlab chiqarilgan (Miramax). 20–40 mm — toza suv, "
                 "50–150 mm — kanalizatsiya",
    },
    "rtp": {
        "label": "Kanalizatsiya (RTP, polipropilen)",
        "category": "Kanalizatsiya quvurlari",
        "about": "Kulrang polipropilen quvur — rezina halqa bilan ulanadi, oqava uchun. "
                 "Qattiq PVX dan farqli, egiluvchan: urilganda va qishda yorilmaydi",
    },
}

# ─── Buyum turlari ─────────────────────────────────────────────────────────
# Tartib MUHIM: "polatvod" ichida "atvod" bor, shuning uchun u birinchi
# tekshiriladi. Har qatorda: (kalit, ko'rinadigan nom, imlo variantlari).
KINDS = [
    ("polotvod",   "Yarim burchak (45°)",  r"pol\s*-?\s*a[tv]+[o0]d|pol\s*-?\s*a[tv]+[o0]t|polot?vod"),
    ("otvod",      "Burchak (90°)",        r"\ba[tv]v?[o0]d\b|\ba[tv]v?[o0]t\b|\bo[tv]v?od\b"),
    ("troynik_kos", "Troynik qiyshiq (45°)", r"tra?y?nik\s*kass?oy|kos[oy]y\s*tra?y?nik"),
    ("troynik",    "Troynik (uch tomonlama)", r"tra?y?nik|troynik|trayniq"),
    ("reviziya",   "Reviziya (tozalash lyugi)", r"reviziya|revizia|reviz\b"),
    ("sifon",      "Sifon",                r"\bsifon\b"),
    ("radiator_kran", "Radiator krani",     r"radiat[ro]r?\s*kran|kran\s*radiator"),
    ("krestovina", "Krestovina (to'rt tomonlama)", r"krestovina"),
    ("amerikanka", "Amerikanka (razyomli ulagich)", r"amerika?nka|amerkanka"),
    ("ushastik",   "Ushastik (quloqli burchak)", r"ushastik|quloqli"),
    ("adapter",    "Adapter (rezbali o'tkazgich)", r"adapter|adaptor"),
    ("mufta",      "Mufta (to'g'ri ulagich)", r"\bmuftr?a\b|\bmufta\b"),
    ("perexodnik", "Perexodnik (diametr o'tkazgich)",
     r"pere[xh]a?[o0]dnik|perexadnik|perehadnik|pere[xh]a?[o0]d\b"),
    ("klapan",     "Qaytarmas klapan (obratniy)", r"obratn\w*\s*klap\w*|klapan"),
    ("manjet",     "Manjet (rezina halqa)", r"manjet\w*"),
    ("kran",       "Sharavoy kran",        r"shara?voy\s*kran|shar\s*kran|\bkran\b"),
    ("filtr",      "Filtr",                r"\bfiltr\b"),
    ("klipsa",     "Klipsa (mahkamlagich)", r"\bklipsa\b|\bklips\b"),
    ("mostik",     "Mostik (o'tkazgich ko'prik)", r"\bmostik\b"),
    ("zaglushka",  "Zaglushka (tiqin)",    r"zaglu[sj]h?ka|zaglujka"),
    ("flanes",     "Flanes",               r"\bflan[ets]+\b"),
    ("turba",      "Quvur (turba)",        r"\bturba\w*|\btruba\w*|\bquvur\w*"),
]

# Do'konda uchraydigan diametrlar (mm). Ro'yxat yopiq — "25/15 rezba" dagi
# 15 ni diametr deb o'qib yubormaslik uchun.
DIAMETERS = (16, 20, 25, 32, 40, 50, 63, 75, 90, 100, 110, 150, 160)

# Suv turi — faqat RTR quvurlarida yoziladi.
WATER = [
    ("issiq",  "Issiq suv",      r"issiq|goryach"),
    ("sovuq",  "Sovuq suv",      r"sovuq|muzdek|xolodn"),
    ("pol",    "Issiq pol",      r"topl?iy?\s*pol|issiq\s*pol|topliy"),
]


# ─── Quvurni ajratish: issiq yoki sovuq ────────────────────────────────────
# Do'kon egasining aytgani (01.11.2026): rossiya RTR quvurlarida SOVUQ
# suvnikining ichi tekis, hech qanday chiziq yo'q; ISSIQ suvnikining esa
# ICHIDA kulrang qatlam (shisha tolali armatura) bo'ladi. Tashqaridan
# ikkalasi bir xil oq ko'rinadi — farq faqat quvurning KESIMIDA bilinadi.
# Shuning uchun: ro'parasidan olingan surat issiq-sovuqni ko'rsatmaydi,
# kesimi ko'ringan surat esa ko'rsatadi.
NOTES = {
    ("turba", "issiq"): "Kesimida ichkarida kulrang qatlam (shisha tolali armatura) "
                        "ko'rinadi — issiq suv va isitish uchun",
    ("turba", "sovuq"): "Ichi tekis, armatura qatlami yo'q — sovuq suv uchun",
    ("turba", "pol"):   "Issiq pol konturi uchun, bobina (o'ram) bo'lib sotiladi",
}


def note(name: str) -> str:
    """Tovarni ajratib turadigan qo'shimcha izoh ('' — izoh yo'q).

    Mijoz kartochkasi, kanal posti va AI maslahatchi shu matndan
    foydalanadi: surat ayta olmaydigan narsani so'z aytib beradi."""
    d = parse(name)
    return NOTES.get((d["kind"], d["water"]), "")


# ─── Zavod haqida ──────────────────────────────────────────────────────────
# Manba: ishlab chiqaruvchining o'z sayti rtp.ru (rosturplast.ru shu yerga
# yo'naltiradi) va texnik passportlar. Shu yerda YOZILGANI — tekshirilgan
# ma'lumot; do'kon ustasining tajribasi alohida belgilangan.
BRAND = {
    "name": "РосТурПласт (RTP)",
    "site": "rtp.ru",
    "facts": [
        "Rossiya ishlab chiqaruvchisi, muhandislik santexnikasini 2005 yildan chiqaradi",
        "Rossiyada 4 ta zavodi bor: Yekaterinburg, Krasnodar, Novosibirsk va "
        "Yegoryevsk (Moskva viloyati), 7 ta savdo ofisi",
        "3500 dan ortiq mahsulot turi — quvur, fiting, latun armatura, issiq pol",
        "Mahsulot liniyalari: ALPHA (PPR quvur va fitinglar — bizdagi «RTR»), "
        "BETA va BETA ELITE (ichki kanalizatsiya — bizdagi «RTP»), "
        "BETA ORANGE (ko'chadagi kanalizatsiya), SIGMA (latun), GAMMA (PND), "
        "DELTA (PE-Xa), OMEGA (issiq pol)",
        "Zavod kafolati 10 yil, xizmat muddati 50 yil deb ko'rsatiladi",
        "GOST 53630-2009, 18899-2001 va 32415-2013 bo'yicha ishlab chiqariladi",
    ],
    # Do'kon ustasining o'z tajribasi (AI uni «zavod ma'lumoti» deb ko'rsatmasin)
    "shop": [
        "Kanalizatsiya quvurlari egiluvchan — shuning uchun urilganda yoki "
        "qishda sinmaydi, yoriq ketmaydi",
        "Toza suv quvurlarida: sovuq suvnikining ichi tekis, issiq suvnikining "
        "ichida kulrang armatura qatlami bo'ladi",
    ],
}

# Mahsulotning ICHKI tuzilishi — mijoz «nega qimmat / qaysi biri kerak» deb
# so'raganda AI shuni tushuntiradi.
STRUCTURE = {
    "rtr": [
        "Oq polipropilen (PP-R) quvur, maxsus payvandlagich bilan eritib "
        "ulanadi — ulanish joyi quvurning o'zi kabi mustahkam, rezina yoki "
        "gayka yo'q, ya'ni oqadigan joyi ham yo'q",
        "Issiq suv va isitish uchun qatlamli (armaturali) turi ishlatiladi: "
        "ikki qatlam polipropilen orasida shisha tolali qatlam bo'ladi. U "
        "quvurni issiqdan cho'zilishiga yo'l qo'ymaydi — shuning uchun uzun "
        "liniyada egilib-bukilib ketmaydi",
        "Bosimga chidaydi (PN20/PN25), issiq suvda ham ishlaydi",
    ],
    "rtp": [
        "Kulrang polipropilen quvur, rozetkasida rezina halqa bor — "
        "payvandlash shart emas, bir-biriga kiritib ulanadi",
        "Polipropilen qattiq PVX dan egiluvchanroq: ustiga bosilsa yoki "
        "urilsa sinmaydi, qishda ham mo'rt bo'lib qolmaydi",
        "Oqava issiq suvga ham chidaydi (mashina, idish yuvish suvi) va "
        "suv oqimining shovqinini kamroq o'tkazadi",
    ],
}


# MPP — O'zbekiston mahsuloti. Internetdan bu firma haqida ishonchli
# ma'lumot topilmadi (sayt ham, katalog ham), shuning uchun bu yerda FAQAT
# do'kon biladigan narsa yozilgan. AI o'zidan maqtov qo'shmasligi uchun
# "tekshirilmagan" deb belgilab qo'yildi.
BRAND_MPP = {
    "name": "Miramax (MPP)",
    "site": "",
    "facts": [
        "O'zbekistonda ishlab chiqariladi — mahalliy mahsulot",
        "Do'konda «MPP» belgisi bilan yuritiladi",
        "Toza suv: 20, 25, 32, 40 mm — oq PPR quvur va fitinglar",
        "Kanalizatsiya: 50, 75, 100, 150 mm — kulrang quvur, bo'laklab "
        "sotiladi (25, 50, 100, 200, 300 sm)",
    ],
    "shop": [
        "Narxi import mahsulotdan arzonroq, shuning uchun ko'p oladilar",
        "Sovuq suvga «Arzoni PN10» turi ham bor — bosimi pastroq, narxi arzon",
    ],
}


def brand_of(system: str) -> dict:
    return BRAND_MPP if system == "mpp" else BRAND


def brand_text(system: str = "") -> str:
    """Zavod + tuzilish haqida qisqa ma'lumot (AI promptiga qo'shiladi)."""
    b = brand_of(system)
    nom = b["name"] + (f" ({b['site']})" if b.get("site") else "")
    out = [f"ISHLAB CHIQARUVCHI: {nom}"]
    out += [f"- {x}" for x in b["facts"]]
    # MPP quvurining tuzilishi RTR/RTP niki bilan bir xil (PPR va
    # polipropilen), shuning uchun tuzilish matni umumiy.
    st = STRUCTURE.get(system if system in STRUCTURE else
                       ("rtp" if system == "mpp_sewer" else "rtr"))
    if system in ("rtr", "rtp"):
        st = STRUCTURE[system]
    if st and system:
        out.append("MAHSULOT TUZILISHI:")
        out += [f"- {x}" for x in st]
    out.append("DO'KON TAJRIBASI (zavod ma'lumoti emas):")
    out += [f"- {x}" for x in b["shop"]]
    return "\n".join(out)


def _norm(s: str) -> str:
    s = (s or "").lower()
    for ch in ("ʻ", "ʼ", "`", "’"):
        s = s.replace(ch, "'")
    return re.sub(r"\s+", " ", s).strip()


def system_of(name: str) -> str:
    """'rtr' | 'rtp' | 'mpp' | '' — nomning boshidagi tizim belgisi."""
    n = _norm(name)
    m = re.match(r"^(rtr|rtp|mpp)\b", n) or re.match(r"^(rtr|rtp|mpp)\+", n)
    return m.group(1) if m else ""


def kind_of(name: str) -> tuple:
    """(kalit, ko'rinadigan nom) — topilmasa ('', '')."""
    n = _norm(name)
    for key, label, pat in KINDS:
        if re.search(pat, n):
            return key, label
    return "", ""


# Rezba o'lchami mm da yoziladi (15, 20, 25...), ustalar esa dyuymda gapiradi.
THREAD_INCH = {10: "3/8\"", 15: "1/2\"", 20: "3/4\"", 25: "1\"",
               32: "1¼\"", 40: "1½\"", 50: "2\""}
# Shu turlarda «20/15» ikkinchi raqami — REZBA (quvur 20 mm, rezba 1/2").
THREADED = {"adapter", "amerikanka", "ushastik", "zaglushka", "kran", "filtr", "mufta"}
# Bularda esa ikkinchi raqam ham DIAMETR («32/20» — 32 dan 20 ga o'tkazgich).
MULTI_DIA = {"perexodnik", "troynik", "krestovina"}


def length_of(name: str) -> str:
    """Quvurning uzunligi: «200 sm», «1 metr» → '200 sm' / '100 sm'.

    Kanalizatsiya quvuri bo'lak-bo'lak sotiladi (25/50/100/200/300 sm),
    shuning uchun uzunlik — o'lchamning bir qismi. Diametrni hisoblashdan
    OLDIN ajratib olinadi, aks holda «100 Turba 25 sm» dagi 25 diametr
    bo'lib ketardi."""
    n = _norm(name)
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:sm|см)\b|(\d+(?:[.,]\d+)?)\s*(?:metr|metrli|m)\b", n)
    if not m:
        return ""
    if m.group(1):
        return f"{int(float(m.group(1).replace(',', '.')))} sm"
    metr = float(m.group(2).replace(",", "."))
    return f"{int(metr * 100)} sm"


def _strip_length(n: str) -> str:
    """Uzunlik va devor qalinligini olib tashlaydi (diametr aralashmasin)."""
    n = re.sub(r"\d+(?:[.,]\d+)?\s*(?:sm|см)\b", " ", n)
    n = re.sub(r"\d+(?:[.,]\d+)?\s*(?:metr|metrli)\b", " ", n)
    n = re.sub(r"\(\s*\d+\s*mm\s*\)", " ", n)          # «(3mm)» — devor qalinligi
    return n


def sizes_of(name: str, kind: str = "") -> dict:
    """Nomdagi o'lchamlar: {'dn': 25, 'all': [25, 20], 'thread': '15'}.

    `dn` — asosiy diametr (tizim belgisidan keyingi birinchi raqam).
    Ikkinchi raqam turga qarab o'qiladi: perexodnik/troynikda u ham
    diametr, adapter/amerikanka/ushastikda esa REZBA o'lchami."""
    n = _strip_length(_norm(name))
    thread = ""
    m = re.search(r"\b(\d)\s*/\s*(\d)\b", n)          # 1/2, 3/4 — dyuym
    if m:
        thread = f"{m.group(1)}/{m.group(2)}"
    nums = [int(x) for x in re.findall(r"\d+", n)]
    dia = []
    for x in nums:                      # takrorini tashlaymiz, tartib saqlanadi
        if x in DIAMETERS and x not in dia:
            dia.append(x)
    dn = dia[0] if dia else 0
    if not thread:
        # «15 rezba», «rezba 15», «15 tashqi rezba», «25/15rezba»
        m2 = (re.search(r"(\d+)\s*(?:ichki|tashqi|para)?\s*rezb", n)
              or re.search(r"rezb\w*\s*(\d+)", n))
        if m2:
            thread = m2.group(1)
        elif kind in THREADED and len(dia) > 1:
            thread = str(dia[1])             # «20/15» — ikkinchisi rezba
    # «20/15», «32/25» — birinchisi quvur, ikkinchisi REZBA. Rezba o'lchami
    # (15, 10) diametrlar ro'yxatida yo'q, shuning uchun alohida qaraymiz.
    pair = re.search(r"\b(\d{2,3})\s*[/*x]\s*(\d{1,3})\b", n)
    if (kind in THREADED and pair and int(pair.group(1)) == dn
            and (not thread or thread == str(dn))):
        thread = pair.group(2)
    # Rezba diametr bilan bir xil chiqib qolsa — ikkinchi raqamni olamiz.
    if thread.isdigit() and dn and int(thread) == dn and len(dia) > 1:
        thread = str(dia[1])
    if thread.isdigit() and dn and int(thread) > dn:
        thread = ""                          # bema'ni: rezba quvurdan katta
    return {"dn": dn, "all": dia if kind in MULTI_DIA or len(dia) > 1 else dia,
            "thread": thread}


def water_of(name: str) -> tuple:
    n = _norm(name)
    for key, label, pat in WATER:
        if re.search(pat, n):
            return key, label
    return "", ""


def thread_side(name: str) -> str:
    """'ichki' | 'tashqi' | '' — rezba ichkarimi yoki tashqarida."""
    n = _norm(name)
    if re.search(r"ichki|vnutr", n):
        return "ichki"
    if re.search(r"tashqi|naruj", n):
        return "tashqi"
    return ""


def parse(name: str) -> dict:
    """Nomni qoidaga solib tushunarli ma'lumotga aylantiradi.

    Tizim topilmasa ham turni/o'lchamni qaytaradi — boshqa quvurlarga ham
    asqotadi; `system` bo'sh bo'lsa bo'lim taklif qilinmaydi."""
    sys_key = system_of(name)
    kind, kind_label = kind_of(name)
    sz = sizes_of(name, kind)
    water, water_label = water_of(name)
    length = length_of(name)
    if not kind and water == "pol":
        kind, kind_label = "turba", "Quvur (turba)"     # «Topliy pol» — o'ramdagi quvur
    # Chuyan (chugun) o'tkazgich/manjet — kanalizatsiya tarafi, nomi qaysi
    # belgidan boshlanishidan qat'i nazar. LEKIN faqat quvur qismi bo'lsa:
    # «chugunniy skavarodka» (tova) yoki «paranit batare uchun» (radiator
    # zichlagichi) ham «chugun» so'zini tutadi, ular quvurga aloqasiz.
    if kind and re.search(r"chugun|chuyan", _norm(name)):
        sys_use = "kanalizatsiya"
    else:
        sys_use = _use_for(sys_key, sz["dn"])
    return {
        "system": sys_key,
        "system_label": SYSTEMS.get(sys_key, {}).get("label", ""),
        "about": SYSTEMS.get(sys_key, {}).get("about", ""),
        "category": ("Kanalizatsiya quvurlari" if sys_use == "kanalizatsiya"
                     else ("Toza suv quvurlari" if sys_use == "suv" else "")),
        "length": length,
        # MPP da tizim bitta, ish turi esa diametr bilan ajraladi
        "use": sys_use,
        "kind": kind,
        "kind_label": kind_label,
        "dn": sz["dn"],
        "sizes": sz["all"],
        "thread": sz["thread"],
        "thread_inch": THREAD_INCH.get(int(sz["thread"]), "") if sz["thread"].isdigit() else "",
        "thread_side": thread_side(name),
        "water": water,
        "water_label": water_label,
    }


SEWER_DN = (50, 75, 90, 100, 110, 150, 160)      # shu diametrlar — kanalizatsiya


def _use_for(system: str, dn: int) -> str:
    """'suv' | 'kanalizatsiya' | '' — tovar nima uchun ishlatiladi."""
    if system == "rtr":
        return "suv"
    if system == "rtp":
        return "kanalizatsiya"
    if system == "mpp":
        if dn in SEWER_DN:
            return "kanalizatsiya"
        if dn:
            return "suv"
        return "suv"                 # o'lchamsiz MPP (issiq pol) — suv tarafi
    return ""


def _category_for(system: str, dn: int) -> str:
    use = _use_for(system, dn)
    if not use:
        return ""
    return ("Kanalizatsiya quvurlari" if use == "kanalizatsiya"
            else "Toza suv quvurlari")


def category(name: str) -> str:
    """Bo'lim nomi — qoidaga tushmasa '' (keyin AI qaraydi)."""
    return parse(name).get("category", "")


def facts(name: str) -> str:
    """Bir qatorlik tushuntirish — kartochka, kanal posti va AI uchun.

    Masalan: «Toza suv (RTR, PPR) · Burchak (90°) · 25 mm»."""
    d = parse(name)
    if not d["system"] and not d["kind"]:
        return ""
    parts = []
    if d["system_label"]:
        parts.append(d["system_label"])
    if d["kind_label"]:
        parts.append(d["kind_label"])
    if d["dn"]:
        parts.append(f"{d['dn']} mm")
    if d.get("length"):
        parts.append(d["length"])
    if d["thread"]:
        side = f" {d['thread_side']}" if d["thread_side"] else ""
        inch = f' ({d["thread_inch"]})' if d.get("thread_inch") else ""
        parts.append(f"rezba {d['thread']}{inch}{side}")
    elif d["thread_side"]:
        parts.append(f"{d['thread_side']} rezba")
    if d["water_label"]:
        parts.append(d["water_label"])
    return " · ".join(parts)
