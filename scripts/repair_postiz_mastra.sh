#!/usr/bin/env bash
# Repair Postiz when Mastra telemetry tables hit Postgres's 1600-column limit.
# Cause: postiz `prisma db push` + Mastra runtime ALTER TABLE on every container
# start leaves dropped-column slots on mastra_ai_spans (gitroomhq/postiz-app#1473).
# These tables are observability only — dropping them does not delete posts.
set -euo pipefail

POSTGRES_CTR="${POSTIZ_POSTGRES_CONTAINER:-shamrock-postiz-postgres}"
POSTIZ_CTR="${POSTIZ_CONTAINER:-shamrock-postiz}"

if ! docker ps --format '{{.Names}}' | grep -qx "$POSTGRES_CTR"; then
  echo "postiz postgres not running ($POSTGRES_CTR)"
  exit 0
fi

echo "Dropping bloated Mastra telemetry tables (if present)..."
docker exec "$POSTGRES_CTR" psql -U postiz -d postiz -v ON_ERROR_STOP=1 -c \
  "DROP TABLE IF EXISTS mastra_ai_spans CASCADE; DROP TABLE IF EXISTS mastra_scorers CASCADE;"

if docker ps --format '{{.Names}}' | grep -qx "$POSTIZ_CTR"; then
  echo "Restarting Postiz backend..."
  docker exec "$POSTIZ_CTR" pm2 restart backend >/dev/null
  for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
    if docker exec "$POSTIZ_CTR" sh -c "ss -lntp 2>/dev/null | grep -q ':3000'" \
      || docker exec "$POSTIZ_CTR" sh -c "netstat -lntp 2>/dev/null | grep -q ':3000'"; then
      echo "Postiz backend listening on :3000 (try $i)"
      exit 0
    fi
    sleep 5
  done
  echo "WARNING: backend did not bind :3000 within 60s"
  exit 1
fi
