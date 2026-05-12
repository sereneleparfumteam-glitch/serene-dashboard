#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# Serene AI · Full pipeline runner (cron-ready)
#
# extract → analyze → render → upload Drive → notify
#
# Uso:
#   ./run_full.sh                                 # default: prod, last 7d
#   ./run_full.sh act_935968735451363             # otra cuenta
#   ./run_full.sh act_1020250386264513 30         # últimos 30 días
#   SKIP_UPLOAD=1 ./run_full.sh                   # solo render local
#   SKIP_NOTIFY=1 ./run_full.sh                   # sin notificación
#
# Variables de entorno requeridas:
#   META_ACCESS_TOKEN   Meta Graph API token (con ads_read)
#
# Variables opcionales:
#   DRIVE_FOLDER_ID         Folder ID destino (default: root del remote serene)
#
#   # Notificación (cualquiera funciona, primero match wins):
#   TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
#   CALLMEBOT_PHONE + CALLMEBOT_APIKEY
#   SMTP_HOST + SMTP_USER + SMTP_PASS + NOTIFY_TO  (+ SMTP_PORT opcional, def 465)
#   NOTIFY_WEBHOOK_URL
#   WHATSAPP_NUMBER  (fallback manual con wa.me link impreso)
# ─────────────────────────────────────────────────────────────

set -euo pipefail

# ──── Args ────
ACCOUNT_ID="${1:-act_1020250386264513}"
DAYS_BACK="${2:-7}"
SKIP_UPLOAD="${SKIP_UPLOAD:-0}"
SKIP_NOTIFY="${SKIP_NOTIFY:-0}"
[[ "${3:-}" == "--skip-upload" ]] && SKIP_UPLOAD=1
[[ "${3:-}" == "--skip-notify" ]] && SKIP_NOTIFY=1

# ──── Paths ────
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"
mkdir -p logs

# ──── Auto-load .env if exists ────
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

# ──── Validate env ────
if [[ -z "${META_ACCESS_TOKEN:-}" ]]; then
  echo "❌ META_ACCESS_TOKEN no está configurado." >&2
  echo "   Exporta: export META_ACCESS_TOKEN='EAA...'" >&2
  echo "   O agrégalo a /root/serene-dashboard/.env" >&2
  exit 1
fi

# ──── Date range ────
UNTIL="$(date -u +%Y-%m-%d)"
SINCE="$(date -u -d "$DAYS_BACK days ago" +%Y-%m-%d)"

LOG_FILE="logs/run_$(date +%Y%m%d_%H%M%S).log"
START_TS=$(date +%s)

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log "Serene AI · Full Pipeline"
log "  Account: $ACCOUNT_ID"
log "  Range:   $SINCE → $UNTIL ($DAYS_BACK days)"
log "  Skip:    upload=$SKIP_UPLOAD notify=$SKIP_NOTIFY"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ──── 1. Extract ────
SLUG="${ACCOUNT_ID#act_}"
SNAPSHOT_FILE="${SLUG}_${SINCE}_to_${UNTIL}.json"

log ""
log "[1/4] Extract Meta"
if ! python3 extract.py "$ACCOUNT_ID" --since "$SINCE" --until "$UNTIL" --out "$SNAPSHOT_FILE" 2>&1 | tee -a "$LOG_FILE"; then
  log "❌ Extract Meta failed"
  exit 1
fi

# Extract Shopify (optional — skips silently if creds missing)
if [[ -n "${SHOPIFY_CLIENT_ID:-}" && -n "${SHOPIFY_CLIENT_SECRET:-}" ]]; then
  log "[1.5/4] Extract Shopify"
  if ! python3 extract_shopify.py --since "$SINCE" --until "$UNTIL" --out data/shopify_snapshot.json 2>&1 | tee -a "$LOG_FILE"; then
    log "⚠ Extract Shopify failed (non-fatal — continúa sin data Shopify)"
  fi
else
  log "[1.5/4] Skip Shopify (no SHOPIFY_CLIENT_ID/SECRET in env)"
fi

# Extract Activity Feed (Meta + Shopify + Google)
log "[1.7/4] Extract Activity Feed"
if ! python3 extract_activity.py --account-id "$ACCOUNT_ID" --since "$SINCE" --until "$UNTIL" --out data/activity_snapshot.json 2>&1 | tee -a "$LOG_FILE"; then
  log "⚠ Extract Activity failed (non-fatal)"
fi

# Archive snapshot to history/ (foundation for Pack E forecasting + anomaly detection)
mkdir -p data/history
TODAY=$(date -u +%Y-%m-%d)
cp "data/$SNAPSHOT_FILE" "data/history/meta_${TODAY}.json" 2>/dev/null && log "  → archived meta snapshot to history/meta_${TODAY}.json"
[[ -f data/shopify_snapshot.json ]] && cp data/shopify_snapshot.json "data/history/shopify_${TODAY}.json" && log "  → archived shopify snapshot to history/shopify_${TODAY}.json"
[[ -f data/activity_snapshot.json ]] && cp data/activity_snapshot.json "data/history/activity_${TODAY}.json" && log "  → archived activity snapshot to history/activity_${TODAY}.json"

# Keep last 90 days of history (rotation)
find data/history -type f -mtime +90 -delete 2>/dev/null || true

# ──── 2. Analyze + Render + Upload ────
log ""
log "[2/4] Analyze + Render"
HUMAN_NAME="Serene AI Dashboard ${SLUG} - $(date +%Y-%m-%d).html"

if [[ "$SKIP_UPLOAD" == "1" ]]; then
  python3 main.py "$SNAPSHOT_FILE" 2>&1 | tee -a "$LOG_FILE"
  DRIVE_LINK=""
else
  log "[3/4] Upload to Drive"
  python3 main.py "$SNAPSHOT_FILE" --upload --name "$HUMAN_NAME" 2>&1 | tee -a "$LOG_FILE"
  DRIVE_LINK="https://drive.google.com/drive/search?q=${HUMAN_NAME// /%20}"
fi

# ──── 3. Notify ────
if [[ "$SKIP_NOTIFY" != "1" ]]; then
  log ""
  log "[4/4] Notify"
  if ! python3 notify.py --snapshot "$SNAPSHOT_FILE" ${DRIVE_LINK:+--drive-link "$DRIVE_LINK"} 2>&1 | tee -a "$LOG_FILE"; then
    log "⚠ Notify step failed (non-fatal)"
  fi
fi

# ──── Done ────
END_TS=$(date +%s)
ELAPSED=$((END_TS - START_TS))

log ""
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log "✓ Done in ${ELAPSED}s"
log "  Snapshot: data/$SNAPSHOT_FILE"
log "  HTML:     output/dashboard_${SLUG}_$(date +%Y-%m-%d)*.html"
[[ -n "$DRIVE_LINK" ]] && log "  Drive:    $HUMAN_NAME"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
