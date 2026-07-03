#!/bin/bash
# ─────────────────────────────────────────────────────────────────────
# Chinor (admin) — yangi versiya chiqarish (Windows + macOS yangilanishi)
#
# Ishlatish:
#   1) package.json dagi "version" ni oshiring (masalan 1.0.2 -> 1.0.3)
#   2) ./release.sh
#
# Windows (.exe + latest.yml) va macOS (.zip + latest-mac.yml) yig'iladi,
# fayllar ../chinor-bot/updates/admin/ ga ko'chiriladi va VPS'dagi bot
# papkasiga yuklanadi. Ilovalar feed'ni shu yerdan oladi:
#   https://kassa.chinorpos.com/updates/admin/
# (kassa feed'i /updates/ da — admin ALOHIDA /updates/admin/ prefiksida,
# shunda ikkala ilova yangilanishlari to'qnashmaydi.)
# ─────────────────────────────────────────────────────────────────────
set -e
cd "$(dirname "$0")"

VER=$(node -p "require('./package.json').version")

echo "▶ v$VER — macOS build (.app + .zip) yig'ilmoqda..."
npx electron-builder --mac --publish never

echo "▶ v$VER — Windows installer yig'ilmoqda..."
npx electron-builder --win --x64 --publish never

UP="../chinor-bot/updates/admin"
mkdir -p "$UP"
echo "▶ Yangilanish fayllari $UP ga ko'chirilmoqda..."
# Windows
cp -f dist/latest.yml "$UP"/
cp -f dist/Chinor-Setup-"$VER".exe "$UP"/
cp -f dist/Chinor-Setup-"$VER".exe.blockmap "$UP"/ 2>/dev/null || true
# macOS
cp -f dist/latest-mac.yml "$UP"/
cp -f dist/Chinor-mac-"$VER".zip "$UP"/

# VPS'dagi bot shu papkani tarqatadi — fayllarni serverga yuklaymiz.
HOST="${CHINOR_SSH:-root@46.101.111.198}"
KEY="${CHINOR_SSH_KEY:-$HOME/.ssh/chinor_vps}"
REMOTE="/opt/chinor/chinor-bot/updates/admin"
echo "▶ VPS'ga yuklanmoqda ($HOST:$REMOTE)..."
if rsync -avz -e "ssh -i $KEY -o ConnectTimeout=10" \
    "$UP"/latest.yml "$UP"/latest-mac.yml \
    "$UP"/Chinor-Setup-"$VER".exe "$UP"/Chinor-mac-"$VER".zip \
    "$HOST:$REMOTE/" 2>/dev/null; then
  echo "✅ VPS feed yangilandi."
else
  echo "⚠ VPS'ga yuklab bo'lmadi — keyin qo'lda yuklang:"
  echo "  rsync -avz -e 'ssh -i $KEY' $UP/ $HOST:$REMOTE/"
fi

echo ""
echo "✅ Tayyor! Chinor (admin) v$VER chiqarildi."
echo "   Ilovalar 30 daqiqada bir tekshiradi (yoki qayta ochilganda)."
