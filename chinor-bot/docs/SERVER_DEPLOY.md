# Server deploy — VPS (Ubuntu 24.04)

Bot + Mini App API'ni doimiy yoniq VPS'da ishga tushirish. Telegram bot
**long-polling** ishlatadi — Telegram uchun ochiq port kerak emas; faqat Mini
App/sync API (`:8765`) Cloudflare Tunnel orqali `kassa.chinorpos.com` da ochiladi.

> Yangilanish feed'i (kassa/admin) VPS'da EMAS — Cloudflare R2'da
> (`updates.chinorpos.com`). Qarang [UPDATES_R2.md](UPDATES_R2.md). Demak VPS
> o'chsa ham desktop yangilanishlar ishlayveradi.

## 0) VPS yaratish

- **Spec:** 1 vCPU / **1 GB RAM** / Ubuntu **24.04** (bot uchun yetarli).
- **Provayder:** DigitalOcean ($6/oy, Google bilan kirish) yoki Hetzner CX22
  (~€4.5/oy). Region: Frankfurt/Amsterdam (Telegram cheklovsiz).
- Server ochilгач IP'ni oling. Mac'dan kalit qo'shing:
  ```bash
  ssh-keygen -t ed25519 -C chinor      # kalit bo'lmasa (Enter, Enter)
  ssh-copy-id root@<VPS-IP>            # yoki droplet yaratishda pubkey qo'shing
  ssh root@<VPS-IP>                    # kirib ko'ring
  ```

## 1) Tizim paketlari (VPS'da, root)

```bash
apt update && apt install -y python3-venv python3-pip git libzbar0 sqlite3
adduser --system --group --home /opt/chinor chinor
mkdir -p /var/lib/chinor && chown chinor:chinor /var/lib/chinor
```

## 2) Kodni olish

> Avvalo bu branch GitHub'ga push/merge qilingan bo'lsin (deploy/ fayllari
> repoda bo'lishi uchun). Keyin VPS'da:

```bash
cd /opt && git clone <repo-URL> chinor && chown -R chinor:chinor /opt/chinor
cd /opt/chinor
sudo -u chinor python3 -m venv .venv
sudo -u chinor .venv/bin/pip install -r chinor-bot/requirements.txt
```

## 3) Sozlamalar (.env)

```bash
sudo -u chinor cp chinor-bot/.env.example chinor-bot/.env
sudo -u chinor nano chinor-bot/.env
```
To'ldiring (eng muhimlari):
```
BOT_TOKEN=...
GLAVNIY_ADMIN_ID=...
CHANNEL_ID=...
DATA_DIR=/var/lib/chinor
MINI_APP_URL=https://fronted-bgq.pages.dev/?api=https://kassa.chinorpos.com
CORS_ALLOW_ORIGIN=https://fronted-bgq.pages.dev
API_HOST=127.0.0.1
API_PORT=8765
GEMINI_API_KEY=...        # ixtiyoriy
```
> `API_HOST=127.0.0.1` — server faqat tunnel orqali ochiladi, to'g'ridan-to'g'ri
> internetga emas (xavfsizroq).

## 4) Bot xizmati (systemd)

```bash
sudo cp deploy/chinor-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now chinor-bot
journalctl -u chinor-bot -f          # loglar (Ctrl+C chiqish)
```

## 5) Cloudflare Tunnel → kassa.chinorpos.com

```bash
# cloudflared o'rnatish
curl -L https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update && sudo apt install -y cloudflared

# tunnel (brauzer ochiladi — chinorpos.com zonasini tanlang)
cloudflared tunnel login
cloudflared tunnel create chinor-pos
cloudflared tunnel route dns chinor-pos kassa.chinorpos.com
```
`/etc/cloudflared/config.yml` ni yarating (namuna: `deploy/cloudflared-config.example.yml`):
```yaml
tunnel: chinor-pos
credentials-file: /etc/cloudflared/<UUID>.json   # create chiqargan UUID
ingress:
  - hostname: kassa.chinorpos.com
    service: http://localhost:8765
  - service: http_status:404
```
> `create` chiqargan `~/.cloudflared/<UUID>.json` ni `/etc/cloudflared/` ga ko'chiring.

Xizmat qilib o'rnatish:
```bash
sudo cloudflared service install
sudo systemctl enable --now cloudflared
curl -s https://kassa.chinorpos.com/api/health      # -> {"ok": true}
```

## 6) Kunlik backup (systemd timer)

```bash
sudo cp deploy/chinor-backup.service deploy/chinor-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now chinor-backup.timer
sudo systemctl start chinor-backup.service          # qo'lda sinash
ls -la /var/lib/chinor/backups
```

## 7) Mac'dan ulash (yakuniy)

```bash
./set-server-host.sh kassa.chinorpos.com    # API hostni .env+app.js ga muhrlaydi
./deploy-miniapp.sh                          # Mini App'ni Pages'ga deploy
```

## Keyin — kundalik boshqaruv (Mac'dan)

| Vazifa | Buyruq |
|---|---|
| Backend kodini yangilash | `./deploy-server.sh root@<VPS-IP>` (git pull + restart) |
| Mini App'ni yangilash | `./deploy-miniapp.sh` |
| Kassa/admin yangilanishi | `cd chinor-kassa && ./release.sh` · `cd chinor-admin && ./release.sh` |
| Loglarni ko'rish | `ssh root@<VPS-IP> journalctl -u chinor-bot -f` |
| Bazani tiklash | VPS: `/var/lib/chinor/backups/` dagi `.db.gz` ni oching |
