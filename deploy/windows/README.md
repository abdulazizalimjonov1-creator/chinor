# Windows prod server

Since 2026-10-22, production runs on the office Windows laptop.

- **Access:** from the Mac over Tailscale: `ssh chino@100.124.35.18`. Key-only login; the default shell is PowerShell.
- **Folder:** `C:\chinor`. It holds the code (copied over, not a git checkout), `.venv` (Python 3.12), `data\` (DATA_DIR), `secrets\` (the tunnel key and `config.yml`) and `logs\`.

| File | Purpose |
|---|---|
| `run-bot.ps1` | Runs `main.py` in a loop and restarts it 10 s after it exits. |
| `run-tunnel.ps1` | Runs the Cloudflare tunnel (`kassa.chinorpos.com` → 127.0.0.1:8765) in a loop. |
| `install-autostart.ps1` | Creates the `Chinor-bot` / `Chinor-tunnel` tasks (SYSTEM, at startup +30 s). Run it once from an admin PowerShell. |
| `power-setup.ps1` | Laptop power settings: lid → do nothing, no sleep, battery at 7% → hibernate. Run it from an admin PowerShell. |
| `cloudflared-config.example.yml` | Template for `C:\chinor\secrets\config.yml`. |
| `SETUP_PROMPT.md` | The prompt used to prepare the Windows machine with Claude. |

## Deploying a code change

```
scp <files> chino@100.124.35.18:C:/chinor/<path>
ssh chino@100.124.35.18 "Stop-ScheduledTask Chinor-bot; Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force; Start-ScheduledTask Chinor-bot"
```

## Rules

- **Never run the Mac and Windows servers at the same time.** Two bots polling at once makes Telegram return 409, and two copies of the Telethon session trigger `AUTH_KEY_DUPLICATED`.
- **To roll back to the Mac:**
  1. Stop both tasks.
  2. Copy `pos.db`, `marketplace.db` and `tg_userbot.session` back to the Mac.
  3. Move the plists from `~/Library/LaunchAgents/chinor-disabled/` back into `~/Library/LaunchAgents/`.
  4. `launchctl load` them.
- **Encoding:** `Set-Content -Encoding utf8` in PowerShell 5 adds a BOM, which breaks `.env` parsing. Write files with `[IO.File]::WriteAllText(..., New-Object Text.UTF8Encoding($false))` instead.
