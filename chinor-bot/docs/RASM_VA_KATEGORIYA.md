# Rasm brendlash va rasmga qarab kategoriyalash

Katalog rasmlari endi bir xil ko'rinishga keltiriladi va tovar bo'limi
(kategoriya) NOMDAN emas, RASMDAN aniqlanadi.

---

## 1. Rasm oqimi (yuklashdan katalogga)

```
telefon rasmi
   │  Mini App ichida ~1024px ga siqiladi
   ▼
POST /api/product/image
   │  serverda yana siqiladi (max 1280px, JPEG)
   ├──────────────► prod_<id>_<vaqt>.jpg        = ASL  → image_orig_url
   │  bot/imageproc.brand()
   │     • u2netp modeli (onnxruntime) fonni kesadi
   │     • och yashil kvadrat fon (1200×1200)
   │     • tovar tagida yumshoq soya
   │     • o'ng past burchakda Chinor logotipi
   └──────────────► prod_<id>_<vaqt>_br.jpg     = KATALOG → image_url
```

Asl rasm **hech qachon o'chirilmaydi**. Mini App'da mijoz «📷 Asl rasm»
tugmasi bilan uni ko'radi — tovar haqiqatda qanday ekanini tekshirish uchun.

Rasm yuklangach, tovarning bo'limi bo'sh bo'lsa, fon vazifasi AI ga rasmni
yuborib bo'limni aniqlaydi (javob rasm yuklash tezligiga ta'sir qilmaydi).

### onnxruntime — ixtiyoriy, lekin tavsiya etiladi

```bash
pip install onnxruntime
```

* Model `u2netp` (~4.6 MB) birinchi ishlatishda `DATA_DIR/.u2net/` ga
  yuklab olinadi (bot `nologin` foydalanuvchi ostida ishlagani uchun
  `~/.u2net` emas).
* Bitta rasm ≈ **0.6 s** (Mac), ≈ **1.2 s** (1 vCPU VPS); peak xotira ≈ 350 MB.
* Sifat yetarli bo'lmasa `.env` da: `CUTOUT_MODEL=u2net` (176 MB, aniqroq).
* **`rembg` paketi ATAYIN ishlatilmagan**: u aynan shu modelni chaqiradi,
  lekin numba + scikit-image + pymatting tortadi va 1 GB li serverda botni
  OOM qildi (2026-08-05, VPS'da sinaldi). Endi model to'g'ridan-to'g'ri
  chaqiriladi — natija bir xil, xotira ~4 barobar kam.
* Xotira "arena"si o'chirilgan (`enable_cpu_mem_arena=False`) — 549 MB
  o'rniga 351 MB, tezlik o'zgarmadi.
* **O'rnatilmasa hech narsa buzilmaydi** — rasm shunchaki kesilmasdan
  och yashil fonga solinadi (katalog baribir bir xil ko'rinadi).

Ko'rinishni `bot/imageproc.py` boshidagi sozlamalar boshqaradi: `CANVAS`,
`BG_TOP`/`BG_BOTTOM` (fon rangi), `OBJECT_RATIO`, `LOGO_RATIO`.
Logotip: `chinor-bot/assets/logo.png` (`.env`: `BRAND_LOGO=...`).

---

## 2. Bo'limni aniqlash (uch bosqich)

| Bosqich | Fayl | Nimaga qaraydi | Qachon |
|---|---|---|---|
| 1 | `marketplace/categorize.py` | nomdagi kalit so'z | tekin, bir marta, ~66% |
| 2 | `marketplace/categorize_ai.py` | nom (matnli AI) | qolganiga |
| 3 | `marketplace/categorize_vision.py` | **RASM** (vision AI) | rasmi borlarga |

Nom do'konning ichki tili ("Krakadil knobkali", "SHayton 60sm") — rasm esa
tovarni to'g'ridan-to'g'ri ko'rsatadi, shuning uchun rasm aniqroq.

AI **faqat mavjud ro'yxatdan** tanlaydi (baza bo'limlari + `ai_category.py`
dagi standart 16 talik), ishonchsiz bo'lsa `Boshqalar` deydi va biz bo'limni
umuman qo'ymaymiz — noto'g'ri bo'limdan ko'ra bo'limsiz qolgani yaxshiroq.

**Maxfiylik:** AI ga faqat rasm va tovar nomi boradi. Narx, tannarx, qoldiq,
mijoz ma'lumotlari hech qachon yuborilmaydi.

---

## 3. Buyruqlar (bir martalik ishlar)

Mavjud rasmlarni brendlash:

```bash
python -m marketplace.brand_images                 # sinov (5 ta, bazaga yozmaydi)
python -m marketplace.brand_images --all --apply   # hammasi
```

Rasmi borlarni rasmga qarab bo'limlash:

```bash
python -m marketplace.categorize_vision                    # sinov (5 ta)
python -m marketplace.categorize_vision --all --apply      # bo'limsizlar
python -m marketplace.categorize_vision --all --apply --recheck   # hammasini qayta
```

**Bepul AI kvotasi bir yugurishga yetmaydi** (~30 rasmdan keyin 429 → 1 soat
sovish). Shuning uchun skript: har so'rov orasida 4 s kutadi (`--sleep`),
ketma-ket 8 ta bo'sh javobdan keyin o'zi to'xtaydi (`--stop-after`) va
ko'rilganlarni `DATA_DIR/vision_cat_done.json` ga yozadi — bir soatdan keyin
yana ishga tushirsangiz QOLGANIDAN davom etadi (`--again` = boshidan).

Ikkalasi ham `--apply` dan oldin **pos.db zaxira nusxasini** oladi
(`pos.db.bak-img-*`, `pos.db.bak-cat-*`).

---

## 4. Mini App'dagi o'zgarishlar

* Kartochka va modal rasmi `object-fit: contain` — **rasm endi qirqilmaydi**
  (ilgari `cover` edi va tovarning yarmi ko'rinmasdi).
* Modalda «🖼 Katalog rasmi / 📷 Asl rasm» o'tkazgichi, rasmni bosib to'liq
  ekranda ochish.
* Bo'lim nomi kartochkada ko'rinadi.
* Admin: mahsulot kartasida **🏷 Bo'limni aniqla (AI)** va
  **🖼 Rasmni tozalash** tugmalari (`POST /api/product/categorize`,
  `POST /api/product/rebrand`). Qayta brendlash har safar ASL rasmdan
  boshlanadi — sifat pasaymaydi.

---

## 5. Baza

`products.image_orig_url` (TEXT) — asl rasm manzili. Migratsiya
`database/_base.py` da avtomatik (`ALTER TABLE ... ADD COLUMN`), qo'lda hech
narsa qilish shart emas. Eski tovarlarda bo'sh bo'ladi — u holda `image_url`
ning o'zi asl rasm hisoblanadi.
