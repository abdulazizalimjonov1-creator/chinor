# Chinor sennik agentini shu kompyuterga o'rnatadi.
#
# Nima qiladi: config.json yozadi va "kirishda ishga tushadigan" vazifa
# yaratadi. Administrator huquqi KERAK EMAS, dastur joriy foydalanuvchi
# nomidan ishlaydi va ekranda ko'rinmaydi.
#
# Ishlatish (shu papkada, PowerShell'da):
#   .\install.ps1 -Server https://kassa.chinorpos.com -Token <kalit> `
#                 -Key qabul -Name "Qabul kompyuteri"
#
# -Printer berilmasa, nomida "XP-365" bor printer o'zi topiladi.

param(
  [Parameter(Mandatory=$true)][string]$Server,
  [Parameter(Mandatory=$true)][string]$Token,
  [Parameter(Mandatory=$true)][string]$Key,
  [string]$Name = "",
  [string]$Printer = "",
  [string]$TaskName = "Chinor-sennik"
)

$ErrorActionPreference = 'Stop'
$base = Split-Path -Parent $MyInvocation.MyCommand.Definition
if (-not $Name) { $Name = $env:COMPUTERNAME }

if (-not $Printer) {
    $cands = @(Get-CimInstance Win32_Printer | Where-Object { $_.Name -match 'XP-?365' } |
               Select-Object -ExpandProperty Name)
    # Tarmoq orqali ko'rinadigan nusxasi emas, USB ga ulangani kerak
    $usb = @($cands | Where-Object { $_ -notmatch '@' })
    if ($usb.Count -gt 0) { $Printer = $usb[0] } elseif ($cands.Count -gt 0) { $Printer = $cands[0] }
}
if (-not $Printer) {
    Write-Host "Yorliq printeri topilmadi. Printerlar ro'yxati:" -ForegroundColor Yellow
    Get-CimInstance Win32_Printer | Select-Object -ExpandProperty Name
    throw "Printer nomini -Printer bilan ko'rsating"
}

$cfg = [ordered]@{ server = $Server.TrimEnd('/'); token = $Token; key = $Key
                   name = $Name; printer = $Printer }
# UTF-8, BOM'siz: BOM bilan yozilsa boshqa dasturlar faylni o'qiy olmaydi
[IO.File]::WriteAllText((Join-Path $base 'config.json'),
    ($cfg | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
Write-Host "config.json yozildi — printer: $Printer" -ForegroundColor Green

# Eski nusxa ishlab tursa — to'xtatamiz. Aks holda qayta o'rnatgandan keyin
# ikkita agent birga ishlaydi va eskisi hali ham eski sozlama bilan turadi.
schtasks /Query /TN $TaskName 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { schtasks /End /TN $TaskName 2>$null | Out-Null }
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like '*ChinorSennik*' -and $_.ProcessId -ne $PID } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$vbs = Join-Path $base 'start-hidden.vbs'
schtasks /Create /SC ONLOGON /TN $TaskName /TR "wscript.exe `"$vbs`"" /RL LIMITED /F | Out-Null
schtasks /Run /TN $TaskName | Out-Null
Write-Host "«$TaskName» vazifasi yaratildi va ishga tushdi." -ForegroundColor Green
Write-Host "Jurnal: $base\agent.log"
