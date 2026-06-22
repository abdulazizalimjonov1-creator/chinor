# Chinor POS

Telegram bot + Mini App + Desktop (Electron) kassa va admin — hammasi
**bitta SQLite baza** (`pos.db`) va **bitta API server** (`chinor-bot/main.py`,
port `8765`) ustida ishlaydi.

```
                    ┌─────────────────────────────┐
                    │  chinor-bot/main.py          │
                    │  aiogram bot + aiohttp API   │
                    │        port 8765             │
                    └──────────────┬──────────────┘
                                   │  pos.db (SQLite)
      ┌────────────────┬───────────┴───────┬────────────────────┐
      │                │                   │                    │
 Telegram bot     Mini App (web)      Chinor Kassa        Chinor Admin
 (chinor-bot/    (app/ → Pages)      (chinor-kassa/,     (chinor-admin/,
  handlers/)      /api/*              Electron)           Electron)
                                      /api/sync/*         /api/*
                                      + /updates/
```

Internetga chiqish **Cloudflare Tunnel** orqali (ilgari ngrok edi) —
[chinor-bot/docs/CLOUDFLARE_TUNNEL.md](chinor-bot/docs/CLOUDFLARE_TUNNEL.md).

---

## Papka tuzilishi (4 ta asosiy bo'lim)

Loyiha ildizida 4 ta mustaqil bo'lim turadi — har birida o'z kodi:

| Papka | Vazifasi |
|---|---|
| **`chinor-bot/`** | Telegram bot + HTTP API server (Python). Mini App'ni ham shu serve qiladi. |
| **`app/`** | Mini App web (HTML/CSS/JS) — Cloudflare Pages'ga deploy qilinadi |
| **`chinor-kassa/`** | Desktop kassa (Electron, Win/Mac) — offline sotuv terminali |
| **`chinor-admin/`** | Desktop admin panel (Electron) — ombor, hisobot, boshqaruv |

### `chinor-bot/` ichi

| Papka / fayl | Vazifasi |
|---|---|
| `main.py` | Kirish nuqtasi — botni va API serverni ishga tushiradi |
| `bot/` | Xizmatlar: `web_api.py` (HTTP API), `keyboards.py`, `auth.py`, `permissions.py` (rollar), `exporter.py` (Excel), `gemini_analyzer.py` (AI), `config.py`, `notifier.py`, `barcode.py`, `backup.py`, `states.py` |
| `handlers/` | Telegram UI oqimlari: `sale.py`, `glavniy.py` (/start), `admin_products.py`, `admin_clients.py`, `admin_stats.py`, `catalog.py`, `client.py`, `auth_setup.py`, `ai_analytics.py` |
| `database/` | Ma'lumotlar qatlami: `_base.py` + mavzu modullar (`_products`, `_sales`, `_clients`, `_orders`, `_catalog`, `_admins`, `_analytics`, `_stats`, `_formatters`, `_helpers`), `channel_db.py` |
| `updates/` | Desktop avto-yangilanish feed (`latest.yml` + o'rnatuvchilar) |
| `uploads/` | Mahsulot rasmlari (DB ularga ishora qiladi) |
| `exports/` | Excel hisobotlar (avtomatik yaratiladi) |
| `docs/` | Qo'shimcha hujjatlar (tunnel, eski qaydlar) |
| `pos.db` | **Jonli SQLite baza — tegmang, zaxiralang** |
| `.env` | Sirlar (token, port, URL) — git'ga chiqmaydi |

---

## Ishga tushirish (server / Mac)

```bash
cd chinor-bot
pip install -r requirements.txt          # bir marta
cp .env.example .env                      # so'ng .env ni to'ldiring
python main.py                            # bot + API (port 8765)
```

> Eslatma: bot `chinor-bot/` ichidan ishga tushiriladi — `pos.db`, `uploads/`,
> `.env` shu papkada bo'lishi kerak. Mini App'ni esa server `../app/` dan oladi.

Kerakli `.env` qiymatlari: `BOT_TOKEN`, `GLAVNIY_ADMIN_ID`, `CHANNEL_ID`,
`MINI_APP_URL`, `CORS_ALLOW_ORIGIN`, `API_PORT` (8765). To'liq ro'yxat —
`chinor-bot/.env.example`.

Internetga ochish (doimiy URL): [chinor-bot/docs/CLOUDFLARE_TUNNEL.md](chinor-bot/docs/CLOUDFLARE_TUNNEL.md).

---

## Desktop ilovalarni yig'ish va tarqatish

```bash
cd chinor-kassa          # yoki chinor-admin
npm install              # bir marta
npm run dist             # Windows o'rnatuvchi (dist/ ga)
# so'ng dist/*.exe + latest.yml ni  ../chinor-bot/updates/  ga ko'chiring
./release.sh             # (mavjud bo'lsa) shu jarayonni avtomatlashtiradi
```

Yangilanish manzili `chinor-kassa/package.json` → `build.publish[0].url` da
build vaqtida muhrlanadi.

---

## Server manzilini almashtirish (bir buyruq)

Tunnel URL o'zgarsa, uni 3 ta faylda (`chinor-bot/.env`,
`chinor-kassa/package.json`, `app/app.js`) qo'lda emas, skript bilan yangilang:

```bash
./set-server-host.sh kassa.sizning-domen.uz
```

So'ng: bot'ni qayta ishga tushiring · `app/` ni Pages'ga deploy qiling ·
desktop'ni qayta yig'ing.

---

## "Qayerni o'zgartiraman?" — tezkor yo'riqnoma

| Nimani o'zgartirmoqchisiz | Qayerga qarang |
|---|---|
| Bot tugmalari / menyu | `chinor-bot/bot/keyboards.py` |
| Bot buyruq mantiqi (sotuv, mahsulot, mijoz...) | `chinor-bot/handlers/` ichidagi mos fayl |
| HTTP API endpoint (Mini App / desktop) | `chinor-bot/bot/web_api.py` |
| Baza so'rovlari / jadvallar | `chinor-bot/database/_*.py` |
| Rol va ruxsatlar | `chinor-bot/bot/permissions.py` |
| Mini App ko'rinishi | `app/` |
| Desktop kassa oynasi | `chinor-kassa/renderer/` |
| Desktop kassa sync / yangilanish | `chinor-kassa/main.js`, `chinor-kassa/lib/` |
| Admin paneli | `chinor-admin/renderer/` |
| Sozlamalar (token, port, URL) | `chinor-bot/.env` (`bot/config.py` o'qiydi) |

---

## Zaxira (backup)

`chinor-bot/pos.db` — yagona haqiqat manbai. Muntazam nusxa oling:

```bash
cp chinor-bot/pos.db "backups/pos-$(date +%Y%m%d).db"
```
