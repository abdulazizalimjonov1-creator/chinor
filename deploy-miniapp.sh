#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Telegram Mini App'ni Mac'dan bitta buyruq bilan yangilash.
#
# Mini App = app/ ichidagi statik fayllar (index.html, app.js, style.css,
# logo.png). Ular Cloudflare Pages'da turadi. Bu skript ularni Pages'ga
# yuklaydi — bir necha soniyada butun dunyo CDN'ida jonli bo'ladi.
#
# Bir martalik tayyorgarlik:
#   npx wrangler login          # brauzer ochiladi, Cloudflare hisobiga kirasiz
#
# Har safar yangilash (repo ildizidan):
#   ./deploy-miniapp.sh
#
# Pages loyiha nomi boshqacha bo'lsa yoki production branch boshqa nomda bo'lsa:
#   ./deploy-miniapp.sh <loyiha-nomi> <production-branch>
# Loyihalar ro'yxatini ko'rish:  npx wrangler pages project list
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")"

# Pages loyiha nomi — "xxx.pages.dev" dagi xxx. Agar boshqacha bo'lsa o'zgartiring.
PROJECT="${1:-fronted-bgq}"
# Pages "production" branch — SHU nomga deploy qilinsa jonli URL yangilanadi.
# (Skript qaysi git branch'da turishidan qat'i nazar — preview'ga emas, jonliga.)
BRANCH="${2:-main}"
DIR="app"

if [ ! -f "$DIR/index.html" ]; then
  echo "❌ $DIR/index.html topilmadi — skriptni repo ildizidan ishga tushiring."
  exit 1
fi

echo "▶ Mini App ($DIR/)  ->  Cloudflare Pages: '$PROJECT'  (branch: $BRANCH)"
echo
npx wrangler pages deploy "$DIR" \
  --project-name="$PROJECT" \
  --branch="$BRANCH" \
  --commit-dirty=true

cat <<'NEXT'

✅ Tayyor — Mini App Pages'ga yuklandi (bir necha soniyada jonli bo'ladi).
   Foydalanuvchi Telegram'da Mini App'ni qayta ochsa yangi versiyani ko'radi.

Eslatma:
  • Server manzili (Mini App qaysi API'ga ulanishi) app/app.js dagi
    _DEFAULT_API va MINI_APP_URL'dagi ?api=... orqali keladi. Domen o'zgarsa
    AVVAL  ./set-server-host.sh <domen>  ni ishga tushiring, KEYIN bu skriptni.
  • Telegram WebApp eski versiyani keshlashi mumkin — ko'rinmasa Mini App'ni
    butunlay yopib qayta oching.
NEXT
