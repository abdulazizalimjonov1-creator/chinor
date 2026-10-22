# Chinor bot: yiqilsa 10 soniyadan keyin qayta ishga tushadi (Task Scheduler, SYSTEM).
$env:DATA_DIR = "C:\chinor\data"
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONIOENCODING = "utf-8"
$ff = Get-ChildItem "C:\Users\chino\AppData\Local\Microsoft\WinGet\Packages" -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue | Select-Object -First 1
if ($ff) { $env:Path = $ff.DirectoryName + ";" + $env:Path }
Set-Location "C:\chinor\chinor-bot"
while ($true) {
    "$(Get-Date -Format s) bot start" | Out-File -Append -Encoding utf8 C:\chinor\logs\supervisor.log
    & C:\chinor\.venv\Scripts\python.exe main.py *>> C:\chinor\logs\bot.log
    "$(Get-Date -Format s) bot exit $LASTEXITCODE" | Out-File -Append -Encoding utf8 C:\chinor\logs\supervisor.log
    Start-Sleep -Seconds 10
}
