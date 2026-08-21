"""Yetkazib beruvchini tovarlarga biriktirish — prixod ro'yxatidan.

Kompaniya nakladnoysidagi nomlar bazadagi nomlarga aynan to'g'ri kelmaydi
(«Coca-Cola 0.5», «Кока кола 0,5 л», «coca cola 05l»). Shuning uchun moslash
bosqichma-bosqich: shtrix-kod → sku → normallashtirilgan nom → «skelet» →
o'xshashlik. Ishonchli moslar avtomatik biriktiriladi, shubhalilari esa
variantlari bilan ro'yxatga chiqadi — kod o'zi taxmin qilib biriktirmaydi,
chunki noto'g'ri biriktiruv keyin noto'g'ri buyurtmaga aylanadi.

Biriktiruv `pos.db` da saqlanadi (`suppliers` + `products.supplier_id`) —
o'sha yerda Chinor admin ilovasining «Ombor → Yetkazib beruvchilar» bo'limi
uni ko'rsatadi va tahrirlash mumkin. Ulanish kaliti: Chinor `products.id` ==
YesPOS `sku` (pos_bridge.py bilan bir xil).

Ishlatish:
    python -m marketplace.supplier_link list
    python -m marketplace.supplier_link import --supplier "Coca-Cola" --file cola.txt
    python -m marketplace.supplier_link import --supplier "Coca-Cola" --file cola.txt --apply
    python -m marketplace.supplier_link assign --supplier "Coca-Cola" --sku 7481,7482
    python -m marketplace.supplier_link show --supplier "Coca-Cola"
    python -m marketplace.supplier_link unlink --sku 7481

Fayl formati — har qatorda bitta tovar. Ustunlar tab / `;` / `|` bilan
ajratilishi mumkin (Excel'dan nusxa olsa shunday bo'ladi):
    Coca-Cola 0.5L
    9780201379624;Fanta 1L;24
    7481  Sprite 1.5L
`#` bilan boshlangan qator — izoh.
"""

import argparse
import difflib
import os
import re
import shutil
import sqlite3
import sys
from typing import Dict, List, Optional, Tuple

from marketplace import config
from marketplace.db import now_local

AUTO_SCORE = 0.86   # shundan yuqorisi — avtomatik biriktiriladi
# Artikul bo'yicha topilganda nom shu darajada o'xshash bo'lishi shart.
# Pastroq bo'lsa — artikul boshqa tizimniki, unga ishonilmaydi.
SKU_NAME_MIN = 0.55
MAYBE_SCORE = 0.60  # shundan yuqorisi — variant sifatida ko'rsatiladi


# ─── Nom normalizatsiyasi ────────────────────────────────────────────────────
# Bu `chinor-bot/database/_helpers.py` dagi `norm_search`/`skeleton_search`
# NUSXASI. Ataylab nusxa: `marketplace` paketi chinor-bot'ga bog'liq emas
# (faqat requests + dotenv), shu bog'liqlikni saqlab qolamiz. Ikkalasi bir xil
# qoidada ishlashi kerak — birini o'zgartirsangiz, ikkinchisini ham.

def norm(s: str) -> str:
    s = (s or "").lower()
    for ch in ("'", "‘", "’", "`", "ʻ", "ʼ"):
        s = s.replace(ch, "")
    s = s.translate(str.maketrans("qxw", "khv"))
    s = s.replace("ch", "c").replace("sh", "s")
    s = s.replace("ya", "a").replace("yo", "o").replace("yu", "u")
    return re.sub(r"[^a-z0-9]+", "", s)


def skeleton(s: str) -> str:
    return re.sub(r"[aeiou]", "", norm(s))


# Kirill → lotin: nakladnoylar ko'pincha rus tilida keladi.
_CYR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "j", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "s", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "",
    "ы": "i", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def translit(s: str) -> str:
    return "".join(_CYR.get(c, _CYR.get(c.lower(), c)) for c in (s or ""))


def fold(s: str) -> str:
    """Moslashtirish kaliti: kirill → lotin → normalizatsiya → `c` va `k` birlashtiriladi.

    Nega `c`/`k`: «Coca Cola» lotinchada `c`, kirillchadan («Кока кола») esa `k`
    bo'lib keladi. Ular birlashtirilmasa aynan bir tovar 0.70 o'xshashlik bilan
    «shubhali» ro'yxatiga tushib qoladi.
    """
    return norm(translit(s)).replace("c", "k")


def sizes(s: str) -> set:
    """Nomdagi raqam guruhlari: «Fanta 0.25» → {"025"}, «Fanta 2.5 L» → {"25"}.

    O'lcham buyurtmada hal qiluvchi: 0.25 litrni 2.5 litr bilan almashtirib
    yuborish — noto'g'ri tovarni noto'g'ri miqdorda keltirish demak. Shuning
    uchun raqamlari umuman mos kelmasa, moslik avtomatik qabul qilinmaydi.
    """
    return set(re.findall(r"\d+", norm(translit(s))))


# ─── Kirish faylini o'qish ───────────────────────────────────────────────────

