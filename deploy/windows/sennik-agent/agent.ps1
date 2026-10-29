# Chinor — sennik agenti.
#
# Printer ulangan kompyuterda JIM turadi: serverdan navbatni so'rab, kelgan
# baytlarni USB printerga uzatadi. Ekranda hech narsa ochilmaydi, hech narsa
# so'ralmaydi — kompyuterda ishlayotgan odam buni sezmaydi.
#
# Sennik ko'rinishi (nom, narx, SKU, logotip) SERVERDA chiziladi; bu yerga
# tayyor baytlar keladi. Shuning uchun ko'rinish o'zgarsa, bu faylga tegish
# shart emas.
#
# Qo'lda ishga tushirish (sinash uchun): powershell -File agent.ps1 -Once

param([switch]$Once)

$ErrorActionPreference = 'Stop'
$base = Split-Path -Parent $MyInvocation.MyCommand.Definition
$cfgPath = Join-Path $base 'config.json'
$logPath = Join-Path $base 'agent.log'

function Write-Log($msg) {
    try {
        if ((Test-Path $logPath) -and ((Get-Item $logPath).Length -gt 512KB)) {
            # Jurnal cheksiz o'smasin — faqat oxirgi qismini qoldiramiz
            (Get-Content $logPath -Tail 200) | Set-Content $logPath
        }
        Add-Content $logPath ("{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg)
    } catch { }
}

if (-not (Test-Path $cfgPath)) { Write-Log "config.json topilmadi"; exit 1 }
$cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json

# Windows PowerShell 5 sukut bo'yicha TLS 1.0 bilan ulanadi — Cloudflare
# ortidagi server esa uni rad etadi va agent «aloqa yo'q» deb o'tiraveradi.
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

# Printerga XOM (RAW) baytlarni yuborish — Windows drayveri matnga
# aralashmasin: TSPL buyruqlari printerga o'zgarishsiz borishi kerak.
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class ChinorRaw {
  [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Ansi)]
  public struct DI { [MarshalAs(UnmanagedType.LPStr)] public string n;
                     [MarshalAs(UnmanagedType.LPStr)] public string p;
                     [MarshalAs(UnmanagedType.LPStr)] public string t; }
  [DllImport("winspool.Drv",EntryPoint="OpenPrinterA",SetLastError=true,CharSet=CharSet.Ansi)] public static extern bool OpenPrinter(string s,out IntPtr h,IntPtr d);
  [DllImport("winspool.Drv",EntryPoint="ClosePrinter",SetLastError=true)] public static extern bool ClosePrinter(IntPtr h);
  [DllImport("winspool.Drv",EntryPoint="StartDocPrinterA",SetLastError=true,CharSet=CharSet.Ansi)] public static extern bool StartDocPrinter(IntPtr h,int l,ref DI di);
  [DllImport("winspool.Drv",EntryPoint="EndDocPrinter",SetLastError=true)] public static extern bool EndDocPrinter(IntPtr h);
  [DllImport("winspool.Drv",EntryPoint="StartPagePrinter",SetLastError=true)] public static extern bool StartPagePrinter(IntPtr h);
  [DllImport("winspool.Drv",EntryPoint="EndPagePrinter",SetLastError=true)] public static extern bool EndPagePrinter(IntPtr h);
  [DllImport("winspool.Drv",EntryPoint="WritePrinter",SetLastError=true)] public static extern bool WritePrinter(IntPtr h,IntPtr b,int c,out int w);
  public static void Send(string pn, byte[] data) {
    IntPtr h;
    if(!OpenPrinter(pn, out h, IntPtr.Zero)) throw new Exception("Printer ochilmadi: "+Marshal.GetLastWin32Error());
    var di = new DI(); di.n = "Chinor sennik"; di.t = "RAW";
    IntPtr p = Marshal.AllocCoTaskMem(data.Length);
    Marshal.Copy(data, 0, p, data.Length);
    int w;
    try {
      if(!StartDocPrinter(h, 1, ref di)) throw new Exception("StartDoc: "+Marshal.GetLastWin32Error());
      StartPagePrinter(h);
      if(!WritePrinter(h, p, data.Length, out w)) throw new Exception("WritePrinter: "+Marshal.GetLastWin32Error());
      EndPagePrinter(h); EndDocPrinter(h);
    } finally { Marshal.FreeCoTaskMem(p); ClosePrinter(h); }
  }
}
'@

$headers = @{ 'X-Print-Token' = $cfg.token }
$pollUrl = $cfg.server.TrimEnd('/') + '/api/print-agent/poll'
$doneUrl = $cfg.server.TrimEnd('/') + '/api/print-agent/done'
Write-Log "Ishga tushdi: $($cfg.name) → $($cfg.printer)"
$quiet = $false          # aloqa uzilganini jurnalga bir marta yozamiz

while ($true) {
    try {
        $printers = @()
        try { $printers = @(Get-CimInstance Win32_Printer | Select-Object -ExpandProperty Name) } catch { }
        $body = @{ key = $cfg.key; name = $cfg.name; printer = $cfg.printer
                   printers = $printers } | ConvertTo-Json -Compress
        # Server navbat bo'shligida javobni ~20 s ushlab turadi — shuning
        # uchun bu so'rov "osilib qolgan" emas, aksincha sennik bir zumda
        # chiqishi uchun shunday.
        $r = Invoke-RestMethod -Uri $pollUrl -Method Post -Body $body `
             -ContentType 'application/json' -Headers $headers -TimeoutSec 60
        $quiet = $false
        foreach ($j in @($r.jobs)) {
            $ok = $false; $err = ''
            $bytes = [Convert]::FromBase64String($j.data)
            # Printer bir zum band bo'lishi yoki yangi yoqilgan bo'lishi
            # mumkin — darrov taslim bo'lmaymiz, 3 marta urinamiz.
            for ($try = 1; $try -le 3; $try++) {
                try {
                    [ChinorRaw]::Send($cfg.printer, $bytes)
                    $ok = $true; $err = ''
                    Write-Log "chop etildi: $($j.name)"
                    break
                } catch {
                    $err = $_.Exception.Message
                    Write-Log "urinish $try/3 ($($j.name)): $err"
                    if ($try -lt 3) { Start-Sleep -Seconds 5 }
                }
            }
            try {
                $d = @{ key = $cfg.key; id = $j.id; ok = $ok; error = $err } | ConvertTo-Json -Compress
                Invoke-RestMethod -Uri $doneUrl -Method Post -Body $d `
                    -ContentType 'application/json' -Headers $headers -TimeoutSec 20 | Out-Null
            } catch { }
        }
    } catch {
        if (-not $quiet) { Write-Log "server bilan aloqa yo'q: $($_.Exception.Message)"; $quiet = $true }
        Start-Sleep -Seconds 10
    }
    if ($Once) { break }
}
