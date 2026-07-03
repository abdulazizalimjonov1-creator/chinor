#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Desktop yangilanish fayllarini (latest.yml, *.exe, *.blockmap,
# latest-mac.yml, *.zip) Cloudflare R2 bucket'iga Mac'dan yuklaydi.
# Kassa va admin shu feed'dan avtomatik yangilanadi.
#
# Har bir ilova o'z prefiksiga yuklanadi:  R2 -> chinor-updates/kassa/...
#                                                          /admin/...
# (publish URL ham shunga mos: updates.<domen>/kassa/ , /admin/)
#
# Bir martalik tayyorgarlik:
#   npx wrangler login
#   npx wrangler r2 bucket create chinor-updates
#   # R2 dashboard -> bucket -> Settings -> Public access ->
#   #   "Connect custom domain"  ->  updates.SIZNING-DOMEN.uz
#
# Ishlatish (repo ildizidan; odatda release.sh o'zi chaqiradi):
#   ./deploy-updates.sh <kassa|admin> [dist-jild]
# dist-jild default: chinor-<app>/dist
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")"

APP="${1:?Ilova kerak:  ./deploy-updates.sh kassa   yoki   admin}"
SRC="${2:-chinor-$APP/dist}"
BUCKET="${BUCKET:-chinor-updates}"

[ -d "$SRC" ] || { echo "❌ Manba jild topilmadi: $SRC"; exit 1; }

shopt -s nullglob
files=("$SRC"/*.yml "$SRC"/*.exe "$SRC"/*.blockmap "$SRC"/*.zip)
[ ${#files[@]} -gt 0 ] || { echo "❌ $SRC ichida yangilanish fayllari yo'q (*.yml/*.exe/*.zip)."; exit 1; }

echo "▶ R2: $BUCKET/$APP/   <-   $SRC   (${#files[@]} ta fayl)"
for f in "${files[@]}"; do
  key="$APP/$(basename "$f")"
  echo "  ↑ $key"
  npx wrangler r2 object put "$BUCKET/$key" --file "$f" --remote >/dev/null
done

echo
echo "✅ Tayyor — '$APP' feed R2'da. Qurilmalar ~30 daqiqada (yoki qayta ochilganda) yangilanadi."
