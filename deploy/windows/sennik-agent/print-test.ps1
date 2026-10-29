# Sinov sennigi: tayyor TSPL faylni printerga XOM holda yuboradi.
# Nima uchun kerak: printer ulanganini va yorliq o'lchami to'g'riligini
# serverga bog'lanmasdan tekshirish uchun (nosozlikni izlashda birinchi qadam).
#
#   powershell -ExecutionPolicy Bypass -File print-test.ps1 [-File test_30x20.bin]

param([string]$File = "test_30x20.bin", [string]$Printer = "")

$ErrorActionPreference = 'Stop'
$base = Split-Path -Parent $MyInvocation.MyCommand.Definition
if (-not $Printer) {
    $cfg = Get-Content (Join-Path $base 'config.json') -Raw | ConvertFrom-Json
    $Printer = $cfg.printer
}
$path = if (Test-Path $File) { $File } else { Join-Path $base $File }
$bytes = [IO.File]::ReadAllBytes($path)

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

[ChinorRaw]::Send($Printer, $bytes)
Write-Host "Yuborildi: $path ($($bytes.Length) bayt) -> $Printer" -ForegroundColor Green
