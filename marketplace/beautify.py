"""Tovar nomini chiroyli ko'rinishga keltiradi → `products.display_name`.

MUHIM: nom QISQARTIRILMAYDI. Barcha ma'lumot saqlanadi — o'lcham (300mm),
og'irlik (2,5kg), model (N3, PN20) va artikul kodi (LIT-185043). Ular
tovarni aniqlaydi, ularsiz "Bolg'a" degan o'nlab tovar bir xil ko'rinardi.

Nima o'zgaradi:
  • Imlo bir xillashtiriladi: shipatel/shpatel/Shpatel → Shpatel,
    samarez → Samorez, kluch → Klyuch, traynik → Troynik.
  • Bosh harf: birinchisi katta, qolgani kichik. Lekin kodlar va
    o'lchov birliklari o'z holicha qoladi (LIT-185043, PN20, 2,5kg).
  • Ortiqcha bo'sh joy va tinish belgilari tozalanadi.

Asl `name` TEGILMAYDI — kassada xodimlar tovarni o'sha nom bilan taniydi.

Ishlatish:
    python -m marketplace.beautify                    # namuna ko'rsatadi
    python -m marketplace.beautify --show "SHipatel ABOY 300MM lit-181607"
    python -m marketplace.beautify --apply            # display_name ga yozadi
"""

import argparse
import os
import re
import sqlite3
import sys

from marketplace import config

# ── Imlo bir xillashtirish ─────────────────────────────────────────────────
# Chapda — bazada uchraydigan noto'g'ri/turli variantlar, o'ngda — to'g'risi.
# Faqat butun so'z sifatida almashtiriladi, so'z ichida emas.
SPELLING = {
    "shipatel": "shpatel", "shpatl": "shpatel", "shipatl": "shpatel",
    "samarez": "samorez", "samaruz": "samorez", "shurip": "shurup",
    "kluch": "klyuch", "klyuc": "klyuch", "kalyuch": "klyuch",
    "traynik": "troynik", "troyni": "troynik",
    "zakllopka": "zaklopka", "zaklyopka": "zaklopka",
    "stamiska": "stameska", "stameksa": "stameska",
    "mastrok": "masterok", "atyorka": "otvyortka", "atvyorka": "otvyortka",
    "plaskagupsa": "plaskagubsa", "plasmas": "plastmas", "plastmass": "plastmas",
    "aboy": "oboy", "aboy": "oboy",
    "qiskich": "qisqich", "qisgich": "qisqich", "kisqich": "qisqich",
    "smestitel": "smesitel", "smestitl": "smesitel", "smesit": "smesitel",
    "turba": "truba", "givazdyor": "gvozdodyor", "gvazdyor": "gvozdodyor",
    "chotka": "cho'tka", "shotka": "cho'tka",
    "togora": "tog'ora", "tugora": "tog'ora",
    "ventilyatr": "ventilyator", "ventilyator": "ventilyator",
    "emultsiya": "emulsiya", "gruntovka": "gruntovka",
    "vyklyuchat": "vyklyuchatel", "viklyuchatel": "vyklyuchatel",
    "adnarazoviy": "bir martalik", "arzoni": "arzon", "qimmati": "qimmat",
    "kotta": "katta", "oddiy": "oddiy",
}

# Shu ko'rinishdagi bo'laklar O'Z HOLICHA qoladi (kichik harfga tushmaydi):
#   LIT-185043, N3, PN20, 3CD-125, 40x60, 2,5kg, 300mm, 1/2
_KEEP = re.compile(
    r"^(?:"
    r"[A-Za-z]{2,4}[-–]?\d{3,}"        # LIT-185043, EESD-0
    r"|[A-Za-z]{1,3}\d{1,4}"           # N3, PN20, D16
    r"|\d+[.,]?\d*\s*(?:mm|sm|cm|m|kg|g|l|ml|kv|vt|w|a|v)\b"   # 300mm, 2,5kg
    r"|\d+\s*[x×]\s*\d+"               # 40x60
    r"|\d+/\d+"                        # 1/2
    r"|\d+[.,]\d+"                     # 0.5
    r")$", re.IGNORECASE)


_UNIT = re.compile(r"^(\d+[.,]?\d*)\s*([A-Za-z]+)$")


