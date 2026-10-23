# @cheklars_bot ni loyihaga ko'chirish

Kassa kompyuteridagi (DESKTOP-26P4PL4) **ChekHisobot** tizimi qiladigan ishni
Chinor serveriga va Mini App'ga ko'chirish. Mini App tayyor bo'lgach bot
tokeni almashtiriladi va foydalanuvchilarga xabar beriladi.

## Nima ko'chdi, nima ko'chmadi

| Kassadagi tizim | Bizda |
|---|---|
| `userbot.py` → cheklar | `marketplace/tg_receipts.py` (allaqachon bor edi, 54k chek) |
| «Задолженность» o'qish | `receipts.client_debt` — YANGI |
| «ОПЛАТА» (qarz to'lovi) | `debt_payments` jadvali — YANGI |
| Qarz hisobi (`Ombor.cs`) | `marketplace/debt.py` — YANGI |
| Mijoz kabineti (bot tugmalari) | Mini App «Mening hisobim» → Kassadagi hisobim |
| Admin: qarzdorlar, daftar, Excel | Mini App «📕 Qarzdorlar» |
| Cheklarni tanlab birlashtirish | Mini App: tanlash → Excel / mijozga yuborish |
| `ChekProxy` + `ChekMatn` (printerdan o'qish) | **KO'CHIRILMADI** — kerak emas, cheklar Telegramdan to'liq keladi |

Cheklarning o'zi ko'chirilmaydi: ChekHisobot ularni aynan bizdagi manbadan
(@yespos_notify_bot) oladi, ya'ni ikkala bazada bir xil 54 ming chek.
Ko'chadigan narsa — faqat qo'lda kiritilgani: mijozlar, telefonlar,
Telegram bog'lanishi, tashqi qarz, qulflar.

## Qarz qanday hisoblanadi

Qarz noldan QAYTA HISOBLANMAYDI. QPOS o'z rasmiy qoldig'ini ikki joyda yozadi:
chekdagi «Задолженность» va «ОПЛАТА» dagi «Остаток». Shulardan eng keyingisi
**surat** qilib olinadi:

```
qarz = surat + (suratdan keyingi nasiya) − (suratdan keyingi to'lovlar)
             + qo'lda kiritilgan harakatlar + tashqi qarz
```

Shuning uchun bitta chek o'qilmay qolsa ham hisob keyingi chekda o'zini
o'nglaydi. Daftarda QPOS qoldig'i bilan farq chiqsa — «Kassada tuzatish»
qatori bo'lib KO'RINADI, yashirilmaydi.

## Deploy tartibi (Windows server)

> DIQQAT: `receipts_sync.py` har 30 daqiqada userbot sinxronini ishga
> tushiradi. Telethon sessiya fayli qulflanadi, shuning uchun qo'lda
> sinxron paytida bot TO'XTATILGAN bo'lishi kerak.

1. **Kodni yuklash** va botni to'xtatish (Task Scheduler: `Chinor-bot`).

2. **Cheklarni qayta o'qish** — `client_debt` va `debt_payments` ni
   to'ldirish uchun (bir martalik, ~54k xabar):

   ```
   python -m marketplace.tg_receipts sync --all --reparse
   ```

   Natijada «qarz to'lovi» soni chiqadi — u kassadagi **581** ga yaqin
   bo'lishi kerak. Keskin kam bo'lsa — ajratgichni tekshirish lozim.

3. **Mijozlarni ko'chirish.** `hisobot.db` ni kassa kompyuteridan nusxalab
   oling (faqat o'qiladi), keyin:

   ```
   python -m marketplace.migrate_chekhisobot --db C:\yo'l\hisobot.db
   python -m marketplace.migrate_chekhisobot --db C:\yo'l\hisobot.db --apply
   ```

   Kutilayotgani: 77 mijoz, 66 tasi telefonli, 14 tasi Telegramga bog'langan,
   1 tashqi qarz, 3 sinx qulfi, 6 eslatma qulfi.

4. **Tekshirish.** Mini App → «📕 Qarzdorlar». Raqamlar kassadagi
   ChekHisobot bilan solishtiriladi:

   ```
   ChekHisobot.exe sql "SELECT nom, ... FROM mijozlar ORDER BY 2 DESC LIMIT 12"
   ```

   Eng katta 10 qarzdor mos tushsa — ko'chirish to'g'ri ketgan.

5. **Mini App'ni deploy qilish:**

   ```bash
   npx wrangler pages deploy app --project-name=fronted --branch=production
   ```

6. Botni qayta ishga tushirish.

## Token almashtirish (oxirgi bosqich)

Faqat 1–6 bosqich tekshirilgandan KEYIN:

1. Kassa kompyuterida `ChekTizimi` vazifasini o'chiring va
   `C:\Users\Public\ChekHisobot` dagi botni to'xtating — **bitta tokenni ikki
   dastur o'qisa, xabarlar bir-biridan o'g'irlanadi**.
2. `@cheklars_bot` tokenini Chinor botiga o'tkazing (yoki aksincha — qaysi
   nom qolishiga qarab).
3. Ro'yxatdagi mijozlarga bir martalik xabar: endi cheklar va qarz Mini
   App'da, do'konga borish shart emas.

## Mijoz qanday bog'lanadi

- Eski botda ro'yxatdan o'tgan 14 kishi: `chat_id` migratsiyada keladi,
  ular darrov ko'radi.
- Qolganlar: botga `/start` → «📱 Raqamni yuborish». Raqam kassadagi
  mijoz telefoni bilan mos tushsa, hisob avtomatik bog'lanadi va javobda
  «Kassadagi hisobingiz topildi» deb yoziladi
  (`handlers/glavniy.py::_bind_kassa_account`).
- Telefoni yo'q 11 mijozni admin Mini App'dan kiritib qo'yishi mumkin:
  «📕 Qarzdorlar» → mijoz → «⚙️ Sozlamalar».

## Fayllar

| Fayl | Vazifasi |
|---|---|
| `marketplace/receipts.py` | `Задолженность` + `ОПЛАТА` ajratgichi, `debt_payments` jadvali |
| `marketplace/debt.py` | qarz hisobi, daftar, qarzdorlar, telefon bo'yicha bog'lash |
| `marketplace/migrate_chekhisobot.py` | `hisobot.db` dan mijozlarni ko'chirish |
| `chinor-bot/bot/api_debt.py` | `/api/my/*` (mijoz) va `/api/debt/*` (admin) |
| `chinor-bot/bot/debt_export.py` | guruhlangan Excel (chek → «+» → tovarlar) |
| `app/app.js`, `app/index.html` | Mini App ekranlari |
