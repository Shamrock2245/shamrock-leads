---
name: docker-ops
description: Container management and deployment operations for Hetzner VPS. Covers building, deploying, monitoring, and troubleshooting Docker containers.
---

# Docker Ops

> Manage the ShamrockLeads containers on Hetzner VPS.

## When to Use
- Deploying new code to Hetzner
- Debugging container issues
- User says "deploy", "restart", "check the server", "container is down"
- Scaling or upgrading services

---

## Architecture

```
Hetzner VPS
├── shamrock-leads      (Python 3.12, port 8088)
│   ├── APScheduler     (67 county scrapers)
│   ├── Lead Scorer
│   ├── MongoDB Writer
│   ├── Sheets Writer
│   └── Slack Notifier
│
└── shamrock-node-red   (Node-RED, port 1880)
    ├── Ops Dashboard
    ├── Cron Scheduler
    └── Slack Relay
```

---

## Standard Operations

### Deploy New Code
```bash
ssh shamrock-hetzner

cd /opt/shamrock-leads
git pull origin main
docker-compose build --no-cache
docker-compose up -d

# Verify
docker-compose ps
docker logs shamrock-leads --tail 20
```

### Restart Services
```bash
# Restart everything
docker-compose restart

# Restart just the scraper
docker-compose restart shamrock-leads

# Restart just Node-RED
docker-compose restart node-red
```

### View Logs
```bash
# Live logs
docker logs shamrock-leads -f

# Last 100 lines
docker logs shamrock-leads --tail 100

# Search for errors
docker logs shamrock-leads 2>&1 | grep "❌"

# Node-RED logs
docker logs shamrock-node-red --tail 50
```

### Check Health
```bash
# Container status
docker-compose ps

# Resource usage
docker stats --no-stream

# Disk usage
docker system df
```

---

## Emergency Procedures

### Container Won't Start
```bash
# Check logs for crash reason
docker logs shamrock-leads --tail 50

# Common causes:
# 1. Missing .env file → copy from .env.example
# 2. MongoDB URI wrong → check MONGODB_URI
# 3. Python import error → rebuild: docker-compose build --no-cache
```

### Out of Disk Space
```bash
# Clean up old images and containers
docker system prune -a

# Check what's using space
du -sh /var/lib/docker/*
```

### Container Using Too Much Memory
```bash
# Add memory limits in docker-compose.yml
deploy:
  resources:
    limits:
      memory: 512M
```

### Rollback
```bash
# If latest deploy broke something
git log --oneline -5  # Find the working commit
git checkout <commit>
docker-compose build && docker-compose up -d
```

---

## Environment Setup (First Deploy)

```bash
# 1. SSH into Hetzner
ssh root@<hetzner-ip>

# 2. Install Docker
curl -fsSL https://get.docker.com | sh

# 3. Clone repo
cd /opt
git clone https://github.com/Shamrock2245/shamrock-leads.git
cd shamrock-leads

# 4. Create .env from template
cp .env.example .env
nano .env  # Fill in real values

# 5. Create creds directory
mkdir -p creds
# Copy GCP service account key to creds/service-account-key.json

# 6. Build and start
docker-compose up -d --build

# 7. Verify
docker-compose ps
docker logs shamrock-leads --tail 50
```

---

## Monitoring

### Docker Health Checks
Both containers have built-in health checks:
- `shamrock-leads`: Python process alive (60s interval)
- `node-red`: HTTP GET to localhost:1880 (30s interval)

### External Monitoring
- Slack `#scraper-errors` channel for real-time failure alerts
- Node-RED dashboard for visual health overview
- Sentry (future) for error tracking

---

## Backup Procedures

### Node-RED Flows
```bash
# Backup flows
docker cp shamrock-node-red:/data/flows.json ./backups/flows_$(date +%Y%m%d).json

# Restore flows
docker cp ./backups/flows_YYYYMMDD.json shamrock-node-red:/data/flows.json
docker-compose restart node-red
```

### MongoDB Data
```bash
# MongoDB Atlas handles backups automatically
# Manual export if needed:
mongodump --uri="$MONGODB_URI" --out=./backups/mongo_$(date +%Y%m%d)
```
