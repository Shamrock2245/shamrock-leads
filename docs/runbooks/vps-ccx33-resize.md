# Hetzner CCX33 resize — resource limits + prune

> **Last updated:** 2026-08-13  
> **Host:** `root@178.156.179.237` (`shamrock-scraper-vps`)  
> **Tier:** Hetzner Cloud **CCX33** — 8 dedicated vCPU (4c/8t EPYC Milan) / **32 GB RAM**  
> **Disk:** in-place CPU/RAM resize does **not** grow the volume. Confirm size with `lsblk` / `df -h /`.

Use this after a Cloud Console resize, or after any change to `docker-compose.yml` memory/CPU caps.

---

## What the 32 GB box is for

The previous compose caps were sized for a 8–16 GB box:

| Service | Old cap | CCX33 cap | Why |
|---------|---------|-----------|-----|
| `shamrock-leads` | 4g / 2.0 cpu | **8g / 4.0 cpu** | 351 scrapers; Chromium + xvfb. Already 2+ GB / 400 PIDs mid-cycle on the old 4g cap. |
| `dashboard` | 2g / 1.5 | **3g / 2.0** | Super CRM + 61 API modules |
| `obscura` | 512m / 1.0 | **1g / 1.0** | Stealth CDP pool for CF counties |
| `osint-worker` | 1g / 1.0 | **2g / 1.5** | Maigret / Sherlock / SpiderFoot spikes |
| `postiz` | 2g / 1.5 | **3g / 1.5** | Nest + Temporal workers (~850 MB idle) |
| `traccar` | 512m / 0.5 | **768m / 0.5** | Java GPS; was ~46% of 512m |
| `opencut` | 2g / 1.5 | **1.5g / 1.0** | Idle ~150 MB — give budget back to scrapers |

Limits are **ceilings**, not reservations. Host (nginx, Docker, Warren, Tailscale) plus page cache should keep **~6 GB** unallocated. Do not raise every sidecar to fill 32 GB.

`SCRAPER_MAX_CONCURRENT` stays at **8** until one full 60-minute cycle is green (see below). Then try **10**, then **12**. Do not jump to 16 on 8 dedicated cores.

---

## Apply compose limits (no image rebuild)

Recreating only the services whose caps changed. **Do not** `docker compose up` Postiz unless you also run `scripts/repair_postiz_mastra.sh` — a recreate runs Prisma/Mastra and can 500 `/auth`.

```bash
ssh root@178.156.179.237
cd /opt/shamrock-leads
git pull origin main

# Live-update ceilings without a recreate (safe for Postiz / Temporal / DocuSeal)
docker update --memory 8g  --cpus 4.0 shamrock-leads
docker update --memory 3g  --cpus 2.0 shamrock-dashboard
docker update --memory 1g  --cpus 1.0 shamrock-obscura
docker update --memory 2g  --cpus 1.5 shamrock-osint-worker
docker update --memory 3g  --cpus 1.5 shamrock-postiz
docker update --memory 768m --cpus 0.5 shamrock-traccar
docker update --memory 1536m --cpus 1.0 shamrock-opencut

# Recreate only if tmpfs or other compose keys changed (scrapers need /tmp:size=2g)
COMPOSE_PROFILES=ops,social,alpr,edit,paperwork,tunnel \
  docker compose up -d --no-deps shamrock-leads dashboard obscura osint-worker
```

Verify:

```bash
docker stats --no-stream
free -h
uptime
```

---

## After the first full scraper cycle

A cycle is ~60 minutes (`SCRAPER_DEFAULT_INTERVAL_MINUTES`). The first post-reboot wave is staggered (`STAGGER_SECONDS=15` × 351 ≈ 88 minutes before every job has fired once).

```bash
# Host
uptime                          # load vs 8 cores — worry if 15-min avg > ~10
free -h                         # "available" should stay > 6 GB
swapon --show                   # swap used should stay 0

# Fleet
docker stats --no-stream --format \
  'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}'
docker inspect shamrock-leads --format '{{.HostConfig.Memory}} {{.HostConfig.NanoCpus}}'

# OOM / kills
dmesg -T | grep -iE 'oom|killed process' | tail
docker inspect shamrock-leads --format '{{.State.OOMKilled}} {{.State.Status}}'
```

