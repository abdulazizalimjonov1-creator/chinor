"""Qoidalar qamramagan tovarlarni AI bilan kategoriyalash.

Qoidalar 6663 tovardan 4409 tasini (66%) ajratdi. Qolgani juda xilma-xil
("Kuvalda", "Krakadil knobkali", "SHayton 60sm") — har biriga qoida yozish
samarasiz, AI esa nomdan turini taxmin qila oladi.

MAXFIYLIK: AI ga FAQAT TOVAR NOMI yuboriladi. Narx, qoldiq, tannarx,
yetkazib beruvchi, mijoz — hech biri yuborilmaydi.

AI adashadi, shuning uchun:
  • faqat mavjud 16 kategoriyadan birini tanlashi mumkin, yangisini
    o'ylab topa olmaydi;
  • ishonchsiz bo'lsa "Boshqalar" deyishi so'raladi — noto'g'ri joyga
    tiqilgandan ko'ra kategoriyasiz qolgani yaxshiroq;
  • natijani admin Mini App'dan qo'lda tuzatadi.

Ishlatish:
    python -m marketplace.categorize_ai              # sinov (10 ta to'plam)
    python -m marketplace.categorize_ai --all        # hammasi, yozmasdan
    python -m marketplace.categorize_ai --all --apply
"""

import argparse
import os
import re
import sqlite3
import sys
from collections import Counter

from marketplace import config

config.load_bot_env()   # AI kalitlari chinor-bot/.env da
from marketplace.categorize import RULES, OTHER

# AI dvigateli chinor-bot ichida — zanjir, sovish va ogohlantirish o'sha yerda.
_BOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chinor-bot")
for _p in (_BOT_DIR, "/opt/chinor/chinor-bot"):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

CATEGORIES = [name for name, _ in RULES]
BATCH = 60          # bir so'rovda nechta nom
_SYSTEM = (
    "Sen qurilish mollari do'koni katalogini tartiblovchi yordamchisan. "
    "Senga tovar nomlari ro'yxati beriladi. Har biriga BERILGAN RO'YXATDAN "
    "bitta kategoriya tanlaysan.\n\n"
    "QAT'IY QOIDALAR:\n"
    "1. Faqat berilgan kategoriya nomlaridan birini yoz — yangisini "
    "O'YLAB TOPMA, nomini o'zgartirma.\n"
    "2. Ishonching komil bo'lmasa 'Boshqalar' deb yoz. Noto'g'ri "
    "kategoriya qo'ygandan ko'ra 'Boshqalar' yaxshiroq.\n"
    "3. Javob FAQAT quyidagi ko'rinishda bo'lsin, boshqa hech qanday "
    "matnsiz:\n   1. Kategoriya nomi\n   2. Kategoriya nomi\n"
    "4. Nechta nom berilgan bo'lsa, shuncha qator qaytar."
)


