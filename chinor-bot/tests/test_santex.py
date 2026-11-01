"""Quvur-fiting nomlari qoidasi — do'kondagi HAQIQIY nomlar bilan sinov.

Nega kerak: bu qoida bo'limni (toza suv / kanalizatsiya), turni va
o'lchamni AI'siz aniqlaydi. Nomlar imlosi turlicha («Atvod/Atvot»,
«Mufta/Muftra», «Amerikanka/Amerkanka») — birorta variant tushib qolsa
tovar yana «Santexnika» uyumiga qaytib qoladi va buni faqat katalogni
ko'zdan kechirganda bilib qolasiz.

Ishga tushirish (baza, server, internet kerak emas):

    python3 chinor-bot/tests/test_santex.py
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))          # chinor-bot/
os.environ.setdefault("DATA_DIR", os.path.join(HERE, "_data"))

from bot import santex_rules as R                  # noqa: E402

_fail = []


def check(nom: str, shart: bool, izoh: str = "") -> None:
    print(f"  {'✅' if shart else '❌'} {nom}{(' — ' + izoh) if izoh and not shart else ''}")
    if not shart:
        _fail.append(nom)


# (nom, tizim, tur, diametr, rezba)
NAMUNA = [
    ("RTR 20 Mufta",                      "rtr", "mufta",      20, ""),
    ("Rtr 40 Muftra",                     "rtr", "mufta",      40, ""),      # imlo xatosi
    ("RTR 20 Atvod",                      "rtr", "otvod",      20, ""),
    ("RTR 50 atvod",                      "rtr", "otvod",      50, ""),
    ("Rtr 25 Polatvod",                   "rtr", "polotvod",   25, ""),      # 45°, "atvod" emas
    ("RTP 110 Atvot",                     "rtp", "otvod",      110, ""),
    ("RTP 75 Polatvot",                   "rtp", "polotvod",   75, ""),
    ("RTR 32 Traynik 32*20*32",           "rtr", "troynik",    32, ""),
    ("RTR 25 Krestovina formul",          "rtr", "krestovina", 25, ""),
    ("RTR 20 Adapter Ichki rezba 20/15",  "rtr", "adapter",    20, "15"),    # 20 quvur, 1/2" rezba
    ("RTR 25 Adapter 20 ichki rezba",     "rtr", "adapter",    25, "20"),
    ("Rtr 32 Amerikanka  tashqi rezba 32/25 rezva", "rtr", "amerikanka", 32, "25"),
    ("RTR 20 Amerkanka ichki 15 rezba",   "rtr", "amerikanka", 20, "15"),    # imlo xatosi
    ("RTR 20 Ushastik tashqi 15 rezba",   "rtr", "ushastik",   20, "15"),
    ("Rtr 25 Ushastik para 25/15rezba",   "rtr", "ushastik",   25, "15"),
    ("RTR 25 Perexadnik 25*20",           "rtr", "perexodnik", 25, ""),
    ("RTR 40/32  Perehadnik",             "rtr", "perexodnik", 40, ""),      # imlo xatosi
    ("Rtr 32 sharavoy kran",              "rtr", "kran",       32, ""),
    ("RTR klipsa F20",                    "rtr", "klipsa",     20, ""),
    ("RTR zaglushka 15 tashqi  rezba",    "rtr", "zaglushka",  0,  "15"),    # o'lchami rezbada
    ("RTR 32 Turba issiq suv",            "rtr", "turba",      32, ""),
    ("RTR  Topliy pol turbasi",           "rtr", "turba",      0,  ""),
]

print("\n1) Nomdan tizim / tur / o'lcham:")
for nom, sys_, kind, dn, thread in NAMUNA:
    d = R.parse(nom)
    ok = (d["system"] == sys_ and d["kind"] == kind
          and d["dn"] == dn and d["thread"] == thread)
    check(nom[:44], ok,
          f'olindi: {d["system"]}/{d["kind"]}/{d["dn"]}/{d["thread"]!r}')

print("\n1b) MPP (Miramax, O'zbekiston) — bo'lim DIAMETR bilan ajraladi:")
# Do'kon egasining qoidasi: MPP 20/25/32/40 — toza suv, 50/75/100/150 —
# kanalizatsiya. Bitta belgi ostida ikki xil tizim turadi.
for nom, kutilgan in [("MPP 25 Mufta", "Toza suv quvurlari"),
                      ("MPP 40 Zaglujka  payka", "Toza suv quvurlari"),
                      ("MPP 50 Atvod", "Kanalizatsiya quvurlari"),
                      ("MPP 100 Traynik kassoy", "Kanalizatsiya quvurlari"),
                      ("MPP 150 Turba 200 sm   (3mm)", "Kanalizatsiya quvurlari")]:
    check(nom[:38], R.category(nom) == kutilgan, R.category(nom))
check("qiyshiq troynik ajratildi",
      R.parse("MPP 100 Traynik kassoy")["kind"] == "troynik_kos")
check("to'g'ri troynik ajratildi",
      R.parse("MPP 100 traynik to'g'risi")["kind"] == "troynik")
check("uzunlik o'qildi", R.parse("MPP 100 Turba 25 sm")["length"] == "25 sm")
check("1 metr → 100 sm", R.parse("MPP 75 Turba  1 m")["length"] == "100 sm")
check("uzunlik diametrni buzmadi", R.parse("MPP 100 Turba 25 sm")["dn"] == 100)
check("devor qalinligi diametr emas",
      R.parse("MPP 150 Turba 200 sm   (3mm)")["dn"] == 150)
check("chuyan o'tkazgich — kanalizatsiya",
      R.category("RTR perexod chugunniy 50/75") == "Kanalizatsiya quvurlari")
check("Miramax ma'lumoti", "O'zbekistonda" in R.brand_text("mpp"))

check("chuyan TOVA quvur emas", R.category("Skavarodka chugunniy") == "", R.category("Skavarodka chugunniy"))
check("chuyan manjet — kanalizatsiya",
      R.category("Rezina Chugun  uchun manjet 100") == "Kanalizatsiya quvurlari")

print("\n2) Bo'lim:")
check("RTR → toza suv", R.category("RTR 25 Mufta") == "Toza suv quvurlari")
check("RTP → kanalizatsiya", R.category("RTP 50 Atvot") == "Kanalizatsiya quvurlari")
check("begona nom → bo'sh", R.category("Knauf Rotband 30 kg") == "")

print("\n3) Fakt satri:")
f = R.facts("RTR 20 Adapter Ichki rezba 20/15")
check("rezba dyuymda ham", '1/2"' in f, f)
check("diametr mm da", "20 mm" in f, f)
check("tizim yozilgan", "Toza suv" in f, f)

print("\n4) Rasm o'rniga NOM: bir xil surat — har xil o'lcham")
# Do'konda 20/25/40 polotvod bitta surat bilan turadi. Qoida nomdan
# o'lchamni ajratadi — rasm buni hech qachon ayta olmaydi.
o = [R.parse(f"Rtr {d} Polatvod")["dn"] for d in (20, 25, 40)]
check("20/25/40 ajratildi", o == [20, 25, 40], str(o))

print("\n5) Issiq/sovuq quvur izohi (surat ayta olmaydigan farq):")
check("issiqda armatura qatlami",
      "armatura" in R.note("RTR 32 Turba issiq suv"), R.note("RTR 32 Turba issiq suv"))
check("sovuqda ichi tekis",
      "tekis" in R.note("Rtr 32 turba sovuq suv"), R.note("Rtr 32 turba sovuq suv"))
check("fitingda izoh yo'q", R.note("RTR 25 Mufta") == "")

print("\n6) Zavod ma'lumoti (AI promptiga ketadi):")
bt = R.brand_text("rtp")
check("zavod nomi", "РосТурПласт" in bt)
check("2005 yil", "2005" in bt)
check("4 zavod", "Yekaterinburg" in bt and "Krasnodar" in bt)
check("kanalizatsiya tuzilishi", "egiluvchan" in bt.lower(), bt[-200:])
check("do'kon tajribasi ajratilgan", "DO'KON TAJRIBASI" in bt)
check("toza suvda armatura", "shisha tolali" in R.brand_text("rtr"))

print()
if _fail:
    print(f"❌ {len(_fail)} ta tekshiruv yiqildi")
    sys.exit(1)
print("✅ Hammasi joyida")
