# YesPOS kompyuteri — batafsil prompt (2-versiya)

Quyidagi `---` chiziqlar orasidagi matnni Windows'dagi Claude'ga **to'liq** nusxalab bering. Oldingi suhbat chalkashib ketgan bo'lsa, yangi suhbat ochib, matnni o'sha yerga qo'ying.

---

# Vazifa: do'kon kassa kompyuterini sozlash (bosqichma-bosqich)

## Kontekst — avval o'qib chiq

- Bu kompyuter **"Chinor" do'konining o'z kassa kompyuteri**. Men uning egasiman.
- Kompyuterda **YesPOS** kassa dasturi ishlaydi. Bu do'konning o'z dasturi, ma'lumotlari ham do'konniki.
- Maqsad: do'konning **o'z** tovarlaridagi shtrix-kodlarni va **o'z** mijozlarining telefon raqamlarini do'konning boshqa kompyuteridagi o'z serveriga ko'chirish.
- Hech narsa buzilmaydi va o'zgartirilmaydi. Biz faqat **o'qiymiz**.

## Umumiy qoidalar

1. **Bosqichlarni tartib bilan bajar.** Har bosqich oxirida «✅ N-bosqich tugadi» deb yoz va natijani ko'rsat.
2. **Faqat shu yerda yozilgan buyruqlarni ishlat.** O'zingdan boshqa usul o'ylab topma. Ayniqsa:
   - DLL fayllarni o'zing yuklama, `Add-Type` / P/Invoke ishlatma;
   - Git yoki boshqa dastur ichidagi fayllardan foydalanma;
   - parol yoki himoyani chetlab o'tishga urinma.
3. Biror buyruq xato bersa, **to'xta**, xatoni menga ko'rsat va kut. O'zing aylanib o'tish yo'lini qidirma.
4. **YesPOS dasturini yopma va uning fayllarini o'zgartirma.** Faqat nusxa olamiz va nusxani o'qiymiz.
5. Mijozlarning ismi va telefonini chatga **chiqarma**. Faqat sonini ayt: masalan, «mijozlar: 1240 qator».
6. «Admin kerak» deyilgan buyruqni men o'zim admin PowerShell'da ishga tushiraman. Sen menga faqat buyruq matnini ber.

---

## 1-bosqich. Tailscale (masofaviy tarmoq)

Oddiy PowerShell'da:
```
winget install -e --id Tailscale.Tailscale
```
O'rnatish tugagach, menga **«Tailscale'ga kiring»** deb yoz va kut. Men ekranning pastki o'ng burchagidagi Tailscale belgisidan o'zim kiraman.

Men «kirdim» deganimdan keyin quyidagini ishga tushir va natijasini ko'rsat:
```
& "C:\Program Files\Tailscale\tailscale.exe" ip -4
```

## 2-bosqich. SSH server — admin kerak

Menga quyidagi buyruqlarni **bitta blok** qilib ber. Men ularni admin PowerShell'da ishga tushiraman:
```
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
New-NetFirewallRule -Name "sshd-tailscale" -DisplayName "OpenSSH (faqat Tailscale)" -Direction Inbound -Protocol TCP -LocalPort 22 -RemoteAddress 100.64.0.0/10 -Action Allow
Disable-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue
New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell -Value "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -PropertyType String -Force
Set-Content -Path C:\ProgramData\ssh\administrators_authorized_keys -Value "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFJvOBHX3fL64HnjRDKketm4m3se+tQv9x75Un4tfPPx prom1@pros-MacBook-Pro.local" -Encoding ascii
icacls C:\ProgramData\ssh\administrators_authorized_keys /inheritance:r /grant "*S-1-5-32-544:F" /grant "*S-1-5-18:F"
whoami
```
Men natijani yuborganimdan keyin `whoami` qatoridan **foydalanuvchi nomini** yozib qo'y.

## 3-bosqich. Python

Oddiy PowerShell'da:
```
winget install -e --id Python.Python.3.12
```
Keyin **yangi** PowerShell oynasini och va tekshir:
```
py -3.12 --version
```

## 4-bosqich. YesPOS fayllarini topish — faqat ro'yxat

Quyidagi buyruq hech narsa ochmaydi, faqat fayl nomlarini ro'yxat qiladi:
```
Get-Process | Where-Object { $_.Path -and $_.Path -match "yes" } | Select-Object Name, Path | Format-List
$dirs = @("$env:ProgramFiles","${env:ProgramFiles(x86)}","$env:APPDATA","$env:LOCALAPPDATA","$env:ProgramData","C:\")
foreach ($d in $dirs) { Get-ChildItem -Path $d -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -match "yes|pos" } | Select-Object FullName }
```
Topilgan YesPOS papkasi ichida baza fayllarini qidir. `<PAPKA>` o'rniga topilgan yo'lni qo'y:
```
Get-ChildItem -Path "<PAPKA>" -Recurse -ErrorAction SilentlyContinue -Include *.db,*.sqlite,*.sqlite3,*.fdb,*.gdb,*.mdb,*.ldb | Select-Object FullName, Length, LastWriteTime
```
Natijani menga ko'rsat. Shunda **to'xta va kut** — qaysi faylni o'qishni men aytaman.

## 5-bosqich. Bazaning nusxasini o'qish — men faylni aytganimdan keyin

`<FAYL>` o'rniga men aytgan faylning to'liq yo'lini qo'y:
```
New-Item -ItemType Directory -Force C:\chinor-sync\probe | Out-Null
Copy-Item "<FAYL>" C:\chinor-sync\probe\copy.db
```
Keyin `C:\chinor-sync\probe\read.py` faylini aynan shu matn bilan yarat:
```python
import sqlite3
con = sqlite3.connect("file:C:/chinor-sync/probe/copy.db?mode=ro", uri=True)
for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
    cols = [r[1] for r in con.execute(f'PRAGMA table_info("{name}")')]
    n = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
    print(f"{name} ({n} qator): {', '.join(cols)}")
```
Va ishga tushir:
```
py -3.12 C:\chinor-sync\probe\read.py
```
Bu skript faqat **jadval nomlari, ustun nomlari va qatorlar sonini** chiqaradi. Ma'lumotning o'zini chiqarmaydi.

Agar «file is not a database» xatosi chiqsa, fayl SQLite emas. To'xta va menga ayt.

## 6-bosqich. Tozalash

```
Remove-Item C:\chinor-sync\probe\copy.db
```

## Yakuniy hisobot — shu shaklda yoz

```
Tailscale IP: 100.x.x.x
Foydalanuvchi: ...
SSH: ishlayapti / xato (...)
Python: 3.12.x
YesPOS papkasi: ...
Baza fayli: ... (hajmi, oxirgi o'zgarish)
Jadvallar: (5-bosqich natijasi to'liq)
Bajarilmagan bosqichlar: ...
```

---

## Ma'lumot uchun

- Agar Windows Claude 4–5-bosqichlarda yana to'xtab qolsa, unga faqat **1–3-bosqichlarni** bajartiring. Ulanish tayyor bo'lgach, bazani Mac'dan o'zim tekshiraman.
- YesPOS bazasi SQLite bo'lmasligi ham mumkin: masalan, Firebird yoki bulutda saqlanadigan bo'lishi mumkin. Bunday holatda 4-bosqich natijasiga qarab keyingi yo'lni men tanlayman.