**If** 15-minute load stays under ~6 and `shamrock-leads` stays under ~70% of 8 GB:

```bash
# on the VPS .env only — not committed
sed -i 's/^SCRAPER_MAX_CONCURRENT=.*/SCRAPER_MAX_CONCURRENT=10/' /opt/shamrock-leads/.env
docker compose restart shamrock-leads
```

**If** `available` drops under 4 GB or `shamrock-leads` hits its cap: drop concurrent back to 8 (or 6) before adding a second worker node.

---

## Grow the disk (do this — CPU/RAM resize did not)

CCX33 **new** servers ship with 240 GB. An in-place type change keeps the old volume. This host was still **38 GB / 75% full** on 2026-08-13.

1. Hetzner Cloud Console → server → **Resize** / **Volumes** → grow the root disk to **at least 160 GB** (240 GB matches the CCX33 SKU).
2. Then on the VPS (online, no reboot if the hypervisor already presented the new size):

```bash
lsblk                          # sda should show the new size
growpart /dev/sda 1            # apt-get install -y cloud-guest-utils if missing
resize2fs /dev/sda1
df -h /
```

Until that happens, keep the daily prune (`maintenance/docker-prune.sh` at 07:00 UTC) and do **not** `docker compose build --no-cache` without pruning first.

---

## Safe prune (what we clean vs what we keep)

**Safe to reclaim**

| Target | Why |
|--------|-----|
| `docker builder prune -af` | Unused BuildKit cache (often 4–6 GB) |
| `docker image prune -f` | Dangling layers only |
| `docker container prune -f --filter until=24h` | Stopped leftovers |
| `apt-get clean` + `journalctl --vacuum-size=50M` | Host clutter |
| `/opt/actions-runner/_diag` older than 14 days | Self-hosted runner logs |
| `/opt/actions-runner/bin.2.335.1` + `externals.2.335.1` | Superseded runner bits (live symlink is `2.336.0`) |
| Old kernel `linux-image-6.8.0-136-generic` after confirming `uname -r` is `6.8.0-137` | ~200–400 MB |
| Host `google-chrome-stable` | Unused — scrapers use in-container Playwright |

**Do not delete**

| Path / object | Why |
|---------------|-----|
| Named Docker volumes (`docuseal-*`, `postiz-*`, `temporal-*`, `traccar-*`, `node-red-data`) | Signing + social + GPS + flows |
| `/opt/shamrock-leads`, `/opt/warren` | Production |
| `/opt/shamrock-node-red` | Flow source (container now runs from this compose `ops` profile) |
| `/opt/swfl-arrest-scrapers`, `/opt/shamrock-bond-tracker` | Idle predecessor repos (~190 MB). Archive later if you want; not on the hot path. |
| Live actions-runner (`bin.2.336.0` + unit `…swfl-arrest-scrapers.shamrock-hetzner`) | Still enabled for the old repo |

Nuclear `docker system prune -a --volumes` is forbidden on this host.

---

## One-shot prune commands

```bash
# Docker (running containers + named volumes untouched)
docker builder prune -af
docker image prune -f
docker container prune -f --filter until=24h
docker system df

# Host
apt-get clean
journalctl --vacuum-size=50M
find /opt/actions-runner/_diag -type f -mtime +14 -delete
rm -rf /opt/actions-runner/bin.2.335.1 /opt/actions-runner/externals.2.335.1
rm -rf /home/runner/.cache/pip /home/runner/.cache/selenium

# Only if uname -r is already 6.8.0-137-generic
apt-get purge -y linux-image-6.8.0-136-generic \
  linux-modules-6.8.0-136-generic linux-modules-extra-6.8.0-136-generic || true
apt-get autoremove -y

# Only if lsof /opt/google/chrome/chrome is empty
apt-get purge -y google-chrome-stable && apt-get autoremove -y
```

Daily cron (already installed):

```
0 7 * * * /opt/shamrock-leads/maintenance/docker-prune.sh >> /var/log/docker-prune.log 2>&1
```
