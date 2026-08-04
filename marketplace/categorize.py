"""Tovarlarni kategoriyalarga ajratish — kalit so'z qoidalari bilan.

Nima uchun qoidalar: 6663 tovarning bittasida ham kategoriya yo'q edi, nomlar
esa brend, tur va xususiyatni aralashtirib yuborgan ("Dusel smesitel 1/2",
"RTR truba 20mm", "Kerasys shampun"). Birinchi so'z bo'yicha ajratish
ishlamaydi — 1287 xil birinchi so'z bor, top-40 tasi 23% ni qamraydi.
Shuning uchun tur so'zi nomning ISTALGAN joyidan qidiriladi.

Tartib muhim: ro'yxat yuqoridan pastga tekshiriladi, birinchi moslik g'olib.
Shuning uchun aniqrog'i yuqorida turadi (masalan "elektr asbob" oddiy
"asbob"dan oldin, "shlanka gaz" gaz bo'limida emas santexnikada).

Ishlatish:
    python -m marketplace.categorize            # faqat tahlil (hech narsa yozilmaydi)
    python -m marketplace.categorize --apply    # pos.db ga yozadi
    python -m marketplace.categorize --show "Bolta yog'och"   # bitta nomni sinash
"""

import argparse
import os
import re
import sqlite3
import sys
from collections import Counter

from marketplace import config

# (kategoriya, kalit so'zlar). Kalit so'z nomning istalgan joyidan qidiriladi.
RULES = [
    ("Elektr asboblar",
     "drel perforator bolgarka shurupovert lobzik shlifmashina kompressor svarka "
     "payalnik fen stanok nasos motopompa generator gaykovert shtroborez "
     "pilesos pylesos vibrator zatirochn elektrod svarka- invertor apparat"),

    ("Elektr mollari",
     "sim kabel provod led lampa lampochka rozetka vyklyuchat viklyuchat avtomat "
     "patron udlinitel shteker shtekr schetchik schyot uzo svetilnik projektor "
     "prozhektor izolenta klemma vilka drossel starter zvonok nakanechni "
     "simistitel dimmer trans avtomatik knopka lenta-led nochnik "
     "chint era epa iek tdm vkl ramka-roz rozet perexod-elektr indikator fulzator plata datchik trans- pribor tester avtomat- rele kontaktor pusk"),

    ("Santexnika",
     "unitaz smesitel smestitl kran truba turba shlanka mufta rakovina sifon "
     "dush vanna bachok gofra sgon amerikanka trap sushilka polotenс ugolnik "
     "troynik zaglushka kolen fum sanfayans ppr pn20 pn10 kanalizatsi "
     "leyka bide pissuar germetik-santex vodoschet filtr-voda traynik troynik filtr-suv smestitl smesit tiaga formul-truba prokladka manjet armatura-bach"),

    ("Bo'yoq va lak",
     "emal emultsiya emulsiya lak kraska gruntovka grunt valik chotka kist "
     "shpatel rastvoritel pigment koler olifa antiseptik propitka balonchik "
     "aerozol pentafta akril-kraska vodoemul rang- cho'tka chotka shipatel shpatl akril rolik valik- silikon pena-mont germetik-sil shpritс-germ"),

    ("Qurilish aralashmasi",
     "sement gips rotband shpaklovka shpaklyovka plasterm alebastr albaster "
     "kley klei plitochn zatirka penoplast gipsokarton knauf ceresit hayat "
     "shtukaturka styajka nivelir peskobeton germetik penopolistirol "
     "montajn-pena penа-mont setka setka-armir profil-gips mayachok"),

    ("Sochma material",
     "qum shag'al shagal gisht klines turpoq keramzit sheben otsev "
     "dastafka dastavka"),

    ("Mahkamlagichlar",
     "mix shurup bolt gayka shayba xomut pona klipsa dyubel anker samorez "
     "zaklepka gvozd gvazd vint shpilka gayka-bar rezba skoba-mebel "
     "podshipnik padshivnik samarez samaruz shurip zaklopka zaklyopka gaykа bolt- shpilk"),

    ("Asboblar",
     "klyuch sverlo bur disk pichoq bolta bolg'a bolga arra otvertka atyorka "
     "nasadka metr ruletka uroven passatij plaskagupsa kusachka molotok zubilo "
     "napilnik tiski strubsina stusla kelma mastrok stamiska rubanok ombur "
     "givazdyor gvozdodyor trishotka nayza stepler nojnitsa qaychi tochilka "
     "almazniy koronka bit-nabor otbойnik keskich egov kluch klyuc stameska stamiska masterok mastrok qisqich qiskich qisgich kisqich trishodkali nabor-instr otvyortka atvyorka plaskagubsa nojovka shukurka jirafa karkaz-toqi pistolet derjatel qalam marker keskish rezak burg'i burgi kalibr shtangen tros-asbob domkrat lom kirka bel ketmon panshaxa"),

    ("Eshik va deraza",
     "qulf zamok ruchka petlya shpingalet dovodchik zadvijka lichinka oyna "
     "steklo profil podokonnik nalichnik porog dverni okonn moskitn"),

    ("Isitish va gaz",
     "gaz ballon gorelka radiator konvektor kotel batareya-otop termostat "
     "reduktor obogrevatel teplovent kamin dымохоd balon-gaz gaz-balon ko'mir komir"),

    ("Maishiy texnika",
     "pult dazmol naushnik fanar batareyka soat adapter zaryadka usb "
     "kolonka mikser blender chaynik-elektr tarozi ventilyator ventum "
     "uzatgich perexodnik power-bank flesh televizor antenna ventilyatr ventilyator ventil-"),

    ("Oshxona buyumlari",
     "qozon termos tog'ora togora stakan kastryul tarelka chashka choynak "
     "qoshiq vilka-osh pichoq-osh lagan taxta-osh konteyner banka shisha "
     "krujka podnos doska sito duxovka forma keks skovorod chelak-osh "
     "andava oshxona idish bokal fujer servis-osh"),

    ("Uy-ro'zg'or",
     "shvabra vedro taz chelak korzinka veshalka polka gultuvak qopqoq "
     "shetka-uy prishepka verёvka arqon ilgak stul stol gilam parda "
     "mebelni zerkalo skoch lipuchka lipuchkali bumaga tros arqon- shnur lenta-uy plyonka polietilen brezent"),

    ("Tozalash vositalari",
     "grass sovun poroshok gel vanish domestos salfetka gubka sochiq "
     "tualet-qog paket meshok qop perchatka qo'lqop tryapka chistyash "
     "otbelivat pyatnovyvod osvejitel"),

    ("Shaxsiy parvarish",
     "shampun kerasys head-shoulders krem pasta-tish tish-cho tualet-suv "
     "dezodorant britva stanok-brit vata pampers salfetka-vlaj mochalka "
     "shampun- balzam kondit-vol"),

    ("Ish kiyimi va himoya",
     "kiyim kombinezon nakolennik ochki-zash maska respirator naushnik-zash "
     "kaska jilet fartuk etik-rez botinka zubr ochki naushnik-"),
]

