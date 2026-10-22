# Chinor server: bot va tunnel kompyuter yonganda o'zi ishga tushadigan qilish.
# Admin PowerShell'da ishga tushiring:  powershell -ExecutionPolicy Bypass -File C:\chinor\install-autostart.ps1
# Vazifalar SHU YERDA ISHGA TUSHIRILMAYDI — faqat yaratiladi (holati: Ready).
foreach ($n in "bot", "tunnel") {
    $a = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\chinor\run-$n.ps1"
    $tr = New-ScheduledTaskTrigger -AtStartup
    $tr.Delay = "PT30S"
    $p = New-ScheduledTaskPrincipal -UserId "S-1-5-18" -LogonType ServiceAccount -RunLevel Highest
    $s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName "Chinor-$n" -Action $a -Trigger $tr -Principal $p -Settings $s -Force | Out-Null
}
Get-ScheduledTask -TaskName "Chinor-*" | Select-Object TaskName, State
