#!/bin/bash
# Admin SAYT versiyasini yig'adi: renderer/ + lib/api.js → $1 (standart: dist-web)
# Joylash: npx wrangler pages deploy dist-web --project-name=chinor-admin --branch=production
set -e
cd "$(dirname "$0")"
OUT="${1:-dist-web}"
rm -rf "$OUT"; mkdir -p "$OUT/lib"
cp renderer/* "$OUT/"
cp lib/api.js "$OUT/lib/"
echo "✓ $OUT tayyor"
