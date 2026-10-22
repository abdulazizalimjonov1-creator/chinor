# YesPOS kompyuteridagi Claude uchun prompt

Quyidagi `---` chiziqlar orasidagi matnni **YesPOS ilovasi o'rnatilgan** ikkinchi Windows kompyuterda ochilgan Claude Code'ga to'liq nusxalab bering.

---

Sen "Chinor" do'konidagi kassa kompyuterida ishlayapsan. Bu kompyuterga **YesPOS** kassa ilovasi o'rnatilgan. Do'konning asosiy serveri boshqa Windows kompyuterda turadi, uni Mac'dan Claude boshqaradi.

**Muammo:** serverimizda ikkita ma'lumot yetishmayapti:
1. tovarlarning **shtrix-kodlari**;
2. mijozlarning **telefon raqamlari**.

Ikkalasi ham YesPOS ilovasida bor va bu kompyuterda saqlanadi. Keyinroq shu ma'lumotni serverga muntazam uzatib turadigan tizim quriladi. Bu ishni Mac'dagi Claude masofadan qiladi.

**Sening vazifang ikki qismdan iborat:**
- **A.** Mac bu kompyuterga masofadan ulanishi uchun Tailscale va SSH'ni sozlash.
- **B.** YesPOS ma'lumotni qayerda va qanday ko'rinishda saqlashini aniqlash. Bu faqat **o'qish va hisobot berish**, hech narsa o'zgartirilmaydi.

## Qat'iy qoidalar

- **YesPOS ma'lumotlariga HECH QACHON yozma.** Uning bazasini, fayllarini va sozlamalarini o'zgartirma, o'chirma, ko'chirma. Bazani hech qanday rejimda ochma — avval nusxasini olib, faqat **nusxani** o'qi. YesPOS ishlab turgan bo'lishi mumkin, kassani buzib qo'ymaslik eng muhim.
- **YesPOS jarayonini to'xtatma va qayta ishga tushirma.** Kassada savdo ketayotgan bo'lishi mumkin.
- **Shaxsiy ma'lumotni chatga chiqarma.** Mijoz ismi va telefoni o'rniga faqat **soni** va **ustun nomlari**ni ko'rsat. Misol uchun faqat bitta qatorni yashirib ko'rsat: `+99890*****12`.
- Parol, token yoki kalitlarni chatga yozma.
- Tizim xavfsizlik sozlamalarini o'zgartirma. Firewall, UAC va Defender'ni o'chirma; faqat pastda aytilgan aniq qoidani qo'sh.
- Admin huquqi kerak bo'lgan har bir buyruqdan oldin nima qilishini ayt. Terminaling admin bo'lmasa, buyruqni menga ber — men uni admin PowerShell'da o'zim ishga tushiraman.
- Windows rus tilida bo'lishi mumkin. `icacls`da guruh nomi o'rniga SID ishlat: Administrators = `*S-1-5-32-544`, SYSTEM = `*S-1-5-18`.

## A-qism. Masofaviy ulanish

