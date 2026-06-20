# Chinor POS

Telegram bot + Mini App + Desktop (Electron) kassa — hammasi **bitta SQLite baza**
(`pos.db`) va **bitta API server** (`main.py`, port `8765`) ustida ishlaydi.

```
                    ┌─────────────────────────────┐
                    │   main.py  (bitta jarayon)   │
                    │  aiogram bot + aiohttp API   │
                    │        port 8765             │
                    └──────────────┬──────────────┘
                                   │  pos.db (SQLite)
        ┌──────────────────────────┼──────────────────────────┐
        │                          │                           │
   Telegram bot              Mini App (web)            Desktop Kassa
   (handlers/)              (fronted/ → Pages)        (desktop/, Electron)
                            /api/*                     /api/sync/* + /updates/
```

Internetga chiqish **Cloudflare Tunnel** orqali (ilgari ngrok edi) —
[docs/CLOUDFLARE_TUNNEL.md](docs/CLOUDFLARE_TUNNEL.md).

---

## Papka tuzilishi

| Papka / fayl | Vazifasi |
|---|---|
| `main.py` | Kirish nuqtasi — botni va API serverni ishga tushiradi |
| `bot/` | Xizmatlar: `web_api.py` (HTTP API), `keyboards.py`, `auth.py` (login), `permissions.py` (rollar), `exporter.py` (Excel), `gemini_analyzer.py` (AI), `config.py`, `notifier.py`, `barcode.py`, `backup.py`, `states.py` |
| `handlers/` | Telegram UI oqimlari: `sale.py`, `glavniy.py` (/start), `admin_products.py`, `admin_clients.py`, `admin_stats.py`, `catalog.py`, `client.py`, `auth_setup.py`, `ai_analytics.py` |
| `database/` | Ma'lumotlar qatlami: `_base.py` + mavzular bo'yicha modullar (`_products`, `_sales`, `_clients`, `_orders`, `_catalog`, `_admins`, `_analytics`, `_stats`, `_formatters`, `_helpers`), `channel_db.py` (db singleton) |
| `fronted/` | Mini App web (HTML/CSS/JS) — Cloudflare Pages'ga deploy qilinadi |
| `desktop/` | Electron kassa (Win/Mac): `main.js`, `preload.js`, `lib/`, `renderer/` |
| `updates/` | Desktop avto-yangilanish feed (`latest.yml` + o'rnatuvchilar) |
| `uploads/` | Mahsulot rasmlari (DB ularga ishora qiladi) |
| `exports/` | Excel hisobotlar (avtomatik yaratiladi) |
| `docs/` | Qo'shimcha hujjatlar (tunnel, eski qaydlar) |
| `pos.db` | **Jonli SQLite baza — tegmang, zaxiralang** |

---

## Ishga tushirish (server / Mac)

```bash
pip install -r requirements.txt          # bir marta
cp .env.example .env                      # so'ng .env ni to'ldiring
python main.py                            # bot + API (port 8765)
```

Kerakli `.env` qiymatlari: `BOT_TOKEN`, `GLAVNIY_ADMIN_ID`, `CHANNEL_ID`,
`MINI_APP_URL`, `CORS_ALLOW_ORIGIN`, `API_PORT` (8765). To'liq ro'yxat —
`.env.example`.

Internetga ochish (doimiy URL): [docs/CLOUDFLARE_TUNNEL.md](docs/CLOUDFLARE_TUNNEL.md).

---

## Desktop kassani yig'ish va tarqatish

```bash
cd desktop
npm install                  # bir marta
npm run dist                 # Windows o'rnatuvchi (dist/ ga)
# so'ng dist/*.exe + latest.yml ni  ../updates/  ga ko'chiring
./release.sh                 # (mavjud bo'lsa) shu jarayonni avtomatlashtiradi
```

Yangilanish manzili `desktop/package.json` → `build.publish[0].url` da
build vaqtida muhrlanadi.

---

## Server manzilini almashtirish (bir buyruq)

Tunnel URL o'zgarsa, uni 3 ta faylda (`​.env`, `desktop/package.json`,
`fronted/app.js`) qo'lda emas, skript bilan yangilang:

```bash
./set-server-host.sh kassa.sizning-domen.uz
```

So'ng: bot'ni qayta ishga tushiring · `fronted/` ni Pages'ga deploy qiling ·
desktop'ni qayta yig'ing.

---

## "Qayerni o'zgartiraman?" — tezkor yo'riqnoma

| Nimani o'zgartirmoqchisiz | Qayerga qarang |
|---|---|
| Bot tugmalari / menyu | `bot/keyboards.py` |
| Bot buyruq mantiqi (sotuv, mahsulot, mijoz...) | `handlers/` ichidagi mos fayl |
| HTTP API endpoint (Mini App / desktop) | `bot/web_api.py` |
| Baza so'rovlari / jadvallar | `database/_*.py` |
| Rol va ruxsatlar | `bot/permissions.py` |
| Mini App ko'rinishi | `fronted/` |
| Desktop kassa oynasi | `desktop/renderer/` |
| Desktop sync / yangilanish | `desktop/main.js`, `desktop/lib/` |
| Sozlamalar (token, port, URL) | `.env` (`bot/config.py` o'qiydi) |

---

## Zaxira (backup)

`pos.db` — yagona haqiqat manbai. Muntazam nusxa oling:

```bash
cp pos.db "backups/pos-$(date +%Y%m%d).db"
```
