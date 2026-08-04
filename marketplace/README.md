# Marketplace integratsiyasi (YesPOS gateway)

Onlayn do'kon poydevori: YesPOS marketplace gateway'dan **katalog** va **qoldiq**ni
tortib, o'z bazamizga yozib boradi. Ustiga analitika quriladi.

Nima uchun kerak: gateway faqat **hozirgi** qoldiqni beradi, tarixni bermaydi.
Muzlagan yoki tugab-keltirilmayotgan tovarni topish uchun tarix kerak — shuning
uchun har kuni qoldiq surati shu yerga yoziladi. **Qancha erta boshlansa,
shuncha erta natija.**

## O'rnatish

```bash
pip install -r marketplace/requirements.txt
```

`.env` faylni tayyorlash (repo ildizida yoki `marketplace/` ichida):

```bash
cp marketplace/.env.example marketplace/.env
```

`YESPOS_API_KEY` ni to'ldiring. **Kalit hech qachon kodga yozilmaydi** —
`.env` git'ga tushmaydi (`.gitignore` da).

## Ishlatish

```bash
python -m marketplace.sync status     # baza holati
python -m marketplace.sync catalog    # kategoriya + tovar ro'yxati
python -m marketplace.sync stock      # bugungi qoldiq surati
python -m marketplace.sync all        # ikkalasi
```

Analitika (tarix yig'ilgach):

```bash
python -m marketplace.analytics dead --days 30    # muzlagan tovarlar
python -m marketplace.analytics gap  --days 7     # tugagan-u keltirilmayotgan
```

## Chinor POS'ga ulash (ko'prik)

YesPOS qoldiq/narxini `pos.db` ga ko'chiradi — kassa, admin va bot o'sha bazadan
ishlaydi.

```bash
python -m marketplace.pos_bridge            # faqat ko'rsatadi (dry-run)
python -m marketplace.pos_bridge --apply    # yozadi (avval zaxira nusxa oladi)
```

**Ulash kaliti: Chinor `products.id` == YesPOS `sku`.** Tasodif emas — Chinor
bazasi o'sha do'kon artikullari asosida qurilgan. 6664 tovardan 6585 tasi
mos keladi (98.8%), 93.6% da nom ham bir xil.

`qty` va `sell_price` yoziladi. `cost_price` (tannarx) va `wholesale_price`
(optom) **tegilmaydi** — API ularni bermaydi. Narxi 0 bo'lgan tovarlar
o'tkazib yuboriladi (aks holda do'konda "0 so'm" bo'lib chiqardi).

## USD narx olib tashlandi

Narx endi **so'mda kanonik**. Ilgari USD kanonik edi va uchta joyda sinxronni
buzardi — uchalasi ham tuzatildi:

| Joy | Nima qilardi | Hozir |
|---|---|---|
| `_products.py` `reprice_all_with_rate()` | kurs o'zgarganda `sell_price = sell_price_usd × kurs` | mahsulotga tegmaydi, faqat mijoz qarzi |
| `_base.py` migratsiya | bot har ishga tushganda bo'sh USD narxlarni so'mdan to'ldirardi | mahsulot uchun o'chirildi |
| `web_api.py` savdo narxi | avval `sell_price_usd` ni olardi → ekranda yangi narx, chekda eski narx | avval so'm, USD faqat zaxira |

`products` jadvalidagi `sell_price_usd` / `cost_price_usd` /
`wholesale_price_usd` ustunlari nolga tushirilgan. Mijoz qarzi (`debt_usd`)
tegilmagan — u boshqa masala.

## Kunlik cron (DigitalOcean serverda)

```
30 23 * * *  cd /opt/chinor && python -m marketplace.sync all >> data/sync.log 2>&1
35 23 * * *  cd /opt/chinor && python -m marketplace.pos_bridge --apply >> data/sync.log 2>&1
```

Kuniga bir marta yetadi: butun katalog **bitta** so'rovda keladi, qoldiq esa
500 talik sahifalarda. 7000 tovar ≈ 14 sahifa — gateway limitiga (10/daqiqa)
klient o'zi moslashib kutadi.

## Tuzilma

| Fayl | Vazifasi |
|---|---|
| `config.py` | `.env` dan sozlamalar, baza va rasm yo'llari |
| `client.py` | Gateway klienti — limit hisobi, qayta urinish, xato tarjimasi |
| `db.py` | SQLite sxema: `categories`, `products`, `stock_daily`, `sync_runs` |
| `sync.py` | Katalog/qoldiqni tortib bazaga yozish (CLI) |
| `analytics.py` | Muzlagan / tugagan-gap hisoblari (CLI) |
| `pos_bridge.py` | YesPOS qoldiq/narxini `pos.db` ga ko'chirish (CLI) |

## Bilib qo'yish kerak bo'lgan narsalar

**`page` va `limit` — query param, body emas.** Body ga solinsa gateway
500 qaytaradi. `client.py` buni to'g'ri yuboradi.

**Rasmlar id bo'yicha o'zimizda.** Gateway `image` maydonini bo'sh berishi
mumkin, bergani ham `temp-images/...` (vaqtinchalik) yo'l. Shuning uchun rasm
`data/marketplace-images/<product_id>.jpg` ko'rinishida saqlanadi — `sku` ga
emas, `id` ga bog'lanadi (sku do'konda o'zgarishi mumkin, id o'zgarmaydi).
`sync status` "rasmi yo'q tovarlar" sonini ko'rsatadi.

**Savdo tarixi yo'q.** Gateway na chek, na savdo qaytaradi — hatto API orqali
o'zing yaratgan buyurtmani ham qayta o'qib bo'lmaydi. Shuning uchun savdo
yuborilganda **avval o'z bazaga yozilishi** kerak, keyin gateway'ga. Excel
hisobot o'z bazadan chiqariladi.

**Analitika taxminga tayanadi.** Savdo qoldiqning kamayishidan hisoblanadi.
Inventarizatsiya yoki buzilgan tovarni chiqarish ham qoldiqni kamaytiradi —
bu aniq savdo emas, ishonchli ko'rsatkich.

## Joriy holat (2026-08-04)

Katalog jonli: **6822 tovar**, 1 kategoriya (`Корневая группа` — hammasi
shu bitta guruh ichida, tuzilma yo'q). Ombor qiymati ~1.25 mlrd so'm.

Birinchi sinxron bajarildi, `pos.db` ga 4499 qator yozildi.

Diqqat qilinadigan narsalar:
- **126 tovarda manfiy qoldiq** (sement −153, qum −89, shagal −94) — ombor
  hisobidagi teshik, YesPOS tomonida tuzatilishi kerak.
- **50 tovarning narxi 0**, 1325 tasining qoldig'i 0.
- **Rasm faqat 3 tovarda** — shuning uchun rasmlar `id` bo'yicha o'zimizda.
- **Shtrix-kod API'da yo'q** (`classcode` — 17 xonali IKPU soliq kodi, 6723
  tovarga bir xil; `sku` — ketma-ket artikul). Chinor bazasida esa 2070
  tovarning shtrix-kodi bor.
- Bir necha narx YesPOS'da xato ko'rinadi (`Mufta 25mm plastmas` 600,000 so'm)
  — ko'zgu bo'lgani uchun xato ham ko'chadi.
