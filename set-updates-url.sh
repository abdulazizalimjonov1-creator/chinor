#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Yangilanish feed HOST'ini (Cloudflare R2 public domeni) kassa va admin
# package.json -> build.publish[0].url da almashtiradi.
# /kassa/ va /admin/ yo'l qismi saqlanadi.
#
# Bu HOST build vaqtida ilovaga MUHRLANADI — o'zgartirgach ilovalarni
# qayta yig'ish shart (release.sh).
#
# Ishlatish:
#   ./set-updates-url.sh <yangi-host> [eski-host]
#   misol:  ./set-updates-url.sh updates.chinor.uz
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

NEW="${1:?Host kiriting, masalan: ./set-updates-url.sh updates.chinor.uz}"
OLD="${2:-updates.SIZNING-DOMEN.uz}"
ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "Almashtirish (updates host):  $OLD  ->  $NEW"
echo

changed=0
for f in "$ROOT/chinor-kassa/package.json" "$ROOT/chinor-admin/package.json"; do
  if [ -f "$f" ] && grep -q "$OLD" "$f"; then
    sed -i '' "s|$OLD|$NEW|g" "$f"
    echo "  ✅ yangilandi:  ${f#$ROOT/}"
    changed=$((changed+1))
  else
    echo "  ⏭️  topilmadi:    ${f#$ROOT/}"
  fi
done

echo
[ "$changed" -gt 0 ] || { echo "Hech nima o'zgarmadi. Eski host to'g'rimi?"; exit 1; }

cat <<'NEXT'
Keyingi qadam — ilovalarni qayta yig'ib R2 feed'iga yuklang:
  cd chinor-kassa && npm install && ./release.sh
  cd chinor-admin && npm install && ./release.sh
NEXT
