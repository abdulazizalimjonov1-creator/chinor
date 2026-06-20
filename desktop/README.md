# Chinor Kassa — Offline Windows POS

Internetsiz ishlaydigan kassa (Electron). Sotuvlar lokal saqlanadi, internet
kelganda serverga sinxronlanadi.

## Qanday ishlaydi
- **Login** — admin login/parol bilan (`/api/desktop/login`). Bir marta kirgach,
  token 24 soat saqlanadi.
- **Katalog** — server'dan yuklab olinadi (`/api/sync/catalog`) va lokal keshda saqlanadi.
  Internetsiz ham mahsulotlar ko'rinadi.
- **Sotuv** — har bir sotuv darhol lokal faylga yoziladi (offline ham ishlaydi).
- **Sinxronizatsiya** — internet kelganda yuborilmagan sotuvlar avtomatik
  serverga jo'natiladi (`/api/sync/sales`), katalog yangilanadi. Har 12 soniyada
  tekshiriladi; "⟳ Sinxron" tugmasi bilan qo'lda ham mumkin.

Lokal ma'lumotlar: `%APPDATA%/chinor-kassa/chinor-kassa-data.json` (Windows).

## Ishga tushirish (dasturchi)
```bash
cd desktop
npm install
npm start
```

## Windows .exe yig'ish
```bash
npm run dist          # x64: NSIS installer + portable
# yoki alohida:
npx electron-builder --win --x64
```
Natija `desktop/dist/` ichida:
- `Chinor Kassa Setup 1.0.0.exe` — o'rnatuvchi (installer)
- `ChinorKassa-portable-1.0.0.exe` — portable (o'rnatishsiz ishlaydi)

> Eslatma: Mac'dan ham x64 Windows build chiqadi (electron-builder o'z Wine'ini ishlatadi).
> ARM Windows uchun: `npx electron-builder --win --arm64`.

## Server manzili
Standart: `https://unnatural-vibes-praying.ngrok-free.dev`.
Login oynasidagi "Server manzili" bo'limidan o'zgartirsa bo'ladi.
