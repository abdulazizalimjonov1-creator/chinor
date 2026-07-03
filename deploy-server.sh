#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Mac'dan VPS'dagi botni yangilash: kodni tortib (git pull) qayta ishga
# tushiradi. "Backend'ni Mac'dan boshqarish" — bitta buyruq.
#
# Bir martalik tayyorgarlik:
#   • VPS'da repo /opt/chinor da bo'lsin (SERVER_DEPLOY.md ga qarang)
#   • Mac'dan SSH kalit bilan kira olish:  ssh-copy-id root@<VPS-IP>
#
# Ishlatish:
#   ./deploy-server.sh root@<VPS-IP>
#   yoki:  CHINOR_SSH=root@1.2.3.4 ./deploy-server.sh
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

HOST="${1:-${CHINOR_SSH:-}}"
[ -n "$HOST" ] || { echo "❌ VPS SSH host kerak:  ./deploy-server.sh root@<VPS-IP>"; exit 1; }
REMOTE_DIR="${REMOTE_DIR:-/opt/chinor}"

echo "▶ $HOST : git pull + restart  ($REMOTE_DIR)"
ssh "$HOST" "cd '$REMOTE_DIR' && git pull --ff-only && sudo systemctl restart chinor-bot && sleep 2 && systemctl --no-pager --lines=8 status chinor-bot"

echo ""
echo "✅ Bot yangilandi. Tekshirish:  curl -s https://kassa.chinorpos.com/api/health"
