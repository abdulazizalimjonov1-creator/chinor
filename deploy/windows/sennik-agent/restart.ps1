# Agentni qayta ishga tushiradi (sozlama yoki dastur yangilangandan keyin).
#
#   powershell -ExecutionPolicy Bypass -File restart.ps1
#
# Faqat `agent.ps1` jarayonlari tanlanadi: aks holda skriptning o'zi ham
# ro'yxatga tushib, o'zini to'xtatib qo'yadi.
schtasks /End /TN Chinor-sennik 2>$null | Out-Null
Start-Sleep -Seconds 3
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like '*agent.ps1*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2
schtasks /Run /TN Chinor-sennik | Out-Null
Start-Sleep -Seconds 6
Get-Content (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Definition) 'agent.log') -Tail 2
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like '*agent.ps1*' } |
    ForEach-Object { "agent ishlayapti, PID " + $_.ProcessId }