def _uncategorized(pos_db: str) -> list:
    conn = sqlite3.connect(f"file:{pos_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT id, name FROM products "
            "WHERE is_active = 1 AND COALESCE(category_id, 0) = 0 "
            "ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def _ask(names: list) -> list:
    """Nomlar ro'yxati → kategoriyalar ro'yxati (bir xil uzunlikda)."""
    from bot import ai_engine

    cats = "\n".join(f"- {c}" for c in CATEGORIES) + "\n- Boshqalar"
    listing = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(names))
    prompt = (f"KATEGORIYALAR:\n{cats}\n\n"
              f"TOVAR NOMLARI:\n{listing}\n\n"
              f"Har bir raqam uchun kategoriya yoz.")

    # Javob kamida uchdan bir tovarga tanish kategoriya bermasa — model
    # ishlamayapti (bema'ni matn yoki noto'g'ri shakl). Shunda dvigatel
    # keyingi modelga o'tadi, biz esa bo'sh natija bilan qolmaymiz.
    need = max(1, len(names) // 3)

    def _ok(text: str) -> bool:
        return sum(1 for c in parse_answer(text, len(names)) if c) >= need

    out = ai_engine.chat(prompt, _SYSTEM, validate=_ok)
    if not out:
        return []
    return parse_answer(out, len(names))


def parse_answer(out: str, count: int) -> list:
    """AI javobini raqamlangan kategoriyalar ro'yxatiga aylantiradi.

    NIMA UCHUN BU QADAR EHTIYOT: modellar javobni turlicha bezaydi va
    2026-08-05 da shu sababli 1900 ta tovar bo'limsiz qolib ketgan edi:

      1) Ba'zi model avval BERILGAN kategoriyalar ro'yxatini raqamlab
         qaytaradi ("1. Elektr asboblar … 17. Boshqalar"), keyin asl javobni
         yozadi. Eski tahlilchi shu "aks-sado"ni javob deb qabul qilardi.
      2) Ba'zisi "1. Tovar nomi — Kategoriya" ko'rinishida yozadi; eski
         tahlilchi butun qatorni kategoriya deb olardi.
      3) Ba'zisi javobdan keyin yana bir jadval chizadi (kategoriyasiz);
         eski tahlilchi ("oxirgisi g'olib") o'sha bo'sh jadvalni olardi.

    Yechim: qatordan FAQAT tanish kategoriya nomi qidiriladi (tire yoki
    ikki nuqtadan keyingi bo'lak birinchi navbatda), tanilmagan qator
    e'tiborsiz qoldiriladi, aks-sado bloki esa alohida aniqlanib tashlanadi.
    """
    hits = []          # (idx, kategoriya, aks-sadomi)
    for line in out.splitlines():
        m = re.match(r"\s*(\d+)\s*[.):\-–—]\s*(.+)", line.strip())
        if not m:
            continue
        idx = int(m.group(1)) - 1
        if not (0 <= idx < count):
            continue
        body = m.group(2).strip().strip("*_ ").rstrip(".")
        # "Nom — Kategoriya" / "Nom: Kategoriya" bo'lsa oxirgi bo'lak muhim
        tail = re.split(r"\s+[—–-]\s+|:\s+", body)[-1].strip()
        cat = _valid(tail) or _valid(body)
        if not cat:
            continue
        # Aks-sado belgisi: qatorda kategoriyadan boshqa hech narsa yo'q VA
        # u ro'yxatdagi aynan shu o'rindagi kategoriya.
        echo = (body.lower() == cat.lower()
                and idx < len(CATEGORIES)
                and CATEGORIES[idx].lower() == cat.lower())
        hits.append((idx, cat, echo))

    # Ketma-ket 5 tadan ko'p "aks-sado" bo'lsa — bu ro'yxatning nusxasi,
    # javob emas. (Bitta-ikkitasi tasodifan to'g'ri kelishi mumkin, ular
    # haqiqiy javob bo'lishi ham ehtimol — shuning uchun chegara qo'yamiz.)
    run = best = 0
    for _, _, echo in hits:
        run = run + 1 if echo else 0
        best = max(best, run)
    drop_echo = best >= 5

    got = {}
    for idx, cat, echo in hits:
        if drop_echo and echo:
            continue
        got[idx] = cat          # keyingi bo'lim oldingisini yangilaydi
    return [got.get(i, "") for i in range(count)]


def _valid(cat: str) -> str:
    """AI qaytargan nomni ro'yxatdagi aniq nomga moslaydi."""
    c = (cat or "").strip().lower()
    if not c:
        return ""
    for known in CATEGORIES:
        if c == known.lower():
            return known
    # Yaqin moslik (AI emoji yoki ortiqcha so'z qo'shishi mumkin)
    for known in CATEGORIES:
        if known.lower() in c or c in known.lower():
            return known
    return OTHER if "boshqa" in c else ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AI bilan kategoriyalash")
    ap.add_argument("--apply", action="store_true", help="pos.db ga yozish")
    ap.add_argument("--all", action="store_true", help="hammasi (aks holda 10 to'plam)")
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--pos-db", default=config.POS_DB)
    args = ap.parse_args(argv)

    if not os.path.exists(args.pos_db):
        print(f"pos.db topilmadi: {args.pos_db}")
        return 1

    rows = _uncategorized(args.pos_db)
    print(f"Kategoriyasiz tovar: {len(rows):,}")
    if not rows:
        return 0

    batches = [rows[i:i + args.batch] for i in range(0, len(rows), args.batch)]
    if not args.all:
        batches = batches[:10]
    print(f"To'plam: {len(batches)} ta × {args.batch} nom\n")

    assign, stats, failed = {}, Counter(), 0
    for n, chunk in enumerate(batches, 1):
        got = _ask([r["name"] for r in chunk])
        if not got:
            failed += 1
            print(f"  [{n}/{len(batches)}] AI javob bermadi")
            continue
        ok = 0
        for r, raw in zip(chunk, got):
            cat = _valid(raw)
            if cat and cat != OTHER:
                assign[r["id"]] = cat
                stats[cat] += 1
                ok += 1
            elif cat == OTHER:
                stats[OTHER] += 1
        print(f"  [{n}/{len(batches)}] {ok}/{len(chunk)} ajratildi")

    print(f"\nAI ajratdi: {len(assign):,} ta")
    if failed:
        print(f"Javob bermagan to'plam: {failed}")
    for c, k in stats.most_common():
        print(f"  {k:>5}  {c}")

    if not args.apply:
        print("\n[sinov] Hech narsa yozilmadi. Yozish uchun: --apply")
        return 0
    if not assign:
        return 0

    from marketplace.categorize import apply_to_db
    made, updated = apply_to_db(args.pos_db, assign)
    print(f"\n✓ {made} yangi kategoriya, {updated:,} tovar yangilandi.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
