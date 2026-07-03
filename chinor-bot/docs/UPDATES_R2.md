# Desktop avto-yangilanish — Cloudflare R2 feed

Kassa va admin ilovalari **Cloudflare R2**'dagi feed'dan avtomatik yangilanadi.
Bu serverdan (VPS) **mustaqil** — VPS o'chsa ham yangilanish ishlayveradi, va
hamma narsa **Mac'dan** boshqariladi.

> **Nega R2, Pages emas?** Cloudflare Pages bitta faylni 25 MB bilan cheklaydi;
> kassa o'rnatuvchisi ~80 MB. R2'da bunday limit yo'q.

```
   Mac (build)                Cloudflare R2: chinor-updates        Qurilmalar
  ┌───────────┐   release.sh  ┌──────────────────────────┐  avto  ┌──────────┐
  │ chinor-   │ ───────────▶  │ kassa/  latest.yml + .exe │ ─────▶ │ Windows  │
  │  kassa    │               │         latest-mac.yml+zip│        │  kassa   │
  ├───────────┤               ├──────────────────────────┤        ├──────────┤
  │ chinor-   │ ───────────▶  │ admin/  latest.yml + .exe │ ─────▶ │ admin    │
  │  admin    │               │         latest-mac.yml+zip│        │ (Mac/Win)│
  └───────────┘               └──────────────────────────┘        └──────────┘
        public domen:  https://updates.<domen>/{kassa,admin}/
```

Kassa va admin **alohida prefiks**da (`kassa/`, `admin/`) — chunki ikkalasi ham
`latest.yml` chiqaradi, aralashib ketmasligi uchun. Bu prefiks publish URL'da
muhrlangan (`chinor-kassa/package.json`, `chinor-admin/package.json`).

---

## 1) Bir martalik tayyorgarlik (Mac)

```bash
# Cloudflare'ga kirish (Pages bilan bir xil hisob)
npx wrangler login

# Yangilanish bucket'i
npx wrangler r2 bucket create chinor-updates
```

So'ng **public domen** ulang (Cloudflare dashboard):
`R2 → chinor-updates → Settings → Public access → Connect custom domain` →
`updates.<domen>` (domeningiz Cloudflare'da bo'lishi kerak — tunnel uchun ham
o'sha domen). Bu `https://updates.<domen>/...` ni R2'ga bog'laydi.

Endi shu hostni ilovalarga muhrlang va paketlarni o'rnating:

```bash
./set-updates-url.sh updates.<domen>     # package.json'lardagi URL'ni yangilaydi
cd chinor-kassa && npm install && cd ..  # electron-updater + electron
cd chinor-admin && npm install && cd ..
```

---

## 2) Yangilanish chiqarish (har safar)

```bash
# Kassa
cd chinor-kassa
# package.json -> "version" ni oshiring (masalan 1.6.3 -> 1.6.4)
./release.sh          # build (Win+mac) + R2'ga (kassa/) yuklash

# Admin
cd ../chinor-admin
# package.json -> "version" ni oshiring
./release.sh          # build (Win+mac) + R2'ga (admin/) yuklash
```

`release.sh` ichida: `electron-builder` (Windows `.exe`+`latest.yml`, macOS
`.zip`+`latest-mac.yml`) → `../deploy-updates.sh <app> dist` (R2'ga yuklaydi).
Qurilmalar 30 daqiqada bir tekshiradi (yoki qayta ochilganda) — yangisini
o'zi yuklab o'rnatadi.

> **Windows**: electron-updater jim yuklaydi, ilova yopilganda o'rnatadi.
> **macOS (imzosiz)**: o'zimizning updater yuklab oladi va "Hozir yangilash"
> tugmasi bilan almashtiradi.

---

## 3) Allaqachon tarqatilgan eski kassalar (bir martalik)

electron-updater feed URL `.exe` ichiga **build vaqtida** muhrlanadi. Hozir
qurilmalardagi kassalar hali eski (o'lik ngrok) feed'ni tekshiradi. Ularni R2'ga
o'tkazish uchun **har bir qurilmaga yangi installer'ni bir marta qo'lda**
o'rnating. Shundan keyin avto-yangilanish R2'dan o'zi ishlaydi.

---

## Tez ma'lumotnoma

| Vazifa | Buyruq (repo ildizidan) |
|---|---|
| R2 hostni o'zgartirish | `./set-updates-url.sh updates.<domen>` |
| Kassa versiyasini chiqarish | `cd chinor-kassa && ./release.sh` |
| Admin versiyasini chiqarish | `cd chinor-admin && ./release.sh` |
| Faqat R2'ga yuklash (build'siz) | `./deploy-updates.sh kassa` / `admin` |
| Bucket nomi (default) | `chinor-updates` (`BUCKET=...` bilan o'zgartiriladi) |
