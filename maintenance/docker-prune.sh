#!/bin/bash
# ============================================
# ShamrockLeads — Docker Auto-Prune
# Runs via cron on Hetzner VPS to reclaim disk
# 
# Cleans:
#   - Stopped containers (older than 24h)
#   - Dangling images (untagged build layers)
#   - Unused build cache
#   - Dangling volumes (NOT named volumes)
#
# Safe: Never touches running containers,
#        named volumes (node-red-data), or
#        images currently in use.
# ============================================

set -euo pipefail

LOG_PREFIX="[DOCKER-PRUNE]"
TIMESTAMP=$(date -u +"%Y-%m-%d %H:%M:%S UTC")

echo "${LOG_PREFIX} ${TIMESTAMP} — Starting auto-prune..."

# ── 1. Remove stopped containers older than 24h ─────────────────────────────
CONTAINERS_REMOVED=$(docker container prune -f --filter "until=24h" 2>&1)
echo "${LOG_PREFIX} Containers: ${CONTAINERS_REMOVED}"

# ── 2. Remove dangling images (untagged build layers) ────────────────────────
IMAGES_REMOVED=$(docker image prune -f 2>&1)
echo "${LOG_PREFIX} Images: ${IMAGES_REMOVED}"

# ── 3. Remove unused build cache ─────────────────────────────────────────────
CACHE_REMOVED=$(docker builder prune -f --filter "until=48h" 2>&1)
echo "${LOG_PREFIX} Build cache: ${CACHE_REMOVED}"

# ── 4. Remove dangling volumes (NOT named ones like node-red-data) ───────────
VOLUMES_REMOVED=$(docker volume prune -f 2>&1)
echo "${LOG_PREFIX} Volumes: ${VOLUMES_REMOVED}"

# ── 5. Report disk usage ─────────────────────────────────────────────────────
echo "${LOG_PREFIX} Current Docker disk usage:"
docker system df

echo "${LOG_PREFIX} ${TIMESTAMP} — Prune complete."