_XL_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def read_xlsx(path: str) -> List[List[str]]:
    """`.xlsx` ni XOM XML dan o'qiydi — qatorlar ro'yxati.

    Nega openpyxl emas: (1) `marketplace` paketi ataylab yengil — faqat
    requests + dotenv; (2) YesPOS chiqaradigan fayllarning stil qismi buzuq
    bo'ladi (`vertical` atributi noto'g'ri qiymat bilan) va openpyxl butun
    faylni o'qishdan bosh tortadi. Bizga esa faqat katakdagi matn kerak.
    """
    import zipfile
    import xml.etree.ElementTree as ET

    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            shared = [
                "".join(t.text or "" for t in si.iter(f"{_XL_NS}t"))
                for si in ET.fromstring(
                    z.read("xl/sharedStrings.xml")
                ).findall(f"{_XL_NS}si")
            ]
        sheets = sorted(n for n in z.namelist()
                        if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
        if not sheets:
            raise SystemExit(f"Excel faylida varaq topilmadi: {path}")

        rows = []
        for row in ET.fromstring(z.read(sheets[0])).iter(f"{_XL_NS}row"):
            vals = []
            for c in row.findall(f"{_XL_NS}c"):
                v = c.find(f"{_XL_NS}v")
                if c.get("t") == "s" and v is not None:
                    vals.append(shared[int(v.text)].strip())
                elif c.get("t") == "inlineStr":
                    vals.append("".join(t.text or "" for t in c.iter(f"{_XL_NS}t")).strip())
                else:
                    vals.append((v.text or "").strip() if v is not None else "")
            if any(vals):
                rows.append(vals)
    return rows


def _is_artikul(cell: str) -> bool:
    """3-7 xonali butun son — artikul (sku) bo'lishi mumkin."""
    return cell.isdigit() and 3 <= len(cell) <= 7


def xlsx_to_text(rows: List[List[str]]) -> str:
    """Excel qatorlarini `parse_lines` tushunadigan matnga aylantiradi.

    Sarlavha, «Итого» va imzo qatorlarini tashlab yuborish kerak. Agar
    qatorlarning yarmidan ko'pida artikul ustuni bo'lsa — faqat artikulli
    qatorlar olinadi (aniq va shovqinsiz). Aks holda (oddiy nomlar ro'yxati)
    matnli qatorlarning hammasi olinadi.
    """
    # Artikul QAYSI ustunda turganini topamiz — eng ko'p uchragan ustun.
    # Bu «Итого: … 321000» kabi yakuniy qatorlarni ajratadi: ularda ham son
    # bor, lekin boshqa ustunda.
    counts, seen = {}, {}
    for r in rows:
        for i, c in enumerate(r):
            if _is_artikul(c):
                counts[i] = counts.get(i, 0) + 1
                seen.setdefault(i, set()).add(c)
    # Narx ustuni ham 3-7 xonali son bo'ladi (10000, 11000 …), lekin qiymatlari
    # TAKRORLANADI. Artikul esa har qatorda noyob — birinchi mezon shu.
    #
    # Noyoblik teng bo'lsa ENG CHAPDAGISI olinadi: nakladnoyda artikul doim
    # nomdan oldin, narx esa keyin turadi. Ilgari «qiymati ko'proq» ustun
    # tanlanardi va kichik faylda «Итого» qatoridagi summa artikul ustunini
    # yutib ketardi (4 tovarlik faylda oxirgi «Сумма» tanlangan edi).
    col = None
    if counts:
        # QATOR RAQAMI ustunini chiqarib tashlaymiz: uning qiymatlari 1 dan
        # boshlanib qatorlar soniga yetadi (728 qatorli faylda eng kattasi
        # ~728). Artikul esa bazadagi id — u qatorlar sonidan ancha katta.
        # Bu ustun ham noyob va eng chapda bo'lgani uchun boshqa mezonlar
        # uni ajrata olmaydi.
        limit = len(rows) * 1.2
        real = {i for i in counts if max(int(v) for v in seen[i]) > limit}
        pool = real or set(counts)

        best = max(len(seen[i]) / counts[i] for i in pool)
        most = max(counts[i] for i in pool)
        cands = [i for i in pool
                 if len(seen[i]) / counts[i] >= best - 0.05
                 and counts[i] >= 0.5 * most]
        col = min(cands) if cands else None

    # Ostona past: kichik nakladnoyda (4 tovar + 10 qator sarlavha/imzo)
    # tovar qatorlari ozchilikni tashkil qiladi.
    if col is not None and counts[col] >= max(2, len(rows) // 4):
        # Artikul ustuni topildi — FAQAT artikul va nomni chiqaramiz.
        #
        # Nega: nakladnoyning birinchi ustuni qator RAQAMI (1, 2, 3 … 728).
        # 100-qatordan boshlab u ham 3 xonali son bo'ladi va `parse_lines`
        # uni artikul deb olardi (birinchi uchragani yutadi) — haqiqiy
        # artikul esa e'tibordan qolardi. Ustun aniq bo'lgach taxminga
        # o'rin qoldirmaymiz.
        out = []
        for r in rows:
            if len(r) <= col or not _is_artikul(r[col]):
                continue
            name = ""
            for i, c in enumerate(r):
                if i == col or not c.strip():
                    continue
                if c.replace(".", "").replace(",", "").isdigit():
                    continue          # son — nom emas
                if len(c) > len(name):
                    name = c
            out.append(f"{r[col]};{re.sub(r'\s+', ' ', name).strip()}")
        return "\n".join(out)
    else:
        # Artikul ustuni yo'q — oddiy nomlar ro'yxati.
        keep = [r for r in rows
                if any(len(c) >= 4 and not c.replace(".", "").isdigit() for c in r)]
    # Katak ICHIDAGI ketma-ket bo'shliqlar yig'iladi: aks holda «Sprite Moxito␣␣
    # alyumin banka» ikkita ustunga bo'linib, nom «alyumin banka» bo'lib qolardi.
    return "\n".join(
        ";".join(re.sub(r"\s+", " ", c).strip() for c in r if c.strip()) for r in keep
    )


def _header_columns(rows: List[List[str]]) -> Optional[dict]:
    """Sarlavha qatoridan ustun raqamlarini topadi.

    Taxmin qilishdan ko'ra sarlavhani O'QIGAN yaxshi: «Артикул»,
    «Наименование товара», «Стоимость покупки» — YesPOS nakladnoyida shu
    nomlar doim bir xil. Taxmin ikki marta yanglishgan edi (qator raqamini
    ham, «Итого» summasini ham artikul deb olgan).
    """
    for n, r in enumerate(rows[:15]):
        low = [c.strip().lower() for c in r]
        art = next((i for i, c in enumerate(low) if c.startswith("артикул")), None)
        nam = next((i for i, c in enumerate(low) if c.startswith("наименование")), None)
        if art is None or nam is None:
            continue
        price = next((i for i, c in enumerate(low) if c.startswith("стоимость")), None)
        return {"row": n, "sku": art, "name": nam, "price": price}
    return None


def read_items(path: str) -> List[dict]:
    """Fayl → {name, sku, barcode, qty, price, raw} qatorlari.

    `price` — YETKAZIB BERUVCHINING xarid narxi. Do'kon bir tovarni bir necha
    firmadan oladi va qaysi biri arzon bo'lsa o'shandan oladi, shuning uchun
    narx firma bo'yicha alohida saqlanadi.
    """
    if path.lower().endswith((".xlsx", ".xlsm")):
        rows = read_xlsx(path)
        head = _header_columns(rows)
        if head:
            out = []
            for r in rows[head["row"] + 1:]:
                if len(r) <= head["sku"] or not _is_artikul(r[head["sku"]]):
                    continue
                name = r[head["name"]] if len(r) > head["name"] else ""
                price = ""
                if head["price"] is not None and len(r) > head["price"]:
                    price = r[head["price"]].replace(" ", "").replace(",", "")
                out.append({
                    "sku": r[head["sku"]],
                    "name": re.sub(r"\s+", " ", name).strip(),
                    "barcode": "", "qty": "",
                    "price": price if price.replace(".", "").isdigit() else "",
                    "raw": ";".join(c for c in r if c),
                })
            if out:
                return out
        # Sarlavha topilmadi — eski (taxminiy) yo'l bilan.
        return parse_lines(xlsx_to_text(rows))
    return parse_lines(read_input(path))


def read_input(path: str) -> str:
    """Fayl (yoki stdin) → `parse_lines` uchun matn."""
    if path == "-":
        return sys.stdin.read()
    if path.lower().endswith((".xlsx", ".xlsm")):
        return xlsx_to_text(read_xlsx(path))
    return open(path, encoding="utf-8").read()


def parse_lines(text: str) -> List[dict]:
    """Matnni {name, barcode, sku, qty} qatorlariga ajratadi."""
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Aniq ajratgich (tab / ; / |) bo'lsa faqat shu bo'yicha bo'lamiz.
        # Ko'p bo'shliq ajratgichi FAQAT boshqasi bo'lmaganda ishlaydi — tovar
        # nomi ichida ham ikkita bo'shliq uchraydi va nomni bo'lib yuborardi.
        parts = [p.strip() for p in re.split(r"\t|;|\|", line) if p.strip()]
        if len(parts) == 1:
            parts = [p.strip() for p in re.split(r"  +", line) if p.strip()]
        if not parts:
            continue

        name, barcode, sku, qty = "", "", "", ""
        for p in parts:
            digits = p.replace(" ", "")
            if digits.isdigit():
                if len(digits) >= 8:        # 8+ raqam — shtrix-kod
                    barcode = barcode or digits
                elif len(digits) >= 3:      # 3-7 raqam — sku (artikul)
                    sku = sku or digits
                else:                       # kichik son — miqdor
                    qty = qty or digits
            elif not name or len(p) > len(name):
                name = p
        if not name and not barcode and not sku:
            continue
        out.append({"name": name, "barcode": barcode, "sku": sku,
                    "qty": qty, "raw": line})
    return out


# ─── pos.db ──────────────────────────────────────────────────────────────────

def _connect(read_only: bool = True) -> sqlite3.Connection:
    path = config.POS_DB
    if not os.path.exists(path):
        raise SystemExit(f"pos.db topilmadi: {path}")
    conn = sqlite3.connect(
        f"file:{path}?mode=ro" if read_only else path, uri=True, timeout=30
    )
    conn.row_factory = sqlite3.Row
    if not read_only:
        ensure_schedule_columns(conn)
    return conn


def ensure_schedule_columns(conn: sqlite3.Connection) -> None:
    """`suppliers.lead_days` / `cycle_days` bor-yo'qligini ta'minlaydi.

    Kanonik joyi — `chinor-bot/database/_base.py` `_migrate()`. Bu yerda ham
    tekshiriladi, chunki bu vosita bot qayta ishga tushmasidan oldin ham
    ishlatilishi mumkin (migratsiya faqat bot ishga tushganda bajariladi).
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(suppliers)").fetchall()}
    if "lead_days" not in cols:
        conn.execute("ALTER TABLE suppliers ADD COLUMN lead_days INTEGER DEFAULT 0")
    if "cycle_days" not in cols:
        conn.execute("ALTER TABLE suppliers ADD COLUMN cycle_days INTEGER DEFAULT 0")
    conn.commit()


def _schedule(row) -> tuple:
    """(lead_days, cycle_days) — ustun yo'q bo'lsa (0, 0)."""
    try:
        return int(row["lead_days"] or 0), int(row["cycle_days"] or 0)
    except (IndexError, KeyError):
        return 0, 0


def catalog_names() -> Dict[int, str]:
    """sku → YesPOS katalogidagi HOZIRGI nom (`marketplace.db`).

    Nega kerak: `pos_bridge` pos.db ga faqat qoldiq va narxni ko'chiradi,
    NOMNI emas. YesPOS'da tovar qayta nomlansa, pos.db da eski nom qolib
    ketadi (masalan 16417 pos.db da «Bonaqua 1.5 l», YesPOS'da «Bonaqua 1 l
    gazli»). Yetkazib beruvchi ro'yxati esa YesPOS nomlari bilan keladi —
    shuning uchun moslashda yangi nomni ishlatamiz.
    """
    if not os.path.exists(config.DB_PATH):
        return {}
    conn = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    try:
        return {
            int(sku): name
            for sku, name in conn.execute(
                "SELECT sku, name FROM products WHERE sku <> ''"
            )
            if str(sku).isdigit()
        }
    except sqlite3.Error:
        return {}   # katalog hali yig'ilmagan — pos.db nomlari bilan ishlaymiz
    finally:
        conn.close()


def load_products(conn: sqlite3.Connection) -> List[dict]:
    fresh = catalog_names()
    rows = [dict(r) for r in conn.execute(
        "SELECT id, name, barcode, supplier_id, qty FROM products WHERE is_active=1"
    )]
    for r in rows:
        # Katalogdagi nom bor bo'lsa — o'sha ustun turadi.
        r["name"] = fresh.get(int(r["id"]), r["name"] or "")
    return [
        {
            "id": int(r["id"]),
            "name": r["name"] or "",
            "barcode": (r["barcode"] or "").strip(),
            "supplier_id": int(r["supplier_id"] or 0),
            "qty": float(r["qty"] or 0),
            "norm": fold(r["name"] or ""),
            "skel": skeleton(translit(r["name"] or "")),
            "sizes": sizes(r["name"] or ""),
        }
        for r in rows
    ]


def suppliers(conn: sqlite3.Connection) -> Dict[int, str]:
    return {int(r["id"]): r["name"] for r in conn.execute("SELECT id, name FROM suppliers")}


def find_supplier(conn: sqlite3.Connection, name: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT id, name FROM suppliers WHERE lower(name) = lower(?)", (name.strip(),)
    ).fetchone()
    if row:
        return {"id": int(row["id"]), "name": row["name"]}
    # Qisman moslik — «Coca» deb yozilsa «Coca-Cola» topilsin.
    rows = conn.execute(
        "SELECT id, name FROM suppliers WHERE lower(name) LIKE lower(?)",
        (f"%{name.strip()}%",),
    ).fetchall()
    if len(rows) == 1:
        return {"id": int(rows[0]["id"]), "name": rows[0]["name"]}
    return None


# ─── Moslashtirish ───────────────────────────────────────────────────────────

def match_one(item: dict, products: List[dict], index: dict) -> dict:
    """Bitta qator uchun: {status, product, score, candidates}.

    status: 'aniq' | 'shubhali' | 'topilmadi'
    """
    # 1. Shtrix-kod — eng ishonchli.
    if item["barcode"]:
        hit = index["barcode"].get(item["barcode"])
        if hit:
            return {"status": "aniq", "product": hit, "score": 1.0,
                    "how": "shtrix-kod", "candidates": []}
    # 2. SKU (== pos.db id) — LEKIN nomi ham mos kelsa.
    #
    # Nega tekshiruv kerak: ba'zi nakladnoyda «Артикул» ustuni YesPOS kodi
    # emas, YETKAZIB BERUVCHINING o'z artikuli bo'ladi (masalan Dusel'da 100,
    # 101, 102…). Bunday kichik son tasodifan boshqa tovarning YesPOS kodiga
    # to'g'ri kelib qolishi mumkin — va biz mutlaqo boshqa tovarni biriktirib
    # yuborardik. Nom mos kelmasa, kodga ishonmay nom bo'yicha izlaymiz.
    sku_hit = None
    if item["sku"]:
        hit = index["id"].get(int(item["sku"]))
        if hit:
            if not item["name"]:
                return {"status": "aniq", "product": hit, "score": 1.0,
                        "how": "sku", "candidates": []}
            agree = difflib.SequenceMatcher(
                None, fold(item["name"]), fold(hit["name"])
            ).ratio()
            if agree >= SKU_NAME_MIN:
                return {"status": "aniq", "product": hit, "score": 1.0,
                        "how": "sku", "candidates": []}
            sku_hit = hit          # kod topildi-yu nomi boshqa — quyida ko'rsatamiz

    key = fold(item["name"])
    if not key:
        return {"status": "topilmadi", "product": None, "score": 0.0,
                "how": "", "candidates": []}

    # 3. Normallashtirilgan nom aynan mos va YAGONA bo'lsa.
    exact = index["norm"].get(key) or []
    if len(exact) == 1:
        return {"status": "aniq", "product": exact[0], "score": 1.0,
                "how": "nom", "candidates": []}
    if len(exact) > 1:
        return {"status": "shubhali", "product": None, "score": 1.0,
                "how": "bir xil nomli bir nechta tovar", "candidates": exact[:5]}

    # 4. Unlilarsiz skelet (imlo farqi: samorez/samarez).
    skel = index["skel"].get(skeleton(translit(item["name"]))) or []
    if len(skel) == 1:
        return {"status": "aniq", "product": skel[0], "score": 0.95,
                "how": "skelet", "candidates": []}

    # 5. O'xshashlik.
    q_sizes = sizes(item["name"])
    close = difflib.get_close_matches(key, index["norm_keys"], n=6, cutoff=MAYBE_SCORE)
    cands = []
    for k in close:
        base = difflib.SequenceMatcher(None, key, k).ratio()
        for p in index["norm"][k]:
            score = base
            # Ro'yxatdagi nom bazadagi nom ICHIDA to'liq turibdi
            # («Fanta 0.25» ⊂ «Butilka Fanta 0.25») — kuchli dalil.
            # So'rov nomzod nomi ichida to'liq turibdi. Lekin u nomzodning
            # yarmidan qisqa bo'lsa — bu parcha (masalan brendsiz «alyumin
            # banka 0.45 l»), unga ishonib bo'lmaydi.
            if len(key) >= 6 and key in k and len(key) >= 0.5 * len(k):
                score = max(score, 0.90)
            # O'lchamlar umuman mos kelmasa — avtomatik qabul qilinmaydi.
            if q_sizes and p["sizes"] and q_sizes.isdisjoint(p["sizes"]):
                score = min(score, MAYBE_SCORE - 0.05)
            cands.append((score, p))
    cands.sort(key=lambda x: -x[0])
    cands = [c for c in cands if c[0] >= MAYBE_SCORE - 0.05][:5]
    if not cands:
        if sku_hit:
            return {"status": "shubhali", "product": None, "score": 0.0,
                    "how": "artikul topildi, lekin nomi boshqa",
                    "candidates": [sku_hit]}
        return {"status": "topilmadi", "product": None, "score": 0.0,
                "how": "", "candidates": []}
    if not cands:
        if sku_hit:
            return {"status": "shubhali", "product": None, "score": 0.0,
                    "how": "artikul topildi, lekin nomi boshqa",
                    "candidates": [sku_hit]}
        return {"status": "topilmadi", "product": None, "score": 0.0,
                "how": "", "candidates": []}

    best_score, best = cands[0]
    second = cands[1][0] if len(cands) > 1 else 0.0
    # Ikkinchi variant deyarli teng bo'lsa — avtomatik tanlamaymiz.
    if best_score >= AUTO_SCORE and best_score - second > 0.05:
        return {"status": "aniq", "product": best, "score": best_score,
                "how": "o'xshashlik", "candidates": [p for _s, p in cands[1:3]]}
    return {"status": "shubhali", "product": None, "score": best_score,
            "how": "o'xshashlik", "candidates": [p for _s, p in cands[:3]]}


def build_index(products: List[dict]) -> dict:
    idx = {"barcode": {}, "id": {}, "norm": {}, "skel": {}}
    for p in products:
        if p["barcode"]:
            idx["barcode"].setdefault(p["barcode"], p)
        idx["id"][p["id"]] = p
        idx["norm"].setdefault(p["norm"], []).append(p)
        idx["skel"].setdefault(p["skel"], []).append(p)
    idx["norm_keys"] = list(idx["norm"].keys())
    return idx


# ─── Yozish ──────────────────────────────────────────────────────────────────

def _backup(path: str, keep: int = 5) -> str:
    """Yozishdan oldin nusxa oladi va eskilarini tozalaydi.

    Nusxa ~2.5 MB. Biriktirish qo'lda va kamdan-kam bajariladi, lekin bir necha
    o'nlab kompaniya ro'yxati kiritilsa ham disk to'lib qolmasin.
    """
    import glob

    stamp = now_local().strftime("%Y%m%d-%H%M%S")
    dst = f"{path}.bak-sup-{stamp}"
    shutil.copy2(path, dst)
    for old in sorted(glob.glob(f"{path}.bak-sup-*"))[:-keep]:
        try:
            os.remove(old)
        except OSError:
            pass
    return dst


def ensure_supplier(name: str, phone: str = "", note: str = "") -> Tuple[int, bool]:
    """Yetkazib beruvchini topadi yoki yaratadi. (id, yangi_yaratildimi)."""
    conn = _connect(read_only=False)
    try:
        found = find_supplier(conn, name)
        if found:
            return found["id"], False
        cur = conn.execute(
            "INSERT INTO suppliers(name, phone, note, created_at) VALUES (?,?,?,?)",
            (name.strip(), phone, note, now_local().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
        return int(cur.lastrowid), True
    finally:
        conn.close()


# Bitta tovarni bir nechta firmadan olish mumkin (masalan «Metr tic 5m» ham
# uzcolor'da, ham Epica'da bor). `products.supplier_id` bitta qiymat saqlaydi,
# shuning uchun bog'lanishlar ALOHIDA jadvalda. `products.supplier_id` esa
# ASOSIY yetkazib beruvchi bo'lib qoladi — eski admin ekranlari o'shani o'qiydi.
LINK_SCHEMA = """
CREATE TABLE IF NOT EXISTS product_suppliers (
    product_id   INTEGER NOT NULL,
    supplier_id  INTEGER NOT NULL,
    price        REAL    DEFAULT 0,   -- shu firmaning xarid narxi
    price_at     TEXT    DEFAULT '',  -- narx qaysi nakladnoydan olingani
    created_at   TEXT    NOT NULL,
    PRIMARY KEY (product_id, supplier_id)
);
CREATE INDEX IF NOT EXISTS idx_psup_supplier ON product_suppliers(supplier_id);
"""


def _ensure_links(conn: sqlite3.Connection) -> None:
    """Jadval yo'q bo'lsa yaratadi, eskisiga yangi ustunlarni qo'shadi.

    `CREATE TABLE IF NOT EXISTS` mavjud jadvalga ustun QO'SHMAYDI — jadval
    avvalroq narxsiz yaratilgan bo'lsa, yozish jim yiqilardi. Shuning uchun
    ustunlar alohida tekshiriladi.

    Faqat-o'qish ulanishida hammasi jim o'tadi (`list`/`show` bazani
    o'zgartirmasligi kerak).
    """
    try:
        conn.executescript(LINK_SCHEMA)
        have = {r[1] for r in conn.execute("PRAGMA table_info(product_suppliers)")}
        for col, decl in (("price", "REAL DEFAULT 0"), ("price_at", "TEXT DEFAULT ''")):
            if col not in have:
                conn.execute(f"ALTER TABLE product_suppliers ADD COLUMN {col} {decl}")
        conn.commit()
    except sqlite3.OperationalError:
        pass


def attach(supplier_id: int, product_ids: List[int],
           prices: Dict[int, float] = None) -> int:
    """Tovarlarga yetkazib beruvchi QO'SHADI (mavjudini o'chirmaydi).

    Avval zaxira nusxa oladi. `supplier_id=0` — hamma bog'lanishni yechish.
    """
    if not product_ids:
        return 0
    print(f"Zaxira nusxa: {_backup(config.POS_DB)}")
    conn = _connect(read_only=False)
    try:
        _ensure_links(conn)
        if not supplier_id:
            conn.executemany("DELETE FROM product_suppliers WHERE product_id=?",
                             [(pid,) for pid in product_ids])
            conn.executemany("UPDATE products SET supplier_id=0 WHERE id=?",
                             [(pid,) for pid in product_ids])
            conn.commit()
            return len(product_ids)

        ts = now_local().strftime("%Y-%m-%d %H:%M:%S")
        prices = prices or {}
        # Narx HAR SAFAR yangilanadi — oxirgi nakladnoy eng dolzarbi.
        # Bog'lanishning o'zi esa saqlanadi (takror import zarar qilmaydi).
        conn.executemany(
            """INSERT INTO product_suppliers(product_id, supplier_id, price, price_at, created_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(product_id, supplier_id) DO UPDATE SET
                   price = CASE WHEN excluded.price > 0 THEN excluded.price
                                ELSE product_suppliers.price END,
                   price_at = CASE WHEN excluded.price > 0 THEN excluded.price_at
                                   ELSE product_suppliers.price_at END""",
            [(pid, supplier_id, float(prices.get(pid) or 0), ts, ts)
             for pid in product_ids],
        )
        # Asosiy yetkazib beruvchi faqat BO'SH bo'lsa yoziladi — mavjud
        # biriktiruv ustidan yozilmaydi (aks holda oxirgi yuklangan fayl
        # avvalgilarini bosib ketardi).
        conn.executemany(
            "UPDATE products SET supplier_id=? WHERE id=? "
            "AND COALESCE(supplier_id,0)=0",
            [(supplier_id, pid) for pid in product_ids],
        )
        conn.commit()
        return len(product_ids)
    finally:
        conn.close()


def supplier_products(conn: sqlite3.Connection, sid: int) -> List[int]:
    """Yetkazib beruvchining tovar id'lari (bog'lanish jadvali + asosiy)."""
    _ensure_links(conn)
    rows = conn.execute(
        "SELECT product_id FROM product_suppliers WHERE supplier_id=? "
        "UNION SELECT id FROM products WHERE supplier_id=?", (sid, sid)
    ).fetchall()
    return [int(r[0]) for r in rows]


# ─── Buyruqlar ───────────────────────────────────────────────────────────────

def cmd_list(args) -> int:
    conn = _connect()
    try:
        _ensure_links(conn)
        # Tovar bir nechta firmaga biriktirilgan bo'lishi mumkin — sanoq
        # bog'lanish jadvali va asosiy `supplier_id` ni BIRLASHTIRIB olinadi.
        rows = conn.execute(
            """SELECT s.*,
                      (SELECT COUNT(*) FROM (
                          SELECT product_id AS pid FROM product_suppliers
                           WHERE supplier_id = s.id
                          UNION
                          SELECT id FROM products WHERE supplier_id = s.id
                       )) cnt
                 FROM suppliers s ORDER BY s.name"""
        ).fetchall()
        free = conn.execute(
            "SELECT COUNT(*) n FROM products WHERE is_active=1 "
            "AND COALESCE(supplier_id,0)=0 "
            "AND id NOT IN (SELECT product_id FROM product_suppliers)"
        ).fetchone()["n"]
    finally:
        conn.close()

    if not rows:
        print("Yetkazib beruvchi yo'q. Birinchisini import bilan yaratsangiz bo'ladi.")
    for r in rows:
        lead, cycle = _schedule(r)
        when = (f"{lead}+{cycle} kun" if (lead or cycle) else "standart muddat")
        print(f"  #{r['id']:<3} {r['name']:<28} {r['cnt']:>5} tovar  "
              f"{when:<15} {r['phone'] or ''}")
    print(f"\nBiriktirilmagan tovar: {free}")
    print("Muddat: yetkazish + kelish oralig'i. Belgilash:")
    print("  python -m marketplace.supplier_link schedule --supplier \"Nomi\" "
          "--lead 2 --cycle 7")
    return 0


def cmd_schedule(args) -> int:
    """Yetkazib beruvchining kelish tartibini belgilaydi.

    `--lead`  — buyurtma berilgandan tovar kelguncha necha kun.
    `--cycle` — agent necha kunda bir keladi (buyurtma shu oraliqqa yetishi kerak).
    """
    conn = _connect(read_only=False)
    try:
        sup = find_supplier(conn, args.supplier)
        if not sup:
            print(f"«{args.supplier}» topilmadi. Ro'yxat: supplier_link list")
            return 1
        conn.execute(
            "UPDATE suppliers SET lead_days=?, cycle_days=? WHERE id=?",
            (max(0, args.lead), max(0, args.cycle), sup["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    print(f"{sup['name']}: yetkazish {args.lead} kun + kelish oralig'i "
          f"{args.cycle} kun → buyurtma {args.lead + args.cycle} kunlik ehtiyojga "
          f"hisoblanadi.")
    return 0


def cmd_show(args) -> int:
    conn = _connect()
    try:
        sup = find_supplier(conn, args.supplier)
        if not sup:
            print(f"«{args.supplier}» topilmadi.")
            return 1
        ids = supplier_products(conn, sup["id"])
        rows = conn.execute(
            "SELECT id, name, qty FROM products WHERE is_active=1 AND id IN "
            f"({','.join('?' * len(ids))}) ORDER BY name", ids
        ).fetchall() if ids else []
    finally:
        conn.close()
    print(f"{sup['name']} — {len(rows)} tovar")
    for r in rows:
        print(f"  {r['id']:<8} {r['name'][:50]:<50} qoldiq {r['qty']:.0f}")
    return 0


def cmd_import(args) -> int:
    items = read_items(args.file)
    if not items:
        print("Faylda o'qiladigan qator topilmadi.")
        return 1

    conn = _connect()
    try:
        products = load_products(conn)
        sup_names = suppliers(conn)
        existing = find_supplier(conn, args.supplier)
    finally:
        conn.close()
    index = build_index(products)

    sure, maybe, missing = [], [], []
    for it in items:
        m = match_one(it, products, index)
        if m["status"] == "aniq":
            sure.append((it, m))
        elif m["status"] == "shubhali":
            maybe.append((it, m))
        else:
            missing.append(it)

    print(f"Fayl: {args.file}   qator: {len(items)}")
    print(f"Yetkazib beruvchi: {args.supplier}"
          + ("" if existing else "  (YANGI — yaratiladi)"))
    print(f"\nMOS KELDI ({len(sure)}):")
    for it, m in sure:
        p = m["product"]
        busy = ""
        if p["supplier_id"] and (not existing or p["supplier_id"] != existing["id"]):
            busy = f"  ⟵ hozir: {sup_names.get(p['supplier_id'], '?')}"
        print(f"  {p['id']:<8} {p['name'][:44]:<44} [{m['how']}, {m['score']:.2f}]{busy}")
        if it["name"] and fold(it["name"]) != p["norm"]:
            print(f"           ro'yxatda: {it['name'][:60]}")

    if maybe:
        print(f"\nSHUBHALI — qo'lda tanlang ({len(maybe)}):")
        for it, m in maybe:
            print(f"  ro'yxatda: {it['name'][:60]}   ({m['how']}, {m['score']:.2f})")
            for c in m["candidates"]:
                print(f"      → {c['id']:<8} {c['name'][:50]}")

    if missing:
        print(f"\nTOPILMADI ({len(missing)}):")
        for it in missing:
            print(f"  {it['raw'][:70]}")

    if not args.apply:
        print("\n(Sinov rejimi — hech narsa yozilmadi. Yozish uchun: --apply)")
        if maybe or missing:
            print("Shubhali/topilmaganlarni keyin qo'lda biriktirasiz:")
            print(f"  python -m marketplace.supplier_link assign "
                  f"--supplier \"{args.supplier}\" --sku 111,222")
        return 0

    sid, created = ensure_supplier(args.supplier)
    ids = [m["product"]["id"] for _it, m in sure]
    prices = {
        m["product"]["id"]: float(it["price"])
        for it, m in sure if (it.get("price") or "").replace(".", "").isdigit()
    }
    n = attach(sid, ids, prices)
    print(f"\n{'Yaratildi' if created else 'Mavjud'}: #{sid} {args.supplier}")
    print(f"Biriktirildi: {len(ids)} tovar ({n} qator yangilandi)")
    if maybe or missing:
        print(f"Qolgani ({len(maybe) + len(missing)}) — qo'lda biriktiring.")
    return 0


def cmd_assign(args) -> int:
    ids = [int(x) for x in re.split(r"[,\s]+", args.sku) if x.strip().isdigit()]
    if not ids:
        print("SKU ro'yxati bo'sh.")
        return 1
    sid, created = ensure_supplier(args.supplier)
    conn = _connect()
    try:
        rows = conn.execute(
            f"SELECT id, name FROM products WHERE id IN ({','.join('?' * len(ids))})", ids
        ).fetchall()
    finally:
        conn.close()
    found = {int(r["id"]) for r in rows}
    for r in rows:
        print(f"  {r['id']:<8} {r['name'][:50]}")
    for miss in set(ids) - found:
        print(f"  {miss:<8} — bunday tovar yo'q")
    n = attach(sid, sorted(found))
    print(f"{'Yaratildi' if created else 'Mavjud'}: #{sid} {args.supplier} — {n} tovar biriktirildi")
    return 0


def cmd_unlink(args) -> int:
    ids = [int(x) for x in re.split(r"[,\s]+", args.sku) if x.strip().isdigit()]
    n = attach(0, ids)
    print(f"{n} tovardan yetkazib beruvchi yechildi.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Yetkazib beruvchini tovarlarga biriktirish")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="yetkazib beruvchilar ro'yxati").set_defaults(fn=cmd_list)

    p = sub.add_parser("show", help="bitta yetkazib beruvchining tovarlari")
    p.add_argument("--supplier", required=True)
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("import", help="prixod ro'yxatidan biriktirish")
    p.add_argument("--supplier", required=True, help="kompaniya nomi")
    p.add_argument("--file", required=True, help="tovar ro'yxati fayli ('-' = stdin)")
    p.add_argument("--apply", action="store_true", help="haqiqatan yozish")
    p.set_defaults(fn=cmd_import)

    p = sub.add_parser("schedule", help="agent qachon kelishini belgilash")
    p.add_argument("--supplier", required=True)
    p.add_argument("--lead", type=int, default=3, help="buyurtmadan kelguncha (kun)")
    p.add_argument("--cycle", type=int, default=14, help="necha kunda bir keladi")
    p.set_defaults(fn=cmd_schedule)

    p = sub.add_parser("assign", help="sku bo'yicha qo'lda biriktirish")
    p.add_argument("--supplier", required=True)
    p.add_argument("--sku", required=True, help="vergul bilan: 111,222")
    p.set_defaults(fn=cmd_assign)

    p = sub.add_parser("unlink", help="biriktiruvni yechish")
    p.add_argument("--sku", required=True)
    p.set_defaults(fn=cmd_unlink)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
