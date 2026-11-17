#!/usr/bin/env bash
# Kunlik sayt yangilash (launchd: com.chinor.site-deploy).
# Documents ichidagi repoga macOS fon vazifani kiritmaydi (TCC), shuning uchun
# alohida nusxa ~/.chinor-site-build ishlatiladi: har safar main'ning oxirgi
# holatiga keltiriladi — u yerda qo'lda hech narsa o'zgartirmang.
set -euo pipefail
DIR="$HOME/.chinor-site-build"
cd "$DIR"
echo "=== $(date '+%F %T')"
git fetch -q origin main
git reset -q --hard origin/main
bash deploy/site-deploy.sh
