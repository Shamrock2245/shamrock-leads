# Deploy to Hetzner Workflow

// turbo-all

> Safe deployment of ShamrockLeads containers to Hetzner VPS.

## Prerequisites
- SSH access to Hetzner VPS
- Code committed and pushed to GitHub

---

## Steps

### 1. Check Current Status
```bash
ssh shamrock-hetzner "cd /opt/shamrock-leads && docker-compose ps"
```

### 2. Pull Latest Code
```bash
ssh shamrock-hetzner "cd /opt/shamrock-leads && git pull origin main"
```

### 3. Build Containers
```bash
ssh shamrock-hetzner "cd /opt/shamrock-leads && docker-compose build --no-cache"
```

### 4. Deploy
```bash
ssh shamrock-hetzner "cd /opt/shamrock-leads && docker-compose up -d"
```

### 5. Verify Health
```bash
ssh shamrock-hetzner "cd /opt/shamrock-leads && docker-compose ps && docker logs shamrock-leads --tail 20"
```

### 6. Monitor
Watch for first scraper cycle to complete. Check Slack channels for alerts.
