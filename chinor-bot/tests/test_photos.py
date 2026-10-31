"""Tovar suratlari tekshiruvi — "o'z surati" qoidasi buzilib qolmasin.

Nega kerak: shu qoida bo'yicha SAYT va MIJOZ ilovasi qaysi tovarni
ko'rsatishini hal qiladi. Qoida xato ishlasa ikki tomonga ham og'riydi —
yo internetdan olingan takroriy rasm yana chiqib ketadi, yo o'zimiz
suratga olgan tovar mijozga ko'rinmay qoladi. Skript baza, server va
internetsiz ishlaydi:

    python3 chinor-bot/tests/test_photos.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))          # chinor-bot/
os.environ.setdefault("DATA_DIR", os.path.join(HERE, "_data"))

from bot import api_photos as P                     # noqa: E402

_fail = []


def check(nom: str, shart: bool, izoh: str = "") -> None:
    print(f"  {'✅' if shart else '❌'} {nom}{(' — ' + izoh) if izoh and not shart else ''}")
    if not shart:
        _fail.append(nom)


# ─── 1. Fayl nomidan kim egasi ─────────────────────────────────────────────

print("\n1) Rasm kimniki:")
check("o'zimiz yuklagan", P.photo_owner("/api/img/prod_1234_1699999999.jpg") == 1234)
check("brendlangan nusxa", P.photo_owner("/api/img/prod_1234_1699999999_br.jpg") == 1234)
check("yangi tovarga yuklangan", P.photo_owner("/api/img/prod_new_17.jpg") == "new")
check("umumiy (turi bo'yicha) rasm", P.photo_owner("/api/img/type_smesitel.jpg") is None)
check("tashqi havola", P.photo_owner("https://example.com/kran.jpg") is None)
check("bo'sh", P.photo_owner("") is None)


# ─── 2. Tovar o'z suratlimi ────────────────────────────────────────────────

print("\n2) Tovarning o'z surati:")
oz = {"id": 1234, "image_url": "/api/img/prod_1234_1699_br.jpg"}
begona = {"id": 55, "image_url": "/api/img/prod_1234_1699_br.jpg"}
umumiy = {"id": 56, "image_url": "/api/img/type_kran.jpg"}
bosh = {"id": 57, "image_url": ""}
check("o'zining surati", P.is_own(oz))
check("boshqa tovarning surati emas", not P.is_own(begona))
check("umumiy rasm emas", not P.is_own(umumiy))
check("rasmsiz", not P.is_own(bosh))

# Bitta surat ikki tovarda: ASL egasi (nomidagi id) saqlab qoladi,
# nusxa olgani yo'qotadi — aks holda to'g'ri suratli tovar ham yo'qolardi.
ikki = [{"id": 70, "image_url": "/api/img/prod_70_1.jpg"},
        {"id": 71, "image_url": "/api/img/prod_70_1.jpg"}]
shared = P.shared_urls(ikki)
check("asl egasi saqlab qoladi", P.is_own(ikki[0], shared))
check("nusxa olgani yo'qotadi", not P.is_own(ikki[1], shared))
# Egasi noma'lum surat (prod_new_…) takrorlansa — umumiy
yangi = [{"id": 90, "image_url": "/api/img/prod_new_5.jpg"},
         {"id": 91, "image_url": "/api/img/prod_new_5.jpg"}]
check("egasiz surat takrorlansa umumiy",
      not P.is_own(yangi[0], P.shared_urls(yangi)))

royxat = [oz, begona, umumiy, bosh, {"id": 80, "image_url": "/api/img/prod_new_9.jpg"}]
check("suzgich faqat o'z suratlilarini qoldiradi",
      [p["id"] for p in P.filter_own(royxat)] == [1234, 80],
      str([p["id"] for p in P.filter_own(royxat)]))


# ─── 3. Tozalash: baza va fayllar ──────────────────────────────────────────

class _FakeDB:
    """Haqiqiy baza o'rniga — faqat `_lock` va `_conn()` kerak bo'ladi."""

    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()

    def _conn(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c


print("\n3) Umumiy rasmlarni tozalash:")
tmp = tempfile.mkdtemp(prefix="chinor-photos-")
up = os.path.join(tmp, "uploads")
os.makedirs(up)
dbp = os.path.join(tmp, "t.db")
con = sqlite3.connect(dbp)
con.executescript(
    "CREATE TABLE products(id INTEGER PRIMARY KEY, image_url TEXT DEFAULT '',"
    " image_orig_url TEXT DEFAULT '');")
rows = [
    # (id, image_url, image_orig_url)
    (1, "/api/img/prod_1_100_br.jpg", "/api/img/prod_1_100.jpg"),   # o'zimizniki
    (2, "/api/img/type_kran.jpg", ""),                              # umumiy
    (3, "/api/img/type_kran.jpg", ""),                              # o'sha umumiy
    (4, "/api/img/prod_1_100_br.jpg", ""),                          # o'g'irlangan nusxa
]
con.executemany("INSERT INTO products VALUES(?,?,?)", rows)
con.commit()
con.close()
for nom in ("prod_1_100_br.jpg", "prod_1_100.jpg", "type_kran.jpg"):
    open(os.path.join(up, nom), "wb").write(b"x")

out = P._clean_sync(_FakeDB(dbp), up, True)
con = sqlite3.connect(dbp)
qolgan = dict((r[0], r[1]) for r in con.execute("SELECT id, image_url FROM products"))
con.close()

check("3 ta tovardan rasm uzildi", out["products"] == 3, str(out))
check("o'z surati joyida", qolgan[1] == "/api/img/prod_1_100_br.jpg", qolgan[1])
check("umumiy rasm uzildi", qolgan[2] == "" and qolgan[3] == "" and qolgan[4] == "")
check("umumiy fayl o'chdi", not os.path.isfile(os.path.join(up, "type_kran.jpg")))
check("o'z faylimiz saqlanib qoldi",
      os.path.isfile(os.path.join(up, "prod_1_100_br.jpg")) and
      os.path.isfile(os.path.join(up, "prod_1_100.jpg")))
check("o'chirilgan fayl soni", out["files"] == 1, str(out))

print()
if _fail:
    print(f"❌ {len(_fail)} ta tekshiruv yiqildi: " + ", ".join(_fail))
    sys.exit(1)
print("✅ Hammasi joyida")
