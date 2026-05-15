# DEPLOYMENT.md — ShamrockLeads Production Operations

> **Last Updated:** 2026-05-15
> **Production VPS:** `178.156.179.237` (Hetzner, root access)
> **Public URL:** `https://leads.shamrockbailbonds.biz`
> **Code path (VPS):** `/opt/shamrock-leads`

---

## Infrastructure Overview

| Component | Host | URL/Port | Purpose |
|-----------|------|----------|---------|
| **Dashboard** | Hetzner VPS | `:8088` external → `:5050` internal | Intelligence dashboard |
| **Scrapers** | Hetzner VPS | — (background service) | 51 county scrapers + APScheduler |
| **Traccar** | Hetzner VPS | `:8082` | GPS tracking server |
| **Node-RED** | Hetzner VPS | `:1880` (profile: ops) | Operations dashboard |
| **Nginx** | Hetzner VPS | `:443` → `:8088` | SSL reverse proxy |
| **MongoDB** | Atlas Cloud | `shamrock.1mgkm.mongodb.net` | Primary database (M0 free tier) |
| **iMessage** | Office iMac | ngrok → `:1234` | BlueBubbles bridge |

---

## Deployment Methods

### Method 1: GitHub Actions (Automatic)

Push to `main` triggers `.github/workflows/deploy.yml`:

```yaml
# Automatic flow:
1. Push to main
2. GitHub Action SSHs to VPS
3. git pull origin main
4. docker compose build --no-cache
5. docker compose up -d
```

### Method 2: Manual SSH Deploy

```bash
# Full stack rebuild
ssh root@178.156.179.237 "cd /opt/shamrock-leads && git pull origin main && docker compose build --no-cache && docker compose up -d"

# Dashboard only (faster — most common)
ssh root@178.156.179.237 "cd /opt/shamrock-leads && git pull origin main && docker compose build --no-cache dashboard && docker compose up -d dashboard"

# Scraper only
ssh root@178.156.179.237 "cd /opt/shamrock-leads && git pull origin main && docker compose build --no-cache shamrock-leads && docker compose up -d shamrock-leads"
```

### Method 3: File Deploy Script

For targeted file updates without full rebuild:

```bash
# Deploy specific files via deploy_files.py
python deploy_files.py --files dashboard/sl-core.js dashboard/styles.css
```

---

## Docker Operations

### Service Management

```bash
# Check all services
docker compose ps

# Start all services
docker compose up -d

# Start with ops profile (includes Node-RED)
docker compose --profile ops up -d

# Stop all
docker compose down

# Restart specific service
docker compose restart dashboard

# Rebuild without cache
docker compose build --no-cache dashboard
docker compose up -d dashboard
```

### Logs

```bash
# Follow dashboard logs
docker logs -f shamrock-dashboard --tail 50

# Follow scraper logs
docker logs -f shamrock-leads --tail 50

# Follow traccar logs
docker logs -f shamrock-traccar --tail 50

# Check all containers
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

### Shell Access

```bash
# Dashboard container
docker exec -it shamrock-dashboard /bin/bash

# Scraper container
docker exec -it shamrock-leads /bin/bash

# Run single scraper
docker exec shamrock-leads python main.py lee
```

### Cleanup

```bash
# Remove dangling images
docker image prune -f

# Remove all stopped containers
docker container prune -f

