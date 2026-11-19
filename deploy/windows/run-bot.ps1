# Chinor bot: yiqilsa 10 soniyadan keyin qayta ishga tushadi (Task Scheduler, SYSTEM).
$env:DATA_DIR = "C:\chinor\data"
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONIOENCODING = "utf-8"
# Asosiy log — bot o'zi yozadi (UTF-8, 20 MB x 5 aylanish). Bu yerga faqat
# logging'dan oldingi qulashlar va print()lar tushadi.
$env:BOT_LOG_FILE = "C:\chinor\logs\bot.log"
$console = "C:\chinor\logs\bot-console.log"
$ff = Get-ChildItem "C:\Users\chino\AppData\Local\Microsoft\WinGet\Packages" -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue | Select-Object -First 1
if ($ff) { $env:Path = $ff.DirectoryName + ";" + $env:Path }
Set-Location "C:\chinor\chinor-bot"
while ($true) {
    "$(Get-Date -Format s) bot start" | Out-File -Append -Encoding utf8 C:\chinor\logs\supervisor.log
    if ((Test-Path $console) -and (Get-Item $console).Length -gt 20MB) { Move-Item -Force $console "$console.1" }
    # cmd orqali: baytlar o'zgarishsiz (UTF-8), PowerShell'ning UTF-16/qator bo'lishi yo'q
    cmd /c "C:\chinor\.venv\Scripts\python.exe main.py >> $console 2>&1"
    "$(Get-Date -Format s) bot exit $LASTEXITCODE" | Out-File -Append -Encoding utf8 C:\chinor\logs\supervisor.log
    Start-Sleep -Seconds 10
}
