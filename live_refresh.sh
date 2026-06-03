#!/usr/bin/env bash
# Tiempo real: regenera el dashboard con datos frescos y pushea (Vercel auto-deploya).
# Pensado para correr por cron cada ~15 min en el servidor cerebro.
# Corre LOCAL (no gasta minutos de GitHub Actions). Mantiene el dashboard rico tal cual.
set -uo pipefail
cd /root/serene-dashboard || exit 1

# token
[ -f .env ] && set -a && . ./.env && set +a
[ -z "${META_ACCESS_TOKEN:-}" ] && { echo "$(date '+%F %T') FALTA META_ACCESS_TOKEN"; exit 1; }

ACCOUNT="${1:-act_1020250386264513}"   # Prueba Gabriela (default del dashboard)
LOG="logs/live_refresh.log"
mkdir -p logs data/history
ts(){ date '+%F %T'; }

UNTIL=$(date -u +%Y-%m-%d)
SINCE=$(date -u -d "7 days ago" +%Y-%m-%d)

echo "$(ts) === refresh $ACCOUNT $SINCE→$UNTIL ===" >> "$LOG"

# 1) Extract (Meta + extras best-effort, igual que el workflow)
python3 extract.py "$ACCOUNT" --since "$SINCE" --until "$UNTIL" --out latest.json >> "$LOG" 2>&1 || { echo "$(ts) extract FALLÓ" >> "$LOG"; exit 1; }
[ -n "${SHOPIFY_CLIENT_ID:-}" ] && python3 extract_shopify.py --since "$SINCE" --until "$UNTIL" --out data/shopify_snapshot.json >> "$LOG" 2>&1 || true
python3 extract_activity.py --account-id "$ACCOUNT" --since "$SINCE" --until "$UNTIL" --out data/activity_snapshot.json >> "$LOG" 2>&1 || true
python3 extract_history.py --account-id "$ACCOUNT" --days-back 180 --enrich-top-n 50 --out data/history_campaigns.json >> "$LOG" 2>&1 || true

# 2) Analyze + render
python3 main.py latest.json >> "$LOG" 2>&1 || { echo "$(ts) render FALLÓ" >> "$LOG"; exit 1; }

# 3) Publicar a public/index.html (Vercel root)
NEW=$(ls -t output/dashboard_*.html 2>/dev/null | head -1)
[ -z "$NEW" ] && { echo "$(ts) no se generó HTML" >> "$LOG"; exit 1; }
cp "$NEW" public/index.html

# 4) Push (Vercel auto-deploya). Solo si cambió.
if ! git diff --quiet public/index.html 2>/dev/null; then
  git add public/index.html data/history/ 2>/dev/null
  git -c user.name="Serene Dashboard Bot" -c user.email="bot@sereneleparfum.team" \
      commit -q -m "auto: refresh tiempo real $(ts)" >> "$LOG" 2>&1
  git push -q origin main >> "$LOG" 2>&1 && echo "$(ts) ✓ push OK → Vercel deploy" >> "$LOG" || echo "$(ts) ✗ push FALLÓ" >> "$LOG"
else
  echo "$(ts) sin cambios, no push" >> "$LOG"
fi
