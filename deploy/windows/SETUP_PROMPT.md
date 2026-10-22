# Windows'dagi Claude uchun prompt

Quyidagi `---` chiziqlar orasidagi matnni ofisdagi Windows kompyuterda ochilgan Claude Code'ga to'liq nusxalab bering.

---

Sen ofisdagi Windows kompyuterni "Chinor POS" do'koni uchun **zaxira server** sifatida tayyorlaysan. Hozircha asosiy server Mac'da ishlaydi. Bu Windows unga masofadan ulanadigan va keyinchalik serverni o'z zimmasiga oladigan mashina bo'ladi.

## Maqsad

1. Mac'dan bu Windows'ga **masofadan ulanish** bo'lsin: buyruq yuborish (SSH) va fayl yuborish (scp). Ulanish istalgan tarmoqdan, Tailscale orqali ishlashi kerak.
2. Server ishlashi uchun kerakli dasturlar o'rnatilsin. **Botni hali ishga tushirma.**

## Qat'iy qoidalar

- **Botni (`python main.py`) va `cloudflared tunnel run`ni ishga TUSHIRMA.** Ular hozir Mac'da ishlayapti. Ikkinchi nusxa ishga tushsa, Telegram 409 xatosi chiqadi va kassa tunneli ikkiga bo'linadi. Bu ishni keyin, men aytganimda, Mac'dan bajaramiz.
- Parol, token yoki kalitlarni chatga yozma va hech qayerga yuborma.
- Tizim xavfsizlik sozlamalarini, ya'ni Firewall'ni butunlay o'chirish, UAC yoki Defender'ni, o'zgartirma. Faqat pastda aytilgan aniq qoidalarni qo'sh.
- Administrator huquqi kerak bo'lgan har bir buyruqdan oldin nima qilishini ayt. Agar sening terminaling admin emas bo'lsa, buyruqni menga ber, men admin PowerShell'da o'zim ishga tushiraman.
- Har bir bosqich oxirida natijani tekshir va qisqa hisobot ber.

## 1-bosqich. Tekshiruv

Quyidagilarni aniqlab, menga ayt:
- Windows versiyasi; kompyuter nomi va foydalanuvchi nomi (`whoami`);
- disk joyi;
- `winget` bormi;
- uyqu sozlamalari (`powercfg /a` va joriy reja).

## 2-bosqich. Dasturlar (winget orqali)

```
winget install -e --id Git.Git
winget install -e --id Python.Python.3.12
winget install -e --id Cloudflare.cloudflared
winget install -e --id Gyan.FFmpeg
winget install -e --id Tailscale.Tailscale
```

O'rnatib bo'lgach, yangi terminalda tekshir: `git --version`, `py -3.12 --version`, `cloudflared --version`, `ffmpeg -version`.

## 3-bosqich. Tailscale (masofaviy tarmoq)

1. Tailscale'ni ishga tushir. Tizimga kirishni **men o'zim** qilaman, Mac'dagi akkauntning o'zi bilan. Brauzerda kirish oynasi ochilganda menga ayt va kut.
2. Kirgandan keyin `tailscale ip -4` va `tailscale status` natijasini ko'rsat. Mac shu IP orqali ulanadi.

## 4-bosqich. OpenSSH Server (Mac'dan buyruq va fayl yuborish uchun)

Admin PowerShell'da:

```
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
```

Firewall qoidasi faqat Tailscale tarmog'idan ruxsat bersin (100.64.0.0/10):

```
New-NetFirewallRule -Name "sshd-tailscale" -DisplayName "OpenSSH (faqat Tailscale)" -Direction Inbound -Protocol TCP -LocalPort 22 -RemoteAddress 100.64.0.0/10 -Action Allow
```

Kalit bilan kirish sozlanadi, parol bilan emas:
- Men senga Mac'ning **ochiq** kalitini beraman (`ssh-ed25519 ...` bilan boshlanadigan bitta qator).
- Foydalanuvchi admin bo'lsa, kalitni `C:\ProgramData\ssh\administrators_authorized_keys` ga yoz. Admin bo'lmasa, `%USERPROFILE%\.ssh\authorized_keys` ga yoz.
- `administrators_authorized_keys` uchun huquqlarni to'g'irla:
  `icacls C:\ProgramData\ssh\administrators_authorized_keys /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"`
- So'ng `C:\ProgramData\ssh\sshd_config` faylida `PasswordAuthentication no` qatorini yoq va `Restart-Service sshd` qil. Buni kalit bilan kirish Mac'dan ishlashi tasdiqlangandan **keyingina** qil.
- SSH'da standart shell PowerShell bo'lsin:
  `New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell -Value "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -PropertyType String -Force`

## 5-bosqich. Server papkasi va Python muhiti

- `C:\chinor` papkasini yarat. Kod va ma'lumotlarni Mac o'zi scp orqali shu yerga yuboradi, sen hech narsa yuklab olma.
- Python muhitini yarat: `py -3.12 -m venv C:\chinor\.venv`
- Kerakli paketlarni o'rnat:
  `C:\chinor\.venv\Scripts\pip install "aiogram==3.7.0" "python-dotenv==1.0.0" "openpyxl==3.1.2" "pyzbar==0.1.9" "Pillow>=10" "onnxruntime>=1.17" numpy requests "telethon>=1.34" groq opencv-python-headless`
  (`uvloop` Windows'da ishlamaydi, uni o'rnatma.)
- Tekshir: `C:\chinor\.venv\Scripts\python -c "import aiogram, PIL, onnxruntime, telethon, openpyxl, cv2; print('ok')"`

## 6-bosqich. Server sifatida ishlashga tayyorlash

- Kompyuter tok ulangan holatda uxlamasin:
  `powercfg /change standby-timeout-ac 0` va `powercfg /change hibernate-timeout-ac 0`
- Windows Update kompyuterni ish vaqtida (8:00–22:00) qayta yoqmasin. "Active hours" sozlamasini qo'yishni menga tushuntir, o'zing o'zgartirma.
- Hozircha **avtomatik ishga tushirishni (Task Scheduler yoki xizmat) yaratma**. Uni ko'chirish kuni Mac'dan sozlaymiz.

## Yakuniy hisobot

Oxirida quyidagilarni yoz:
- Tailscale IP;
- foydalanuvchi nomi;
- SSH ishlayaptimi;
- o'rnatilgan versiyalar;
- bajarilmay qolgan yoki menga topshirilgan qadamlar.

---

## Keyin Mac'da nima qilinadi (ma'lumot uchun)

1. Windows hisobotidagi Tailscale IP va foydalanuvchi nomi bilan Mac'dan ulanishni tekshiramiz: `ssh <user>@<tailscale-ip>`.
2. Kodni (`chinor-bot`, `marketplace`) va sirlarni scp orqali yuboramiz. Sirlar: `.env` fayllar, `~/.cloudflared/*.json`, Telethon sessiyasi. Ular git'ga tushmaydi.
3. Ko'chirish kuni ketma-ketlik:
   - Mac'da bot va tunnel to'xtatiladi;
   - `pos.db` va `marketplace.db` Windows'ga yuboriladi;
   - Windows'da bot va tunnel ishga tushirilib, avtomatik yoqiladigan qilib sozlanadi;
   - `kassa.chinorpos.com` tekshiriladi.
