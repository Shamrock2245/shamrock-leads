#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# BlueBubbles → Hetzner VPS URL Sync
# Runs on ShamrocksiMac at login via LaunchAgent
#
# Waits for BlueBubbles to start, reads the Cloudflare tunnel URL,
# and pushes it to the VPS dashboard so outreach works immediately.
# ═══════════════════════════════════════════════════════════════════

BB_LOCAL="http://localhost:1234"
BB_PASSWORD="2245Bail"
VPS_DASHBOARD="http://178.156.179.237:8088"
API_KEY="shamrock-bb-sync-2245"
SUFFIX="0178"

LOG="/tmp/bb-url-sync.log"

echo "$(date) — BlueBubbles URL sync starting..." >> "$LOG"

# Wait for BlueBubbles to become available (up to 5 minutes)
MAX_WAIT=300
WAITED=0
while [ $WAITED -lt $MAX_WAIT ]; do
    RESP=$(curl -s -o /dev/null -w "%{http_code}" "$BB_LOCAL/api/v1/ping" 2>/dev/null)
    if [ "$RESP" = "200" ]; then
        echo "$(date) — BlueBubbles is up (waited ${WAITED}s)" >> "$LOG"
        break
    fi
    sleep 5
    WAITED=$((WAITED + 5))
done

if [ $WAITED -ge $MAX_WAIT ]; then
    echo "$(date) — TIMEOUT: BlueBubbles didn't start in ${MAX_WAIT}s" >> "$LOG"
    exit 1
fi

# Get server info including the Cloudflare URL
SERVER_INFO=$(curl -s "$BB_LOCAL/api/v1/server/info?password=$BB_PASSWORD")
CF_URL=$(echo "$SERVER_INFO" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data.get('data', {}).get('proxy_service', ''))
except:
    print('')
" 2>/dev/null)

if [ -z "$CF_URL" ]; then
    echo "$(date) — ERROR: Could not extract Cloudflare URL from server info" >> "$LOG"
    echo "$(date) — Raw response: $SERVER_INFO" >> "$LOG"
    exit 1
fi

echo "$(date) — Cloudflare URL: $CF_URL" >> "$LOG"

# Push the new URL to the VPS dashboard
RESULT=$(curl -s -X POST "$VPS_DASHBOARD/api/config/bluebubbles-url" \
    -H "Content-Type: application/json" \
    -H "X-API-Key: $API_KEY" \
    -d "{\"suffix\": \"$SUFFIX\", \"url\": \"$CF_URL\"}")

echo "$(date) — VPS response: $RESULT" >> "$LOG"
echo "$(date) — ✅ Sync complete" >> "$LOG"