def _tidy_code(c: str) -> str:
    """Kod va o'lchamni tartiblaydi: LIT-181607 KATTA, 300MM → 300mm."""
    m = _UNIT.match(c)
    if m:                       # o'lchov birligi — kichik harf
        return m.group(1) + m.group(2).lower()
    if re.match(r"^[A-Za-z]{2,4}[-–]?\d{3,}$", c):   # artikul kodi — KATTA
        return c.upper()
    return c.upper() if len(c) <= 4 else c


def _fix_word(w: str) -> str:
    low = w.lower()
    return SPELLING.get(low, low)


def beautify(name: str) -> str:
    """Asl nom → chiroyli nom. Ma'lumot yo'qotilmaydi."""
    s = (name or "").strip()
    if not s:
        return ""
    # Ortiqcha bo'sh joy va takroriy tinish belgilari
    s = re.sub(r"\s+", " ", s)
    # Vergul faqat SO'ZLAR orasida ajratiladi — "0,5" kabi o'nlik kasrga
    # tegilmaydi (aks holda "0, 5" bo'lib ketardi).
    s = re.sub(r"\s*([,;])\s*(?=[^\d])", r"\1 ", s)
    s = re.sub(r"\s*([/×x])\s*(?=\d)", r"\1", s)

    out = []
    for w in s.split(" "):
        if not w:
            continue
        # Qavs va tinish belgilarini vaqtincha ajratamiz — "(N4)" ham kod
        # sifatida tanilishi kerak.
        lead = re.match(r"^[(\[«\"']*", w).group(0)
        trail = re.search(r"[)\]»\"'.,;:]*$", w).group(0)
        core = w[len(lead):len(w) - len(trail)] if trail else w[len(lead):]
        if not core:
            out.append(w)
            continue
        if _KEEP.match(core):
            out.append(lead + _tidy_code(core) + trail)
        else:
            out.append(lead + _fix_word(core) + trail)

    res = " ".join(out).strip()
    if not res:
        return ""
    # Birinchi harf katta, qolgani o'z holicha (kodlar allaqachon saqlangan)
    return res[0].upper() + res[1:]


def _rows(pos_db: str):
    conn = sqlite3.connect(f"file:{pos_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT id, name, display_name FROM products WHERE is_active = 1"
        ).fetchall()
    finally:
        conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Tovar nomini chiroyli qilish")
    ap.add_argument("--apply", action="store_true", help="display_name ga yozish")
    ap.add_argument("--show", help="bitta nomni sinash")
    ap.add_argument("--limit", type=int, default=25, help="nechta namuna")
    ap.add_argument("--pos-db", default=config.POS_DB)
    ap.add_argument("--overwrite", action="store_true",
                    help="qo'lda qo'yilgan display_name ustidan ham yozish")
    args = ap.parse_args(argv)

    if args.show:
        print(f"  {args.show!r}\n→ {beautify(args.show)!r}")
        return 0
    if not os.path.exists(args.pos_db):
        print(f"pos.db topilmadi: {args.pos_db}")
        return 1

    rows = _rows(args.pos_db)
    changed = [(r["id"], r["name"], beautify(r["name"])) for r in rows
               if beautify(r["name"]) and beautify(r["name"]) != r["name"]
               and (args.overwrite or not (r["display_name"] or "").strip())]

    print(f"Jami faol tovar : {len(rows):,}")
    print(f"O'zgaradi       : {len(changed):,}\n")
    for _, old, new in changed[:args.limit]:
        print(f"  {old[:46]}")
        print(f"→ {new[:46]}\n")

    if not args.apply:
        print("[namuna] Hech narsa yozilmadi. Yozish uchun: --apply")
        return 0
    if not changed:
        return 0

    import shutil
    from datetime import datetime
    bak = f"{args.pos_db}.bak-name-{datetime.now():%Y%m%d-%H%M%S}"
    shutil.copy2(args.pos_db, bak)
    print(f"Zaxira nusxa: {bak}")

    conn = sqlite3.connect(args.pos_db, timeout=30)
    try:
        conn.execute("BEGIN")
        conn.executemany(
            "UPDATE products SET display_name = ? WHERE id = ?",
            [(new, pid) for pid, _, new in changed])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(f"\n✓ {len(changed):,} tovarga chiroyli nom yozildi.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