# Nuclear cleanup (WARNING: removes volumes too)
docker system prune -a --volumes
```

---

## Nginx Configuration

Nginx reverse proxy at `/opt/shamrock-leads/nginx/`:

```nginx
# leads.shamrockbailbonds.biz → localhost:8088 (dashboard)
server {
    listen 443 ssl;
    server_name leads.shamrockbailbonds.biz;

    ssl_certificate /etc/letsencrypt/live/leads.shamrockbailbonds.biz/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/leads.shamrockbailbonds.biz/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8088;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### SSL Certificate Renewal

```bash
# Auto-renew via certbot
certbot renew --nginx

# Manual renewal
certbot certonly --nginx -d leads.shamrockbailbonds.biz
```

---

## Health Checks

### Dashboard Health

```bash
# HTTP health check
curl -s http://178.156.179.237:8088/health | python -m json.tool

# Expected response:
{
    "status": "ok",
    "mongo": "connected",
    "uptime": "..."
}
```

### Scraper Health

```bash
# Via dashboard API
curl -s http://178.156.179.237:8088/api/scraper/status | python -m json.tool

# Docker health
docker inspect --format='{{.State.Health.Status}}' shamrock-leads
docker inspect --format='{{.State.Health.Status}}' shamrock-dashboard
```

### MongoDB Status

```bash
# Via dashboard
curl -s http://178.156.179.237:8088/api/mongo-stats | python -m json.tool
```

---

## Monitoring & Alerts

| Channel | Source | Alert Type |
|---------|--------|-----------|
| `#new-arrests-{county}` | Scraper pipeline | New arrest ingested |
| `#leads` | LeadScorer | Hot lead (score ≥70) |
| `#scraper-errors` | BaseScraper | Scraper failures, auto-disable events |
| `#shamrock` | Various | General ops alerts |
| Docker healthcheck | Docker | Container restart on failure |
| APScheduler | Scraper engine | Missed job detection |

---

## Troubleshooting

### Dashboard won't start

```bash
# Check logs
docker logs shamrock-dashboard --tail 100

# Common fixes:
# 1. MongoDB connection timeout → check MONGODB_URI in .env
# 2. Port conflict → check nothing else on 8088
# 3. Missing env vars → compare .env with .env.example
```

### Scraper failures

```bash
# Check specific county
docker exec shamrock-leads python main.py lee --dry-run

# Force re-enable disabled scraper
curl -X POST http://178.156.179.237:8088/api/scraper/enable/lee
```

### iMessage not connecting

```bash
# Check BlueBubbles health
curl -s http://178.156.179.237:8088/api/bb/health | python -m json.tool

# Verify ngrok tunnel is running on office iMac
# Check BLUEBUBBLES_URL_0178 in .env matches current tunnel
```

### MongoDB M0 storage full (512MB)

```bash
# Run data retention purge
curl -X POST http://178.156.179.237:8088/api/retention/purge

# Check storage stats
curl -s http://178.156.179.237:8088/api/retention/stats | python -m json.tool
```

---

## Environment Setup (New VPS)

```bash
# 1. Install Docker
curl -fsSL https://get.docker.com | sh

# 2. Clone repo
cd /opt
git clone git@github.com:Shamrock2245/shamrock-leads.git
cd shamrock-leads

# 3. Configure environment
cp .env.example .env
nano .env  # Fill in all required variables

# 4. Add credentials
mkdir -p creds config
# Copy GCP service account JSON to creds/
# Copy Firebase admin SDK to config/

# 5. Build and start
docker compose up -d --build

# 6. Setup Nginx + SSL
apt install nginx certbot python3-certbot-nginx
cp nginx/shamrock-leads.conf /etc/nginx/sites-available/
ln -s /etc/nginx/sites-available/shamrock-leads.conf /etc/nginx/sites-enabled/
certbot --nginx -d leads.shamrockbailbonds.biz
systemctl restart nginx

# 7. Verify
curl -s http://localhost:8088/health
```

---

## Backup & Recovery

### MongoDB
MongoDB Atlas handles automated backups on the cluster level. For manual backup:
```bash
mongodump --uri="$MONGODB_URI" --db=ShamrockBailDB --out=./backup/$(date +%Y%m%d)
```

### Code
All code is in GitHub (`Shamrock2245/shamrock-leads`). VPS pulls from `main`.

### Docker Volumes
```bash
# Backup dashboard uploads
docker cp shamrock-dashboard:/app/dashboard/uploads ./backup/uploads/

# Backup Traccar data
docker cp shamrock-traccar:/opt/traccar/data ./backup/traccar/
```

---

*Maintained by: Brendan / Shamrock Active Software LLC | `admin@shamrockbailbonds.biz`*