_RULES = [(name, [k for k in kws.split() if k]) for name, kws in RULES]
OTHER = "Boshqalar"


def classify(name: str) -> str:
    """Tovar nomi → kategoriya nomi. Topilmasa 'Boshqalar'."""
    nm = " " + re.sub(r"[,./()\-_]+", " ", (name or "").lower()) + " "
    toks = set(nm.split())
    for cat, kws in _RULES:
        for k in kws:
            if "-" in k:                      # "batareya-otop" → ikkalasi ham bo'lsin
                if all(part in nm for part in k.split("-") if part):
                    return cat
            elif k in toks or (len(k) > 4 and k in nm):
                return cat
    return OTHER


def _rows(pos_db: str):
    conn = sqlite3.connect(f"file:{pos_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT id, name, category_id FROM products WHERE is_active = 1"
        ).fetchall()
    finally:
        conn.close()


def analyze(pos_db: str) -> tuple:
    rows = _rows(pos_db)
    hits = Counter()
    rest = []
    assign = {}
    for r in rows:
        cat = classify(r["name"])
        hits[cat] += 1
        assign[r["id"]] = cat
        if cat == OTHER:
            rest.append(r["name"])
    return rows, hits, rest, assign


def apply_to_db(pos_db: str, assign: dict) -> tuple:
    """Kategoriyalarni yaratib, tovarlarga biriktiradi. Avval zaxira nusxa."""
    import shutil
    from datetime import datetime
    bak = f"{pos_db}.bak-cat-{datetime.now():%Y%m%d-%H%M%S}"
    shutil.copy2(pos_db, bak)
    print(f"Zaxira nusxa: {bak}")

    conn = sqlite3.connect(pos_db, timeout=30)
    made = updated = 0
    try:
        conn.execute("BEGIN")
        ids = {}
        for cat in sorted(set(assign.values())):
            if cat == OTHER:
                continue          # "Boshqalar" uchun kategoriya yaratilmaydi
            r = conn.execute(
                "SELECT id FROM categories WHERE LOWER(name) = LOWER(?)", (cat,)
            ).fetchone()
            if r:
                ids[cat] = r[0]
            else:
                # `created_at` NOT NULL — bot bilan bir xil format ishlatamiz.
                cur = conn.execute(
                    "INSERT INTO categories (name, created_at) VALUES (?, ?)",
                    (cat, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                ids[cat] = cur.lastrowid
                made += 1
        for pid, cat in assign.items():
            cid = ids.get(cat)
            if cid:
                conn.execute(
                    "UPDATE products SET category_id = ? WHERE id = ?", (cid, pid))
                updated += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return made, updated


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Tovarlarni kategoriyalash")
    ap.add_argument("--apply", action="store_true", help="pos.db ga yozish")
    ap.add_argument("--pos-db", default=config.POS_DB)
    ap.add_argument("--show", help="bitta nomni sinab ko'rish")
    ap.add_argument("--rest", type=int, default=15, help="nechta qamralmaganini ko'rsatish")
    args = ap.parse_args(argv)

    if args.show:
        print(f"{args.show!r} → {classify(args.show)}")
        return 0
    if not os.path.exists(args.pos_db):
        print(f"pos.db topilmadi: {args.pos_db}")
        return 1

    rows, hits, rest, assign = analyze(args.pos_db)
    tot = len(rows)
    cov = tot - hits[OTHER]
    print(f"Jami faol tovar : {tot:,}")
    print(f"Kategoriyalandi : {cov:,}  ({cov / tot * 100:.0f}%)")
    print(f"Boshqalar       : {hits[OTHER]:,}\n")
    for cat, n in hits.most_common():
        mark = "  " if cat != OTHER else "← "
        print(f"  {n:>5}  {mark}{cat}")
    if rest and args.rest:
        print(f"\nQamralmaganlardan {min(args.rest, len(rest))} ta namuna:")
        for x in rest[:args.rest]:
            print(f"    {x[:56]}")

    if not args.apply:
        print("\n[tahlil] Hech narsa yozilmadi. Yozish uchun: --apply")
        return 0

    made, updated = apply_to_db(args.pos_db, assign)
    print(f"\n✓ {made} kategoriya yaratildi, {updated:,} tovarga biriktirildi.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
