#!/usr/bin/env bash
# Chinor POS — pos.db kunlik xavfsiz nusxasi (SQLite .backup — WAL-safe,
# ishlab turgan baza ustida ham xavfsiz, lock olmaydi).
# systemd timer (chinor-backup.timer) orqali har kuni ishlaydi.
# Eski nusxalar KEEP_DAYS kundan keyin o'chiriladi.
set -euo pipefail

DATA_DIR="${DATA_DIR:-/var/lib/chinor}"
DB="$DATA_DIR/pos.db"
DEST="$DATA_DIR/backups"
KEEP_DAYS="${KEEP_DAYS:-30}"

[ -f "$DB" ] || { echo "DB topilmadi: $DB — backup o'tkazib yuborildi"; exit 0; }
mkdir -p "$DEST"

STAMP="$(date +%Y%m%d-%H%M)"
OUT="$DEST/pos-$STAMP.db"
sqlite3 "$DB" ".backup '$OUT'"
gzip -f "$OUT"

# eski nusxalarni tozalash
find "$DEST" -name 'pos-*.db.gz' -mtime +"$KEEP_DAYS" -delete
echo "backup ok: $OUT.gz"
