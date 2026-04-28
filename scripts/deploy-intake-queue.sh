#!/usr/bin/env bash
# ============================================================================
# ShamrockLeads — Intake Queue Full Deployment + Verification
# Run this from your local Mac terminal (NOT in the IDE sandbox)
# ============================================================================
set -euo pipefail

VPS="root@178.156.179.237"
DASHBOARD_URL="http://178.156.179.237:8088"

echo "═══════════════════════════════════════════════════"
echo "  Phase 1: Push to GitHub + Deploy to Hetzner"
echo "═══════════════════════════════════════════════════"

# 1a. Push latest code to GitHub
echo "📤 Pushing to GitHub..."
cd ~/Desktop/shamrock-active-software/shamrock-leads
git push origin main

# 1b. Deploy to VPS
echo "🚀 Deploying to Hetzner VPS..."
ssh $VPS "cd /opt/shamrock-leads && \
  git stash 2>/dev/null; \
  git pull origin main && \
  docker compose build --no-cache dashboard && \
  docker compose up -d dashboard && \
  echo '✅ Dashboard container rebuilt and restarted'"

# Wait for dashboard to come up
echo "⏳ Waiting 10s for dashboard to start..."
sleep 10

echo ""
echo "═══════════════════════════════════════════════════"
echo "  Phase 2: Verify VPS Environment"
echo "═══════════════════════════════════════════════════"

# 2a. Check env vars on VPS
echo "🔍 Checking VPS .env for required vars..."
ssh $VPS "echo '--- ENV STATUS ---' && \
  grep -E '^(GAS_WEB_APP_URL|WIX_WEBHOOK_SECRET|GAS_API_KEY|MONGODB_URI|MONGODB_DB_NAME)' /opt/shamrock-leads/.env 2>/dev/null | sed 's/=.*/=✅ SET/' || echo '⚠️  .env not found or empty'"

# 2b. Check container health
echo ""
echo "🐳 Container status:"
ssh $VPS "docker compose -f /opt/shamrock-leads/docker-compose.yml ps --format 'table {{.Name}}\t{{.Status}}'"

echo ""
echo "═══════════════════════════════════════════════════"
echo "  Phase 3: Endpoint Verification"
echo "═══════════════════════════════════════════════════"

# 3a. Health check
echo "🏥 Health check..."
curl -sf "$DASHBOARD_URL/health" | python3 -m json.tool 2>/dev/null || echo "⚠️  Health check failed"

# 3b. Intake stats
echo ""
echo "📊 Intake stats..."
curl -sf "$DASHBOARD_URL/api/intake/stats" | python3 -m json.tool 2>/dev/null || echo "⚠️  Intake stats failed"

# 3c. Intake queue
echo ""
echo "📋 Intake queue (first 3)..."
curl -sf "$DASHBOARD_URL/api/intake/queue?status=all&limit=3" | python3 -m json.tool 2>/dev/null || echo "⚠️  Intake queue failed"

# 3d. Submit a test intake
echo ""
echo "📥 Submitting test intake..."
TEST_RESULT=$(curl -sf -X POST "$DASHBOARD_URL/api/intake/submit" \
  -H "Content-Type: application/json" \
  -d '{
    "source": "manual_entry",
    "firstName": "Test",
    "lastName": "Intake",
    "phone": "2395550000",
    "email": "test@shamrock.test",
    "relationship": "Self",
    "defendantName": "John Doe Test",
    "defendantCounty": "Lee",
    "defendantBookingNumber": "TEST-2026-DEPLOY",
    "defendantBondAmount": "5000",
    "defendantCharges": "Battery"
  }' 2>/dev/null)
echo "$TEST_RESULT" | python3 -m json.tool 2>/dev/null || echo "⚠️  Test intake submission failed"

# Extract intake_id from result for cleanup
INTAKE_ID=$(echo "$TEST_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('intake_id',''))" 2>/dev/null)

# 3e. Archive test intake (cleanup)
if [ -n "$INTAKE_ID" ]; then
  echo ""
  echo "🗑️  Archiving test intake ($INTAKE_ID)..."
  curl -sf -X POST "$DASHBOARD_URL/api/intake/$INTAKE_ID/archive" | python3 -m json.tool 2>/dev/null || echo "⚠️  Archive failed"
fi

echo ""
echo "═══════════════════════════════════════════════════"
echo "  Phase 4: Dashboard UI"
echo "═══════════════════════════════════════════════════"
echo "🌐 Dashboard: $DASHBOARD_URL"
echo "📥 Intake Queue tab: Click '📥 Intake Queue' in the tab bar"
echo ""

echo "═══════════════════════════════════════════════════"
echo "  ✅ Deployment Complete!"
echo "═══════════════════════════════════════════════════"
echo ""
echo "Next steps (manual):"
echo "  1. Open $DASHBOARD_URL in browser"
echo "  2. Click '📥 Intake Queue' tab"
echo "  3. Try '+ Manual Entry' to add a real intake"
echo "  4. Process it → Write Bond flow"
echo ""
echo "Wix webhook URL (for Velo config):"
echo "  POST $DASHBOARD_URL/api/webhooks/wix-intake"
echo "  Header: X-API-Key: <WIX_WEBHOOK_SECRET value>"
echo ""
echo "Telegram Mini App endpoint:"
echo "  POST $DASHBOARD_URL/api/intake/submit"
echo "  Body: { source: 'telegram_mini_app', ... }"
