#!/usr/bin/env bash
# Kamera (ovoz + yaqinlashtirish) va tovar suratlari bo'limini prodga chiqaradi:
# bot fayllari → Windows server, Mini App → Cloudflare Pages, bot qayta ishga.
#
# Mac'dan ishga tushiriladi:
#     bash deploy/kamera-rasm-deploy.sh
#
# DIQQAT: `app/` ni SHU papkadan chiqaradi (ota papkadan emas) — aks holda
# jonli Mini App eski holatga qaytib qolishi mumkin.

set -euo pipefail

SRV="${SRV:-chino@100.124.35.18}"
API="${API:-https://kassa.chinorpos.com}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BOT_FILES="bot/api_cams.py bot/api_photos.py bot/web_api.py bot/api_recommend.py"

echo "▶ 1/4  Bot fayllari → $SRV"
# Ro'yxat TO'LIQ bo'lsin: yangi modul (api_photos.py) ko'chirilmay qolsa,
# uni chaqiradigan web_api.py prodda ImportError beradi.
# shellcheck disable=SC2086
scp -q $(for f in $BOT_FILES; do echo "chinor-bot/$f"; done) \
       "$SRV:C:/chinor/chinor-bot/bot/"

for f in $BOT_FILES; do
    want="$(shasum -a 256 "chinor-bot/$f" | cut -c1-16)"
    got="$(ssh "$SRV" "powershell -Command \"(Get-FileHash C:\\chinor\\chinor-bot\\$(echo $f | tr / \\\\) -Algorithm SHA256).Hash.ToLower().Substring(0,16)\"" | tr -d '\r\n ')"
    if [ "$want" != "$got" ]; then
        echo "   ✗ MOS EMAS: $f (mahalliy $want, serverda $got)" >&2
        exit 1
    fi
done
echo "   ✓ ko'chirildi va tekshirildi"

echo "▶ 2/4  Mini App → Cloudflare Pages"
npx --yes wrangler pages deploy "$ROOT/app" \
    --project-name=fronted --branch=production --commit-dirty=true | tail -2

echo "▶ 3/4  Botni qayta ishga tushirish"
ssh "$SRV" 'powershell -Command "Stop-ScheduledTask -TaskName Chinor-bot; Start-Sleep 3; Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force; Start-Sleep 2; Start-ScheduledTask -TaskName Chinor-bot; Start-Sleep 14"' >/dev/null

echo "▶ 4/4  Tekshiruv"
printf '   suratlar: %s  ·  rasmsizlar: %s  ·  kamera ovozi: %s  ·  sayt katalogi: %s\n' \
  "$(curl -s -o /dev/null -w '%{http_code}' "$API/api/photos/summary")" \
  "$(curl -s -o /dev/null -w '%{http_code}' "$API/api/products/no-photo")" \
  "$(curl -s -o /dev/null -w '%{http_code}' "$API/api/cams/1/audio")" \
  "$(curl -s -o /dev/null -w '%{http_code}' "$API/api/shop/catalog?limit=1")"
echo "   (dastlabki uchtasi 401, oxirgisi 200 bo'lsa — hammasi joyida)"
echo
echo "Qolgani telefonda:"
echo "   • 📹 Kameralar → kamerani ochib 🔊 ni bosing, ikki barmoq bilan yaqinlashtiring"
echo "   • 🖼 Rasmsiz tovarlar → «Umumiy rasmlarni tozalash» ni bir marta bosing"
