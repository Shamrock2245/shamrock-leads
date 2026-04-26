#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════════
# ShamrockLeads — One-Command Deploy
# ════════════════════════════════════════════════════════════════════════════════
# Usage: ./deploy.sh [commit message]
#
# This script:
#   1. Commits any staged/unstaged changes
#   2. Pushes to GitHub (triggers the GitHub Actions auto-deploy)
#   3. Optionally SSHes to Hetzner for an immediate deploy (no waiting for CI)
#
# If GitHub Actions is configured (deploy-hetzner.yml), pushing to main
# will automatically trigger the VPS rebuild. Use --direct flag to SSH
# directly and skip waiting for CI.
# ════════════════════════════════════════════════════════════════════════════════

set -e

HETZNER_HOST="178.156.179.237"
HETZNER_USER="root"
REPO_PATH="/opt/shamrock-leads"
DIRECT_DEPLOY=false

# Parse args
COMMIT_MSG="chore: deploy update $(date '+%Y-%m-%d %H:%M')"
for arg in "$@"; do
    if [ "$arg" = "--direct" ]; then
        DIRECT_DEPLOY=true
    else
        COMMIT_MSG="$arg"
    fi
done

echo "═══════════════════════════════════════════════════"
echo "  ShamrockLeads Deploy Pipeline"
echo "═══════════════════════════════════════════════════"

# ── Step 1: Stage & Commit ────────────────────────────────────────────────────
echo ""
echo "📦 Step 1: Git commit..."
cd "$(dirname "$0")"

if [ -n "$(git status --porcelain)" ]; then
    git add -A
    git commit -m "$COMMIT_MSG"
    echo "   ✅ Committed: $COMMIT_MSG"
else
    echo "   ℹ️  Nothing to commit — working tree clean"
fi

# ── Step 2: Push to GitHub ────────────────────────────────────────────────────
echo ""
echo "🚀 Step 2: Pushing to GitHub..."
git push origin main
echo "   ✅ Pushed to origin/main"

if [ "$DIRECT_DEPLOY" = true ]; then
    # ── Step 3: Direct SSH Deploy ─────────────────────────────────────────────
    echo ""
    echo "🖥️  Step 3: Direct deploy to Hetzner (--direct mode)..."
    ssh ${HETZNER_USER}@${HETZNER_HOST} << 'DEPLOYEOF'
        set -e
        cd /opt/shamrock-leads
        
        echo "📥 Pulling latest..."
        git pull origin main
        
        echo "🐳 Rebuilding..."
        docker compose build --no-cache shamrock-leads dashboard
        
        echo "🔄 Restarting..."
        docker compose up -d
        
        echo "⏳ Waiting 15s..."
        sleep 15
        
        echo "📊 Status:"
        docker compose ps
        
        echo "🏥 Health:"
        curl -sf http://localhost:8088/health && echo " ✅ Scraper OK" || echo " ⚠️ Scraper down"
        curl -sf http://localhost:5050/api/stats && echo " ✅ Dashboard OK" || echo " ⚠️ Dashboard down"
DEPLOYEOF
    echo ""
    echo "   ✅ Direct deploy complete!"
else
    echo ""
    echo "   ℹ️  GitHub Actions will auto-deploy to Hetzner."
    echo "   💡 Use --direct flag to SSH deploy immediately: ./deploy.sh --direct"
fi

echo ""
echo "═══════════════════════════════════════════════════"
echo "  ✅ Done!"
echo "═══════════════════════════════════════════════════"
