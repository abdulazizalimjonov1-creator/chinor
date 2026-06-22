#!/usr/bin/env bash
# Server hostini hamma kerakli joyda almashtiradi (ngrok -> Cloudflare yoki boshqa).
#
# Ishlatish:
#   ./set-server-host.sh <yangi-host> [eski-host]
#   misol:  ./set-server-host.sh kassa.chinor.uz
#
# Eski host ko'rsatilmasa, ngrok hosti deb olinadi.
# Faqat HOST almashadi — https:// va /updates/ kabi yo'llar saqlanadi.
set -euo pipefail

NEW="${1:?Yangi host kiriting, masalan: ./set-server-host.sh kassa.chinor.uz}"
OLD="${2:-unnatural-vibes-praying.ngrok-free.dev}"
ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "Almashtirish:  $OLD  ->  $NEW"
echo

changed=0
for f in "$ROOT/chinor-bot/.env" "$ROOT/chinor-kassa/package.json" "$ROOT/app/app.js"; do
  if [ -f "$f" ] && grep -q "$OLD" "$f"; then
    sed -i '' "s|$OLD|$NEW|g" "$f"
    echo "  ✅ yangilandi:  ${f#$ROOT/}"
    changed=$((changed+1))
  else
    echo "  ⏭️  topilmadi:    ${f#$ROOT/}"
  fi
done

echo
if [ "$changed" -eq 0 ]; then
  echo "Hech nima o'zgarmadi. Eski host to'g'rimi? Tekshiring:"
  echo "  grep -rn '$OLD' chinor-bot/.env chinor-kassa/package.json app/app.js"
  exit 1
fi

cat <<'NEXT'
Keyingi qadamlar:
  1) Bot'ni qayta ishga tushiring:
       pkill -f 'python.*main.py'; cd chinor-bot && python main.py
  2) Mini App'ni Cloudflare Pages'ga qayta deploy qiling (app/).
  3) Windows installer'ni qayta yig'ing va chinor-bot/updates/ ga joylang:
       cd chinor-kassa && npm run dist   # so'ng dist/*.exe + latest.yml -> chinor-bot/updates/
       (yoki chinor-kassa/release.sh)
NEXT
