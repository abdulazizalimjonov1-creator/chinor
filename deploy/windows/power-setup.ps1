# Chinor server — quvvat sozlamalari (noutbuk, tok tez-tez o'chadi).
# Admin PowerShell'da:  powershell -ExecutionPolicy Bypass -File C:\chinor\power-setup.ps1
#
# Natija:
#  * Qopqoq yopilsa — tokda ham, batareyada ham HECH NARSA qilmaydi (server ishlayveradi).
#  * Tok o'chsa — kompyuter batareyada uxlamay ishlashda davom etadi.
#  * Batareya 7% ga tushsa — GIBERNATSIYA (hammasi diskka saqlanadi, batareya
#    to'liq tugab ma'lumot buzilmaydi). Quvvat tugmasini bossangiz, bot va tunnel
#    to'xtagan joyidan davom etadi.
#  * Wi-Fi batareyada ham to'liq quvvatda (ulanish uzilmasin).

$ErrorActionPreference = "Stop"
$S = "SCHEME_CURRENT"

# 1) Qopqoq: 0 = hech narsa
powercfg /setacvalueindex $S SUB_BUTTONS LIDACTION 0
powercfg /setdcvalueindex $S SUB_BUTTONS LIDACTION 0

# 2) Uyqu va gibernatsiya taymerlari: 0 = hech qachon (tokda ham, batareyada ham)
powercfg /change standby-timeout-ac 0
powercfg /change standby-timeout-dc 0
powercfg /change hibernate-timeout-ac 0
powercfg /change hibernate-timeout-dc 0

# 3) Batareya: kritik daraja 7% → gibernatsiya (2); past daraja 15% → faqat ogohlantirish (0)
powercfg /hibernate on
powercfg /setdcvalueindex $S SUB_BATTERY BATLEVELCRIT 7
powercfg /setdcvalueindex $S SUB_BATTERY BATACTIONCRIT 2
powercfg /setacvalueindex $S SUB_BATTERY BATACTIONCRIT 2
powercfg /setdcvalueindex $S SUB_BATTERY BATLEVELLOW 15
powercfg /setdcvalueindex $S SUB_BATTERY BATACTIONLOW 0

# 4) Wi-Fi adapteri: batareyada ham maksimal ishlash (0)
powercfg /setdcvalueindex $S 19cada83-c91f-4b02-9c4f-ff3e5f1b88c1 12bbebe6-58d6-4636-95bb-3217ef867c1a 0
powercfg /setacvalueindex $S 19cada83-c91f-4b02-9c4f-ff3e5f1b88c1 12bbebe6-58d6-4636-95bb-3217ef867c1a 0

# 5) Quvvat tugmasi bosilsa — o'chirish o'rniga hech narsa qilmasin (tasodifan bosilmasin)
#    Kerak bo'lsa izohdan chiqaring:
# powercfg /setacvalueindex $S SUB_BUTTONS PBUTTONACTION 0

powercfg /setactive $S
Write-Host "Tayyor. Joriy qiymatlar:" -ForegroundColor Green
$k = "HKLM:\SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes"
$g = (powercfg /getactivescheme) -replace '.*:\s*([0-9a-f-]{36}).*', '$1'
$lid = Get-ItemProperty "$k\$g\4f971e89-eebd-4455-a8de-9e59040e7347\5ca83367-6e45-459f-a27b-476b1d01c936" -ErrorAction SilentlyContinue
"Qopqoq: tokda=$($lid.ACSettingIndex) batareyada=$($lid.DCSettingIndex)  (0 = hech narsa)"
