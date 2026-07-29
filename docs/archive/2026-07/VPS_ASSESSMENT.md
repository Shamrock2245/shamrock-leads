# VPS Sufficiency Assessment — ShamrockLeads Scraper Ecosystem

**Date:** 2026-07-20**Server:** Hetzner Cloud @ `5.161.126.32`

---

## Current Fleet Size

| State | Registered Scrapers | Actively Returning Data |
| --- | --- | --- |
| FL | 51 | 51 |
| GA | 74 | ~50 (EAS batch) |
| SC | 46 | 16 custom + 7 Zuercher |
| NC | 27 | ~20 |
| TN | 3 | 2 (Davidson, Knox) |
| TX | 3 | 2 (Bexar, Dallas) |
| LA | 2 | 0 (captcha/partial) |
| AL | 3 | 0 (WAF-blocked) |
| CT | 1 | 1 (441 records/run) |
| MS | 2 | 1 (Hinds, 700+ inmates) |
| **Total** | **212** | **~150 active** |

---

## Docker Resource Allocation (docker-compose.yml)

### Core Services (always running)

| Container | Memory Limit | CPU Limit | Purpose |
| --- | --- | --- | --- |
| shamrock-leads | **4 GB** | 2.0 | Scraper engine + Chromium |
| shamrock-dashboard | 2 GB | 1.5 | FastAPI dashboard + API |
| shamrock-osint-worker | 1 GB | 1.0 | Grok/NLP/Risk engines |
| shamrock-obscura | 512 MB | 1.0 | Stealth browser pool |
| shamrock-traccar | 512 MB | 0.5 | GPS tracking |
| **Core Total** | **8.0 GB** | **6.0** |  |

### Social Profile (optional, `--profile social`)

| Container | Memory Limit | CPU Limit |
| --- | --- | --- |
| shamrock-node-red | 512 MB | 0.5 |
| shamrock-social | 1 GB | 1.0 |
| shamrock-postiz | 2 GB | 1.5 |
| shamrock-temporal | 1 GB | 1.0 |
| shamrock-temporal-elasticsearch | 1 GB | 0.5 |
| shamrock-temporal-postgres | 512 MB | 0.5 |
| shamrock-postiz-postgres | 512 MB | 0.5 |
| shamrock-postiz-redis | 256 MB | 0.25 |
| **Social Total** | **6.8 GB** | **5.75** |

### Grand Total (all profiles)

|  | Memory | CPU |
| --- | --- | --- |
| Core only | 8.0 GB | 6.0 cores |
| Core + Social | **14.8 GB** | **11.75 cores** |

---

## Scraper Concurrency Model

- `SCRAPER_MAX_CONCURRENT = 8` (threadpool executor)

- Each Chromium instance: **150–300 MB RAM**

- Peak scraper RAM (8 concurrent headless): **~2 GB**

- Scraper container limit: **4 GB** → headroom: **~2 GB** for Python + queues

---

## VPS Sufficiency Verdict

### If you're on a CX31 (4 vCPU / 8 GB RAM / 80 GB disk):

| Metric | Required (Core) | Available | Status |
| --- | --- | --- | --- |
| RAM | 8.0 GB | 8 GB | **TIGHT** — no headroom for social profile |
| CPU | 6.0 cores | 4 vCPU | **OVER-SUBSCRIBED** — Docker limits are soft caps, but you'll see throttling |
| Disk | ~15 GB (images + data) | 80 GB | OK |

**Verdict: CX31 is insufficient.** You're likely already experiencing OOM kills or Chromium crashes under peak load.

### If you're on a CX41 (8 vCPU / 16 GB RAM / 160 GB disk):

| Metric | Required (Core) | Available | Status |
| --- | --- | --- | --- |
| RAM | 8.0 GB | 16 GB | **OK** — room for social profile too |
| CPU | 6.0 cores | 8 vCPU | **OK** — 2 cores headroom |
| Disk | ~15 GB | 160 GB | OK |

**Verdict: CX41 handles core + social comfortably.** This is the minimum recommended tier.

### Recommended: CX51 or CCX33 (8 vCPU / 32 GB RAM)

With 212 scrapers and growing, plus OSINT worker, Obscura browser pool, and the social stack, the **sweet spot** is:

| Tier | vCPU | RAM | Monthly | Why |
| --- | --- | --- | --- | --- |
| CX41 | 8 | 16 GB | ~€15 | Minimum viable for core + social |
| **CX51** | **16** | **32 GB** | ~€30 | **Recommended** — headroom for 300+ scrapers, APE proxy pool, ML scoring |
| CCX33 | 8 dedicated | 32 GB | ~€50 | If you need guaranteed CPU (no noisy neighbors) |

---

## Bottleneck Analysis

1. **RAM is the primary constraint** — Chromium instances are the biggest consumer. The Zuercher rewrite (pure HTTP, no browser) directly reduces RAM pressure for 7+ SC counties.

1. **CPU is secondary** — scrapers are I/O-bound (waiting on HTTP responses). The 8-concurrent threadpool rarely saturates CPU unless multiple Chromium instances are rendering simultaneously.

1. **Network is not a bottleneck** — Hetzner provides 20 TB egress/month. Scraper traffic is negligible.23

1. **Disk is fine** — MongoDB Atlas handles data storage externally. Local disk is only for Docker images and temp browser profiles.

---

## Recommendations

1. **Upgrade to CX51 (16 vCPU / 32 GB)** if you plan to run the full social stack alongside scrapers.

1. **Stay on CX41** if you keep social on a separate profile and only activate it occasionally.

1. **Reduce ****`SCRAPER_MAX_CONCURRENT`**** to 6** on CX31 to prevent OOM.

1. **The Zuercher rewrite eliminates Chromium for 7 SC counties** — this alone saves ~1.5 GB peak RAM.

1. **CT and Hinds MS scrapers use pure HTTP** — zero browser overhead.

1. **Once APE residential proxy is live**, the WAF-blocked counties (AL, Jackson MS, Greenville SC, Marlboro SC) can be unlocked without adding browser overhead.

