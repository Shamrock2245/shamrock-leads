#!/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
# Shamrock Bail Bonds — Traccar Admin Account Setup
# Run this script on your VPS: bash setup_admin.sh
# ══════════════════════════════════════════════════════════════════════════════

TRACCAR_URL="http://localhost:8082"
ADMIN_EMAIL="admin@shamrockbailbonds.biz"
ADMIN_PASSWORD="Shamrock@Traccar2026!"
ADMIN_NAME="Shamrock Admin"

echo "🍀 Shamrock Traccar — Admin Account Setup"
echo "=========================================="

# 1. Verify server is running
echo ""
echo "▶ Step 1: Verifying Traccar server..."
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$TRACCAR_URL/api/server")
if [ "$STATUS" != "200" ]; then
  echo "❌ Traccar server not responding at $TRACCAR_URL (HTTP $STATUS)"
  echo "   Make sure Traccar is running: docker ps | grep traccar"
  exit 1
fi

NEW_SERVER=$(curl -s "$TRACCAR_URL/api/server" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('newServer', False))")
echo "   ✅ Server online — newServer=$NEW_SERVER"

# 2. Create admin user (only works when newServer=true, i.e., no users exist yet)
echo ""
echo "▶ Step 2: Creating admin user..."
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$TRACCAR_URL/api/users" \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"$ADMIN_NAME\",
    \"email\": \"$ADMIN_EMAIL\",
    \"password\": \"$ADMIN_PASSWORD\",
    \"administrator\": true
  }")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | head -1)

if [ "$HTTP_CODE" = "200" ]; then
  USER_ID=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id','?'))")
  echo "   ✅ Admin user created (ID: $USER_ID)"
elif [ "$HTTP_CODE" = "400" ]; then
  echo "   ⚠️  User may already exist (HTTP 400). Trying to verify login..."
else
  echo "   ❌ Failed to create user (HTTP $HTTP_CODE)"
  echo "   Response: $BODY"
  exit 1
fi

# 3. Verify login works
echo ""
echo "▶ Step 3: Verifying login credentials..."
SESSION=$(curl -s -w "\n%{http_code}" -c /tmp/traccar_cookies.txt \
  -X POST "$TRACCAR_URL/api/session" \
  -d "email=$ADMIN_EMAIL&password=$ADMIN_PASSWORD")

SESSION_CODE=$(echo "$SESSION" | tail -1)
SESSION_BODY=$(echo "$SESSION" | head -1)

if [ "$SESSION_CODE" = "200" ]; then
  echo "   ✅ Login successful!"
  SESSION_NAME=$(echo "$SESSION_BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('name','?'))")
  SESSION_ADMIN=$(echo "$SESSION_BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('administrator','?'))")
  echo "   Name: $SESSION_NAME | Administrator: $SESSION_ADMIN"
else
  echo "   ❌ Login failed (HTTP $SESSION_CODE)"
  echo "   Response: $SESSION_BODY"
  exit 1
fi

# 4. Generate API token for dashboard use
echo ""
echo "▶ Step 4: Generating API token for dashboard..."
TOKEN_RESPONSE=$(curl -s -b /tmp/traccar_cookies.txt \
  -X POST "$TRACCAR_URL/api/session/token" \
  -H "Content-Type: application/json" \
  -d "{\"expiration\": \"2030-01-01T00:00:00.000Z\"}")

if echo "$TOKEN_RESPONSE" | grep -q "token"; then
  API_TOKEN=$(echo "$TOKEN_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('token','?'))")
  echo "   ✅ API Token generated"
else
  # Fallback: token is returned as plain string
  API_TOKEN="$TOKEN_RESPONSE"
  echo "   ✅ API Token: $API_TOKEN"
fi

# 5. Summary
echo ""
echo "══════════════════════════════════════════════════════════════════"
echo "🍀 SHAMROCK TRACCAR — SETUP COMPLETE"
echo "══════════════════════════════════════════════════════════════════"
echo ""
echo "  Traccar Web UI:   http://YOUR_VPS_IP:8082"
echo "  Admin Email:      $ADMIN_EMAIL"
echo "  Admin Password:   $ADMIN_PASSWORD"
echo "  API Token:        $API_TOKEN"
echo ""
echo "  ⚠️  SAVE THESE CREDENTIALS — they will not be shown again."
echo "  ⚠️  Add the API token to your .env as: TRACCAR_TOKEN=$API_TOKEN"
echo ""
echo "  Next step: Open the Shamrock Bond Tracker dashboard and enter"
echo "  your VPS IP + API token in the Settings panel."
echo "══════════════════════════════════════════════════════════════════"

# Cleanup
rm -f /tmp/traccar_cookies.txt
