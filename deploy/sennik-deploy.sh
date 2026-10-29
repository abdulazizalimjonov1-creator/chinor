#!/usr/bin/env bash
# Sennik bo'limini prodga chiqaradi: bot fayllari → Windows server,
# Mini App → Cloudflare Pages, so'ng bot qayta ishga tushadi.
#
# Mac'dan ishga tushiriladi:
#     bash deploy/sennik-deploy.sh
#
# DIQQAT: `app/` ni SHU papkadan chiqaradi (ota papkadan emas) — aks holda
# jonli Mini App eski holatga qaytib qolishi mumkin.

set -euo pipefail

SRV="${SRV:-chino@100.124.35.18}"
API="${API:-https://kassa.chinorpos.com}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "▶ 1/4  Bot fayllari → $SRV"
# DIQQAT: ro'yxat to'liq bo'lsin. 2026-10-29 da `barcode_gen.py` shu yerga
# qo'shilmay qolgani uchun prodda ImportError chiqqan edi (yangi modulni
# chaqiradigan fayllar ko'chirilgan, modulning o'zi esa yo'q).
scp -q chinor-bot/bot/print_agent.py \
       chinor-bot/bot/api_pricetag.py \
       chinor-bot/bot/barcode_gen.py \
       chinor-bot/bot/web_api.py \
       "$SRV:C:/chinor/chinor-bot/bot/"
scp -q chinor-bot/database/_products.py "$SRV:C:/chinor/chinor-bot/database/"
scp -q app/index.html app/app.js app/style.css "$SRV:C:/chinor/app/"

# Ko'chirilganini TEKSHIRAMIZ: fayl yetib bormasa yoki eskisi qolsa, xato
# faqat tugma bosilganda, foydalanuvchi oldida chiqadi.
for f in bot/print_agent.py bot/api_pricetag.py bot/barcode_gen.py \
         bot/web_api.py database/_products.py; do
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
printf '   sizes: %s  ·  logo: %s\n' \
  "$(curl -s -o /dev/null -w '%{http_code}' "$API/api/admin/pricetag/sizes")" \
  "$(curl -s -o /dev/null -w '%{http_code}' "$API/sennik/logo.png")"
echo "   (401 va 200 bo'lsa — hammasi joyida)"

echo "▶ 4/4  Agent kaliti"
# Kalitni bot birinchi so'rovda yaratadi — shuning uchun avval bir marta
# «noto'g'ri kalit» bilan murojaat qilamiz, keyin fayldan o'qiymiz.
curl -s -o /dev/null -X POST -H 'Content-Type: application/json' \
     -H 'X-Print-Token: x' -d '{"key":"x"}' "$API/api/print-agent/poll" || true
TOKEN="$(ssh "$SRV" 'powershell -Command "Get-Content C:\chinor\data\print_agent_token.txt"' | tr -d '\r\n ')"
echo "   KALIT: $TOKEN"
echo
echo "Endi printer ulangan har bir kompyuterda (bir marta):"
echo "   1) deploy/windows/sennik-agent/ papkasini C:\\ChinorSennik\\ ga ko'chiring"
echo "   2) o'sha papkada PowerShell ochib:"
echo "      .\\install.ps1 -Server $API -Token $TOKEN -Key qabul -Name \"Qabul kompyuteri\""
echo "      (server kompyuterida: -Key server -Name \"Server kompyuter\")"
