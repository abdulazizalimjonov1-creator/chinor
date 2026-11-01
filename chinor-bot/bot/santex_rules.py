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
    "rtp": {
        "label": "Kanalizatsiya (RTP, PVX)",
        "category": "Kanalizatsiya quvurlari",
        "about": "Kulrang PVX quvur — rezinka halqa bilan ulanadi, oqava (kanalizatsiya) uchun",
    },
}

# ─── Buyum turlari ─────────────────────────────────────────────────────────
# Tartib MUHIM: "polatvod" ichida "atvod" bor, shuning uchun u birinchi
# tekshiriladi. Har qatorda: (kalit, ko'rinadigan nom, imlo variantlari).
KINDS = [
    ("polotvod",   "Yarim burchak (45°)",  r"pol\s*-?\s*a[tv]+[o0]d|pol\s*-?\s*a[tv]+[o0]t|polot?vod"),
    ("otvod",      "Burchak (90°)",        r"\ba[tv]v?[o0]d\b|\ba[tv]v?[o0]t\b|\bo[tv]v?od\b"),
    ("troynik",    "Troynik (uch tomonlama)", r"tra?y?nik|troynik|trayniq"),
    ("krestovina", "Krestovina (to'rt tomonlama)", r"krestovina"),
    ("amerikanka", "Amerikanka (razyomli ulagich)", r"amerika?nka|amerkanka"),
    ("ushastik",   "Ushastik (quloqli burchak)", r"ushastik|quloqli"),
    ("adapter",    "Adapter (rezbali o'tkazgich)", r"adapter|adaptor"),
    ("mufta",      "Mufta (to'g'ri ulagich)", r"\bmuftr?a\b|\bmufta\b"),
    ("perexodnik", "Perexodnik (diametr o'tkazgich)", r"pere[xh]a?[o0]dnik|perexadnik|perehadnik"),
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
DIAMETERS = (16, 20, 25, 32, 40, 50, 63, 75, 90, 100, 110, 160)

# Suv turi — faqat RTR quvurlarida yoziladi.
WATER = [
    ("issiq",  "Issiq suv",      r"issiq|goryach"),
    ("sovuq",  "Sovuq suv",      r"sovuq|muzdek|xolodn"),
    ("pol",    "Issiq pol",      r"topl?iy?\s*pol|issiq\s*pol|topliy"),
]


def _norm(s: str) -> str:
    s = (s or "").lower()
    for ch in ("ʻ", "ʼ", "`", "’"):
        s = s.replace(ch, "'")
    return re.sub(r"\s+", " ", s).strip()


def system_of(name: str) -> str:
    """'rtr' | 'rtp' | '' — nomning boshidagi tizim belgisi."""
    n = _norm(name)
    m = re.match(r"^(rtr|rtp)\b", n) or re.match(r"^(rtr|rtp)\+", n)
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


def sizes_of(name: str, kind: str = "") -> dict:
    """Nomdagi o'lchamlar: {'dn': 25, 'all': [25, 20], 'thread': '15'}.

    `dn` — asosiy diametr (tizim belgisidan keyingi birinchi raqam).
    Ikkinchi raqam turga qarab o'qiladi: perexodnik/troynikda u ham
    diametr, adapter/amerikanka/ushastikda esa REZBA o'lchami."""
    n = _norm(name)
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
    return {
        "system": sys_key,
        "system_label": SYSTEMS.get(sys_key, {}).get("label", ""),
        "about": SYSTEMS.get(sys_key, {}).get("about", ""),
        "category": SYSTEMS.get(sys_key, {}).get("category", ""),
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
    if d["thread"]:
        side = f" {d['thread_side']}" if d["thread_side"] else ""
        inch = f' ({d["thread_inch"]})' if d.get("thread_inch") else ""
        parts.append(f"rezba {d['thread']}{inch}{side}")
    elif d["thread_side"]:
        parts.append(f"{d['thread_side']} rezba")
    if d["water_label"]:
        parts.append(d["water_label"])
    return " · ".join(parts)
