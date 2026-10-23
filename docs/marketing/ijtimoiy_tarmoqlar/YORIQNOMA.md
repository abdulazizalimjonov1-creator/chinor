# Instagram va YouTube'ni ochish — Chinor

Akkauntni o'zingiz ochasiz. Kod, parol va SMS sizning telefoningizga keladi, men buni qila olmayman.
Quyida nima yozish kerakligi tayyor: ko'chirib, joyiga qo'yasiz.

> **Muhim:** do'kon uchun **alohida email** oching (masalan `chinor.sergeli@gmail.com`).
> Shu bitta Google akkaunt YouTube'ga ham, Facebook/Instagram ro'yxatiga ham ishlatiladi.
> Paroli va 2 bosqichli himoya kodlarini xavfsiz joyda saqlang. Akkaunt shaxsiy telefoningizga
> bog'lanmasin, aks holda telefon almashsa (admin Telegram'i bilan bo'lgani kabi) akkaunt yo'qoladi.

---

## 1. Instagram (15 daqiqa)

1. Instagram ilovasi → **Ro'yxatdan o'tish** → do'kon emaili bilan.
2. **Foydalanuvchi nomi** (bandini tekshiring, birinchi bo'sh turganini oling):
   `chinor.sergeli` · `chinor_uz` · `chinor.dokon` · `chinor_qurilish`
3. **Ism (Name)** — qidiruvda shu chiqadi:
   `Chinor — qurilish mollari | Sergeli`
4. **Profil rasmi:** `profil_rasmi_1080.png`
5. **Bio** (150 belgigacha, ko'chiring):
   ```
   🌳 Qurilish mollari va uy-ro'zg'or · 5000+ tovar
   🧐 Har kuni: qaysi biri sifatli, qaysi biri arzon?
   📍 Sergeli, Chinor bekati · ⏰ 8:00–22:00
   📞 +998 77 877 55 33
   ```
6. **Havola (link):** `https://t.me/Chinors`
7. **Professional akkauntga o'tkazing.** Bu SHART: busiz statistika va avtomatik joylash ishlamaydi.
   Sozlamalar → *Account type and tools* → **Switch to professional account** → **Business**
   - Kategoriya: **Hardware Store** (Qurilish mollari do'koni)
   - Aloqa: telefon `+998 77 877 55 33`, manzil: Sergeli, Chinor bekati yoni
8. **Facebook sahifa ochib ulang.** Bu avtomatik joylash va reklama uchun kerak:
   Instagram → Sozlamalar → *Accounts Center* → Facebook akkaunt qo'shing →
   **Facebook sahifa yarating**: nomi `Chinor`, kategoriya *Hardware Store* → Instagram'ni shu sahifaga ulang.

## 2. YouTube (10 daqiqa)

1. youtube.com → do'kon Google akkaunti bilan kiring → profil belgisi → **Kanal yaratish**.
2. **Kanal nomi:** `Chinor · Usta maslahati`
3. **Handle:** `@chinor.sergeli` (yoki band bo'lmagan yaqini).
4. **Profil rasmi:** `profil_rasmi_1080.png`, **banner:** `youtube_banner_2560x1440.png`
5. **Tavsif:**
   ```
   Chinor — Sergeli tumanidagi qurilish mollari va uy-ro'zg'or do'koni.
   Har kuni qisqa video: ikki tovarni haqiqiy faktlar bilan solishtiramiz —
   qaysi biri sifatli, qaysi biri arzon, kimga qaysi biri mos.
   📍 Toshkent, Sergeli, Chinor bekati yoni · har kuni 8:00–22:00
   📞 +998 77 877 55 33 · Telegram: https://t.me/Chinors
   ```
6. YouTube Studio → *Settings → Channel → Feature eligibility* → **telefon raqamini tasdiqlang**
   (havola qo'yish va muqova tanlash ochiladi).

## 3. Birinchi kunlar

- Har kuni bot tayyorlagan videoni Telegram'da **✅ Kanalga** bosib tasdiqlaysiz.
  Shu videoni Instagram Reels va YouTube Shorts'ga ham qo'lda joylaysiz (telefonga saqlab →
  ilovada **+ → Reel / Short**). Izoh matnini Telegram'dagi xabardan ko'chirasiz.
- Joylash vaqti: kechqurun **19:00–21:00** (odamlar ishdan qaytgan payt).
- 1–2 hafta organik. Keyin eng ko'p **saqlangan** (save) videoga Sergeli atrofi bo'yicha kichik
  **Boost** qilamiz.

## 4. Keyingi bosqich: avtomatik joylash (akkauntlar ochilgach)

| Platforma | Nima kerak | Kim qiladi |
|---|---|---|
| Instagram | Business akkaunt + Facebook sahifa (yuqorida) → developers.facebook.com'da ilova → `instagram_business_content_publish` ruxsati → token | Siz kirasiz va ruxsat berasiz, sozlashni men qilaman |
| YouTube | Google Cloud loyiha → YouTube Data API → OAuth ruxsati | Siz kirasiz va ruxsat berasiz, sozlashni men qilaman |

YouTube'ga API orqali yuklangan video Google tekshiruvidan (audit) o'tmaguncha **faqat "private"**
bo'lib turadi. Shuning uchun YouTube'ga boshida qo'lda joylaymiz, auditga esa parallel ariza beramiz.
Bot videolarni `https://kassa.chinorpos.com` orqali beradi. Instagram aynan shunday ochiq manzilni talab qiladi.
