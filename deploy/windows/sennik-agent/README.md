# Sennik agenti (USB yorliq printeri uchun)

XP-365B kompyuterga **USB** bilan ulangan — serverdan unga to'g'ridan-to'g'ri
yozib bo'lmaydi, brauzerdan chop etish esa har safar oyna ochadi. Shuning
uchun printer ulangan kompyuterda shu kichik dastur turadi: serverdan
navbatni so'raydi va kelgan baytlarni printerga uzatadi.

**Ekranda hech narsa ko'rinmaydi** — oyna ham, savol ham yo'q. Kompyuterda
ishlayotgan odam faqat printerdan chiqqan sennikni ko'radi.

## Eng tez yo'l: bitta buyruq

Printer ulangan kompyuterda PowerShell ochib (Boshlash → `powershell`),
shuni to'liq nusxalab qo'ying — papkani ko'chirish shart emas, agent
serverdan yuklab olinadi:

```powershell
[Net.ServicePointManager]::SecurityProtocol='Tls12'; $d='C:\ChinorSennik'; md $d -Force|Out-Null; iwr https://kassa.chinorpos.com/updates/sennik-agent.zip -OutFile "$env:TEMP\sa.zip" -UseBasicParsing; Expand-Archive "$env:TEMP\sa.zip" $d -Force; Get-ChildItem $d | Unblock-File; powershell -NoProfile -ExecutionPolicy Bypass -File $d\install.ps1 -Server https://kassa.chinorpos.com -Token <KALIT> -Key qabul -Name "Qabul kompyuteri"
```

Skript `-ExecutionPolicy Bypass` bilan ALOHIDA chaqiriladi: ko'p
kompyuterlarda PowerShell skript ishlatishni taqiqlab qo'yadi
(«выполнение сценариев отключено»). `Unblock-File` esa internetdan
yuklangan fayldagi «begona» belgisini olib tashlaydi. Tizim sozlamasi
o'zgartirilmaydi.

`<KALIT>` ni administratordan oling (`deploy/sennik-deploy.sh` oxirida
chiqadi). Boshqa kompyuterda `-Key` va `-Name` ni almashtiring.

Zip serverda `chinor-bot/updates/sennik-agent.zip` da turadi — yangilansa,
shu buyruqning o'zi yetarli (ichida maxfiy ma'lumot yo'q, kalit alohida
beriladi).

## Qo'lda o'rnatish

1. Shu papkani kompyuterga ko'chiring, masalan `C:\ChinorSennik\`.
2. O'sha papkada PowerShell oching (papkada **Shift + o'ng tugma** →
   «Open PowerShell window here»).
3. Buyruqni kiriting (kalitni Chinor administratoridan oling):

```powershell
.\install.ps1 -Server https://kassa.chinorpos.com -Token <KALIT> -Key qabul -Name "Qabul kompyuteri"
```

`-Key` — har kompyuterga o'ziniki: `qabul` (60×40 printer) va `server`
(30×20 printer). Mini App'da shu nom ko'rinadi.

Tayyor. Dastur darhol ishga tushadi va kompyuter har yoqilganda (kirishda)
o'zi tiklanadi.

## Printerni alohida sinash

Serverga bog'lanmasdan, printerning o'zini tekshirish (nosozlikda birinchi
qadam) — tayyor sinov sennigi faylini to'g'ridan-to'g'ri yuboradi:

```powershell
powershell -ExecutionPolicy Bypass -File print-test.ps1
```

Yorliq chiqsa — printer, o'lcham va drayver joyida; muammo serverda yoki
tarmoqda. Chiqmasa — printerni, qog'ozni va `config.json` dagi printer
nomini tekshiring.

## Tekshirish

- Jurnal: `agent.log` — «Ishga tushdi», keyin har chop etilgan sennik.
- Mini App → 🏷 Sennik chop etish → printer ro'yxatida kompyuter
  «ulangan» bo'lib ko'rinishi kerak.

## Savol-javob

**Kompyuterga og'irlik qilmaydimi?** Yo'q: dastur deyarli doim serverdan
javob kutib turadi, protsessorni ishlatmaydi (bo'sh turganda ~0%).

**Printer o'chiq bo'lsa?** Dastur 3 marta urinadi (5 soniyadan). Baribir
chiqmasa, xato `agent.log` ga yoziladi va Mini App'da ko'rinadi — printerni
yoqib, Mini App'dan qaytadan bosiladi.

**Kompyuter o'chiq bo'lsa?** Sennik serverdagi navbatda turadi; kompyuter
yoqilgach o'zi chiqadi (bot qayta ishga tushsa navbat tozalanadi).

**«Выполнение сценариев отключено» desa:** skriptni to'g'ridan-to'g'ri
emas, `powershell -ExecutionPolicy Bypass -File <yo'l>` orqali chaqiring
(yuqoridagi buyruqda shunday). Tizim siyosatini o'zgartirish shart emas —
agentning o'zi ham shu usulda ishga tushadi.

**O'chirish:** `schtasks /Delete /TN Chinor-sennik /F` va papkani o'chiring.

**Printer nomi boshqacha bo'lsa:** `install.ps1` ga `-Printer "To'liq nomi"`
qo'shing (nomlarni `Get-Printer` ko'rsatadi).

## Qora oyna miltillab turibdimi?

Agentning o'zi hech qachon oyna ochmaydi (`wscript` orqali ko'rinmas
ishga tushadi). Miltillash odatda BOSHQA rejalashtirilgan vazifadan
bo'ladi: Vazifalar rejalashtiruvchisi `powershell.exe` ni to'g'ridan-to'g'ri
chaqirsa, `-WindowStyle Hidden` bilan ham oyna bir lahza paydo bo'ladi
(avval yaratiladi, keyin yashiriladi).

Qaysi vazifa ekanini topish:

```powershell
Get-ScheduledTask | ForEach-Object { foreach ($t in $_.Triggers) { if ($t.Repetition.Interval) { $_.TaskName + "  " + $t.Repetition.Interval + "  " + $_.Actions[0].Execute } } }
```

Davosi — shu papkadagi `start-hidden.vbs` kabi kichik VBS yozib, vazifani
`wscript.exe "<yo'l>.vbs"` ga o'tkazish. 2026-10-29 da qabul kompyuterida
`Chinor-Sync` (har 30 daqiqada) aynan shunday tuzatilgan.
