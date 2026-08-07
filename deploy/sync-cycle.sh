#!/bin/sh
# YesPOS → Chinor sinxronizatsiyasining BIR AYLANISHI (cron har 20 daqiqada).
#
#   1. catalog — YANGI TOVARLAR va kategoriyalar (eng muhimi: ilgari bu
#      kuniga bir marta, 18:30 da ishlardi va yangi tovar bir kun kechikardi)
#   2. stock   — narx va qoldiq surati
#   3. bridge  — o'sha surat pos.db ga ko'chiriladi (bot shundan o'qiydi)
#
# Tartib muhim: katalogsiz yangi tovar qoldiq suratiga tushmaydi, suratsiz
# esa ko'prikda ko'chiradigan narsa bo'lmaydi.
#
# `flock` — oldingi aylanish tugamagan bo'lsa yangisi ishga tushmaydi
# (qoldiq surati ~1 daqiqa oladi; sekin tarmoqda cho'zilishi mumkin).

set -u
ROOT=/opt/chinor
PY=$ROOT/.venv/bin/python
LOG=/var/lib/chinor/marketplace-sync.log
LOCK=/tmp/chinor-sync.lock

cd "$ROOT" || exit 1

exec 9>"$LOCK"
if ! flock -n 9; then
    echo "$(date -Is) [sync] oldingi aylanish hali tugamadi — o'tkazib yuborildi" >> "$LOG"
    exit 0
fi

for step in "sync catalog" "sync stock"; do
    # shellcheck disable=SC2086
    $PY -m marketplace.$step >> "$LOG" 2>&1 \
        || echo "$(date -Is) [sync] XATO: $step" >> "$LOG"
done
$PY -m marketplace.pos_bridge --apply >> "$LOG" 2>&1 \
    || echo "$(date -Is) [sync] XATO: pos_bridge" >> "$LOG"

# Log cheksiz o'smasin — 20 daqiqalik jadvalda kuniga ~70 aylanish bo'ladi.
if [ "$(wc -c < "$LOG")" -gt 5000000 ]; then
    tail -c 2000000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
