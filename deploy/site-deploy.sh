#!/usr/bin/env bash
# Do'kon saytini yasaydi va Cloudflare Pages'ga chiqaradi.
#
#     bash deploy/site-deploy.sh
#
# Tovarga surat qo'yilgan sayin shu buyruqni qayta ishga tushiring —
# yangi tovarlarning sahifasi o'zi qo'shiladi va Google'ga ko'rinadi.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "▶ 1/2  Sahifalarni yasash"
python3 deploy/seo-build.py

echo "▶ 2/2  Cloudflare Pages"
# DIQQAT: bu loyihada jonli shox «production», «main» EMAS — noto'g'ri
# shoxga chiqarilsa, sayt o'zgarmaydi va sababini topish qiyin bo'ladi.
npx --yes wrangler pages deploy site \
    --project-name=chinor --branch="${BRANCH:-production}" --commit-dirty=true | tail -3

echo
echo "Tayyor. Google'ga yangi sahifalarni tezroq ko'rsatish uchun:"
echo "  https://search.google.com/search-console → Sitemaps → sitemap.xml"
