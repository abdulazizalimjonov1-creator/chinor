# Kameralar — kompyuterning o'zidan (internetsiz): qilingan ish va davomi

Bu fayl keyingi Claude sessiyasi uchun (Mac'dan, Tailscale orqali boshqa
kompyuterlarni boshqaradi). Avval shuni o'qing.

## Muammo
Do'kondagi kompyuter kameralar bilan bitta switch'ga ethernet orqali ulangan.
Lekin Chinor admin kamera tasvirini internetdagi server orqali olardi
(`chinor-bot/bot/api_cams.py`) — internet uzilsa kameralar ham ko'rinmay qolardi.

## Qilingan ish (branch: `claude/vibrant-dijkstra-1rgrc2`, admin v1.6.0)
- `chinor-admin/lib/cams-local.js` — yangi lokal kamera dvigateli (main process):
  kamerani RTSP orqali ffmpeg bilan o'zi ochadi, 127.0.0.1 da MJPEG beradi
  (token bilan). Internet kerak emas.
  - Qotmaydi: har kamera alohida ffmpeg; 10 s kadr kelmasa qorovul
    o'ldirib qayta ulaydi; kamera qaytsa tasvir o'zi tiklanadi.
  - Ro'yxat: `userData/chinor-cams.json`, parollar `safeStorage` bilan shifrlangan.
  - Brendni avtomatik aniqlash (Hikvision, Dahua, Reolink, XM…), NVR kanali,
    to'liq RTSP manzil, tarmoqdan izlash (554-port), ovoz (AAC).
  - ffmpeg qidiriladi: sozlangan yo'l → `resources/ffmpeg/` → userData →
    `vendor/ffmpeg/<plat>` → PATH. Topilmasa — ilovadan bir marta yuklab olish.
- `main.js` / `preload.js` — `cams:*` IPC; `lib/api.js` — `camsExport`.
- `renderer/app.js` — Kameralar bo'limi qayta yozildi: qo'shish/tahrirlash/
  o'chirish, izlash, «⇩ Serverdan» (Mini App'dagilarni ko'chirish), ustunlar soni,
  to'liq ekran, zoom, 📷 kadrni saqlash.
- Internet yo'q bo'lsa ham ilova ochiladi (avval login qilingan bo'lsa).
- Login oynasida «📹 Kameralar (internetsiz)» — **kassir uchun FAQAT KO'RISH**:
  faqat Kameralar bo'limi, qo'shish/tahrirlash yo'q.
- Server: `GET /api/admin/cams/export` (parol bilan, `cams` ruxsati).
- `release.sh` ffmpeg (win32-x64, darwin-arm64, darwin-x64) ni
  `chinor-admin/vendor/ffmpeg/` ga yuklab, o'rnatuvchiga qo'shadi (gitignore'da).
- Tuzatildi: `.np-head` CSS barcha modal sarlavhalarini buzgan edi.

Sinov: soxta kamera oqimi bilan (uzilish/qotish/tiklanish) va Chromium'da UI.
Haqiqiy RTSP kamerada va haqiqiy Electron oynasida HALI sinalmagan.

## Qilish kerak (Mac'dan)
1. Branch'ni asosiyga birlashtirish (foydalanuvchi ruxsati bilan).
2. Botni yangi kod bilan qayta ishga tushirish (ofisdagi Windows server,
   Tailscale: `chino@100.124.35.18`, papka `C:/chinor/chinor-bot`) —
   export endpoint uchun.
3. `cd chinor-admin && ./release.sh` — Windows + mac yig'iladi, feed'ga yuklanadi.
4. **Kassa Windows kompyuteri** (kameralar bilan bitta switch'da, ethernet
   ulangan) — Tailscale orqali Chinor admin 1.6.0 ni o'rnatish
   (`Chinor-Setup-1.6.0.exe`). Kassa kompyuterining Tailscale IP'sini
   foydalanuvchidan so'rang yoki `tailscale status` dan toping.
   Eslatma: HP Pavilion'ga ethernet ulanmagan — kameralar unda ko'rinmaydi.
5. Kassa kompyuterida: admin login → Kameralar → «🔍 Tarmoqdan izlash» →
   kameralarni qo'shish (yoki «⇩ Serverdan»). Har birida tasvir chiqishini tekshirish.
6. Kassirga: login oynasidagi «📹 Kameralar (internetsiz)» tugmasi.
