#!/bin/bash
# ─────────────────────────────────────────────────────────────────────
# Chinor Kassa — yangi versiya chiqarish (Windows + macOS yangilanishi)
#
# Ishlatish:
#   1) package.json dagi "version" ni oshiring (masalan 1.1.0 -> 1.1.1)
#   2) ./release.sh
#
# Skript Windows installer'i va macOS .zip ni yig'adi, yangilanish
# fayllarini ../chinor-bot/updates/ ga ko'chiradi. Bot serveri shu papkani ngrok
# orqali tarqatadi:
#   • Windows ilovalar (electron-updater) latest.yml ni tekshiradi
#   • macOS ilova (o'zimizning updater) latest-mac.yml ni tekshiradi
# Har ikkalasida ham "Yangilanish bor" tugmasi chiqadi.
# ─────────────────────────────────────────────────────────────────────
set -e
cd "$(dirname "$0")"

VER=$(node -p "require('./package.json').version")

echo "▶ Versiya: $VER — macOS build (.app + .zip) yig'ilmoqda..."
npx electron-builder --mac --publish never

# ── macOS: ad-hoc QAYTA IMZOLASH + zipni qayta yasash ────────────────────
# Imzo muhri (_CodeSignature) yo'q/buzuq bo'lsa Apple Silicon'da macOS ilovani
# ishga tushishi bilanoq o'ldiradi (SIGKILL) — "ochilmayapti" shundan.
# Elektron build imzosiz chiqishi mumkin, shuning uchun o'zimiz imzolaymiz.
# Updater sha512 ni tekshirmaydi (faqat version+url o'qiydi), shuning uchun
# zipni qayta yasash latest-mac.yml ni buzmaydi.
MACAPP=$(find dist -maxdepth 2 -name "Chinor Kassa.app" -type d | head -1)
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
  rm -f "dist/ChinorKassa-mac-$VER.zip"
  ( cd "$(dirname "$MACAPP")" && ditto -c -k --sequesterRsrc --keepParent \
      "$(basename "$MACAPP")" "$OLDPWD/dist/ChinorKassa-mac-$VER.zip" )
else
  echo "⚠ dist ichida «Chinor Kassa.app» topilmadi — imzolash o'tkazib yuborildi!"
fi

echo "▶ Versiya: $VER — Windows installer yig'ilmoqda..."
npx electron-builder --win --x64 --publish never

UP="../chinor-bot/updates"
mkdir -p "$UP"
echo "▶ Yangilanish fayllari ../chinor-bot/updates/ ga ko'chirilmoqda..."
# Windows
cp -f dist/latest.yml "$UP"/
cp -f dist/ChinorKassa-Setup-"$VER".exe "$UP"/
cp -f dist/ChinorKassa-Setup-"$VER".exe.blockmap "$UP"/
# macOS
cp -f dist/latest-mac.yml "$UP"/
cp -f dist/ChinorKassa-mac-"$VER".zip "$UP"/

echo ""
echo "✅ Tayyor! v$VER (Windows + macOS) chiqarildi. updates/ papkasi:"
ls -la "$UP"
echo ""
echo "Eslatma: bot va ngrok yoniq turishi kerak."
echo "Ilovalar 30 daqiqada bir tekshiradi (yoki qayta ochilganda)."