1. **Tailscale:** `winget install -e --id Tailscale.Tailscale`. Kirishni **men o'zim** qilaman, mavjud akkaunt bilan. Kirish havolasi chiqqanda menga ber va kut. Keyin `tailscale ip -4` natijasini ko'rsat.
2. **OpenSSH Server** (admin PowerShell'da):
   ```
   Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
   Start-Service sshd
   Set-Service -Name sshd -StartupType Automatic
   New-NetFirewallRule -Name "sshd-tailscale" -DisplayName "OpenSSH (faqat Tailscale)" -Direction Inbound -Protocol TCP -LocalPort 22 -RemoteAddress 100.64.0.0/10 -Action Allow
   Disable-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue
   New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell -Value "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -PropertyType String -Force
   ```
3. **Mac kaliti.** Quyidagi qatorni kalit fayliga yoz. Foydalanuvchi admin bo'lsa — `C:\ProgramData\ssh\administrators_authorized_keys`, admin bo'lmasa — `%USERPROFILE%\.ssh\authorized_keys`:
   ```
   ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFJvOBHX3fL64HnjRDKketm4m3se+tQv9x75Un4tfPPx prom1@pros-MacBook-Pro.local
   ```
   Admin fayli uchun huquqlarni to'g'irla:
   `icacls C:\ProgramData\ssh\administrators_authorized_keys /inheritance:r /grant "*S-1-5-32-544:F" /grant "*S-1-5-18:F"`
4. **Parol bilan kirish** (`PasswordAuthentication no`) hozircha o'chirilmasin. Mac'dan kalit bilan kirish tekshirilgandan keyin men aytaman.
5. Python o'rnat: `winget install -e --id Python.Python.3.12`. Keyin `C:\chinor-sync` papkasini yarat — keyinchalik sinxron dasturi shu yerda turadi.

## B-qism. YesPOS ma'lumotini topish (faqat o'qish)

1. **O'rnatilgan joy:** YesPOS qayerga o'rnatilgan va qaysi jarayon nomi bilan ishlaydi? Quyidagi joylarni tekshir:
   - `Get-Process`;
   - `Program Files`, `Program Files (x86)`;
   - `%APPDATA%`, `%LOCALAPPDATA%`, `C:\ProgramData`.
2. **Ma'lumotlar bazasi:** YesPOS o'z ma'lumotini qayerda saqlaydi? Quyidagi turlarni tekshir:
   - SQLite (`.db`, `.sqlite`), Firebird (`.fdb`, `.gdb`), Access (`.mdb`);
   - IndexedDB / LevelDB (agar u Electron ilova bo'lsa);
   - mahalliy PostgreSQL yoki MySQL xizmati (`Get-Service`, ochiq portlar `netstat -ano`);
   - yoki ma'lumot faqat bulutda bo'lishi va lokal saqlanmasligi ham mumkin.

   Fayl hajmlari va oxirgi o'zgargan vaqtini ko'rsat.
3. **Baza tuzilishi:** bazani `C:\chinor-sync\probe\` papkasiga **nusxalab**, nusxani o'qi. Quyidagilarni top:
   - **tovarlar** jadvali: ID / SKU / artikul ustuni, **shtrix-kod** ustuni (yoki alohida shtrix-kodlar jadvali — bitta tovarda bir nechta kod bo'lishi mumkin), nomi;
   - **mijozlar** jadvali: ID, ismi, **telefon**, qarzi (agar bo'lsa);
   - har jadvalda nechta qator borligi;
   - tovar ID'si serverdagi YesPOS **SKU** raqamiga mos keladimi? Masalan, 5 xonali yoki `33203697` ko'rinishidagi raqamlar.
4. **Bulut varianti:** baza lokal bo'lmasa, YesPOS qaysi manzil bilan ishlaydi (domen nomi)? Ilova ichida «Eksport» (Excel/CSV) funksiyasi bormi? Bor bo'lsa, menyuning qayerida joylashganini ayt.
5. **Nusxani o'chir:** ish tugagach `C:\chinor-sync\probe\` ichidagi baza nusxasini o'chir. Unda shaxsiy ma'lumot bor.

## Yakuniy hisobot

Oxirida quyidagilarni yoz:
- Tailscale IP, kompyuter nomi va foydalanuvchi nomi;
- SSH ishlayaptimi;
- YesPOS qayerda o'rnatilgan, ma'lumot turi va joylashuvi (fayl yo'li yoki xizmat va port);
- tovarlar va shtrix-kod jadvallari: jadval nomlari, ustun nomlari, qatorlar soni, ID va SKU mosligi;
- mijozlar jadvali: jadval nomi, ustun nomlari, qatorlar soni — ma'lumotning o'zi **emas**;
- bajarilmay qolgan qadamlar va menga topshiriladigan ishlar.

---

## Keyin Mac'da nima qilinadi (ma'lumot uchun)

1. Mac'dan `ssh <user>@<tailscale-ip>` orqali ulanishni tekshiramiz, keyin parol bilan kirishni o'chiramiz.
2. Hisobotga qarab `C:\chinor-sync` ichiga kichik dastur yoziladi. U har 15–30 daqiqada:
   - YesPOS bazasining nusxasini oladi;
   - undan faqat shtrix-kodlar va mijoz telefonlarini o'qiydi;
   - ularni asosiy serverga yuboradi.
3. Server bu ma'lumotni tovarlarga (SKU orqali) va mijozlarga biriktiradi. Shundan keyin skanerlar barcha tovarni taniydi, qarzdorlar ro'yxatida esa telefon raqamlari chiqadi.
4. Dastur ham birinchi Windows'dagi kabi kompyuter yonganda o'zi ishga tushadi va Mac'dan boshqariladi.
