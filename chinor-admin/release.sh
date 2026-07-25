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

# ── macOS: ad-hoc QAYTA IMZOLASH + zipni qayta yasash ────────────────────
# Imzo muhri (_CodeSignature) yo'q/buzuq bo'lsa Apple Silicon'da macOS ilovani
# ishga tushishi bilanoq o'ldiradi (SIGKILL) — "ochilmayapti" shundan.
# Elektron build imzosiz chiqishi mumkin, shuning uchun o'zimiz imzolaymiz.
# Updater sha512 ni tekshirmaydi (faqat version+url o'qiydi), shuning uchun
# zipni qayta yasash latest-mac.yml ni buzmaydi.
MACAPP=$(find dist -maxdepth 2 -name "Chinor.app" -type d | head -1)
if [ -n "$MACAPP" ]; then
  echo "▶ macOS ilovasi imzolanmoqda: $MACAPP"
  find "$MACAPP/Contents/Frameworks" -maxdepth 1 \
       \( -name "*.framework" -o -name "*.app" -o -name "*.dylib" \) -print0 2>/dev/null |
    while IFS= read -r -d '' f; do
      codesign --force --sign - --timestamp=none "$f" >/dev/null 2>&1 || true
    done
  codesign --force --deep --sign - --timestamp=none "$MACAPP"
  codesign -v "$MACAPP" || { echo "❌ imzo tekshiruvi o'tmadi"; exit 1; }
  echo "▶ Imzolangan ilovadan .zip qayta yasalmoqda..."
  rm -f "dist/Chinor-mac-$VER.zip"
  ( cd "$(dirname "$MACAPP")" && ditto -c -k --sequesterRsrc --keepParent \
      "$(basename "$MACAPP")" "$OLDPWD/dist/Chinor-mac-$VER.zip" )
else
  echo "⚠ dist ichida Chinor.app topilmadi — imzolash o'tkazib yuborildi!"
fi

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
