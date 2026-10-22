# Cloudflare tunnel kassa.chinorpos.com -> 127.0.0.1:8765; yiqilsa qayta turadi.
while ($true) {
    "$(Get-Date -Format s) tunnel start" | Out-File -Append -Encoding utf8 C:\chinor\logs\supervisor.log
    & "C:\Program Files (x86)\cloudflared\cloudflared.exe" --config C:\chinor\secrets\config.yml tunnel run *>> C:\chinor\logs\tunnel.log
    "$(Get-Date -Format s) tunnel exit $LASTEXITCODE" | Out-File -Append -Encoding utf8 C:\chinor\logs\supervisor.log
    Start-Sleep -Seconds 10
}
