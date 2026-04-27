# Deploy to Hetzner VPS

> **Trigger phrases:** "deploy", "push to production", "deploy to hetzner", "git sync deploy"

## Overview

Deploys the `shamrock-leads` codebase from local → GitHub → Hetzner VPS Docker stack.

**VPS:** `178.156.179.237` (root)  
**Repo:** `/opt/shamrock-leads`  
**Services:** `shamrock-leads` (scraper), `dashboard` (Flask API on 8088), `node-red` (ops)

---

## Workflow Steps

### 1. Stage & Commit Locally

```bash
cd ~/Desktop/shamrock-active-software/shamrock-leads
git add -A
git status --short
git commit -m "feat: <describe changes>"
```

### 2. Pull Remote (Merge Others' Work)

```bash
git pull origin main
```

> ⚠️ If conflicts arise, resolve them before proceeding.

### 3. Push to GitHub

**⚡ Agent cannot push due to macOS sandbox — user must run:**

```bash
git push origin main
```

### 4. Pull on VPS

```bash
# SSH into Hetzner
ssh root@178.156.179.237

cd /opt/shamrock-leads
git fetch origin
git reset --hard origin/main
git log --oneline -3  # verify correct commit
```

### 5. Rebuild Docker Containers

**Dashboard only (fast — no Chromium deps):**
```bash
docker compose build --no-cache dashboard
docker compose up -d dashboard
```

**Scraper engine (slow — includes Chromium, xvfb, patchright):**
```bash
docker compose build --no-cache shamrock-leads
docker compose up -d shamrock-leads
```

**Both services:**
```bash
docker compose build --no-cache
docker compose up -d
```

> 💡 Dashboard uses `dashboard/Dockerfile` (lightweight, ~200MB).  
> Scraper uses root `Dockerfile` (full Chromium stack, ~1.2GB).

### 6. Verify Health

```bash
docker compose ps                          # All containers healthy?
docker logs shamrock-dashboard --tail 10    # Dashboard API working?
docker logs shamrock-leads --tail 20        # Scrapers running?
curl -s http://localhost:5050/health        # Dashboard health endpoint
```

### 7. Disk Space Management

If Docker build fails with "not enough free space":

```bash
docker system prune -af --volumes    # Remove all unused images/volumes
df -h                                # Check disk space
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `git pull` says "Already up to date" but commit is wrong | `git fetch origin && git reset --hard origin/main` |
| Dashboard won't start | Check `docker logs shamrock-dashboard --tail 30` for import errors |
| Scraper missing `xauth` | Ensure `xvfb` AND `xauth` are in root `Dockerfile` apt-get |
| Charlotte scraper fails "xauth not found" | Rebuild scraper container: `docker compose build --no-cache shamrock-leads` |
| Port 8088 not responding | `docker compose ps` — dashboard should show `0.0.0.0:8088->5050/tcp` |
| SSH session expires mid-deploy | Reconnect and resume from step 4 |
| Agent can't `git push` | macOS sandbox restriction — user must run `git push origin main` manually |

---

## Service Architecture

```
docker-compose.yml
├── shamrock-leads     (build: ./Dockerfile)      → Scraper + APScheduler
├── dashboard          (build: ./dashboard/Dockerfile) → Flask API on :5050 (external :8088)
└── node-red           (profile: ops)             → Ops dashboard on :1880
```

## Key Ports

| Service | Internal | External (VPS) |
|---------|----------|----------------|
| Dashboard | 5050 | 8088 |
| Node-RED | 1880 | 1880 |
| Scraper | N/A | N/A (no exposed port) |
