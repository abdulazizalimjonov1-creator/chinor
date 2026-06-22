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
