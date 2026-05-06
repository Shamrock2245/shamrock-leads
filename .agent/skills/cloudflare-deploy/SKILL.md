---
name: cloudflare-deploy
description: "Deploy applications and infrastructure to Cloudflare using Workers, Pages, Tunnels, and related platform services. Use when deploying, hosting, publishing, or setting up projects on Cloudflare. Covers authentication, compute, storage, AI/ML, networking, security, and media products with detailed decision trees."
source: "https://github.com/openai/skills/tree/main/skills/.curated/cloudflare-deploy"
compatibility: Requires Cloudflare account and wrangler CLI.
---

# Cloudflare Deploy

Consolidated skill for deploying to the Cloudflare platform. Use decision trees to find the right product.

## Prerequisites

- Authenticate via `npx wrangler whoami` before deploying
- Interactive/local: `wrangler login` (one-time OAuth)
- CI/CD: Set `CLOUDFLARE_API_TOKEN` env var

## Quick Decision Trees

### Compute & Runtime
| Need | Product |
|------|---------|
| Serverless functions at the edge | Workers |
| Full-stack web app with Git deploys | Pages |
| Stateful coordination/real-time | Durable Objects |
| Long-running multi-step jobs | Workflows |
| Run containers | Containers |
| Scheduled tasks (cron) | Cron Triggers |

### Storage & Data
| Need | Product |
|------|---------|
| Key-value (config, sessions, cache) | KV |
| Relational SQL | D1 (SQLite) or Hyperdrive (Postgres/MySQL) |
| Object/file storage (S3-compatible) | R2 |
| Message queue (async processing) | Queues |
| Vector embeddings (AI/search) | Vectorize |

### AI & Machine Learning
| Need | Product |
|------|---------|
| Run inference (LLMs, embeddings, images) | Workers AI |
| Vector database for RAG/search | Vectorize |
| Build stateful AI agents | Agents SDK |
| Gateway for any AI provider | AI Gateway |

### Networking
| Need | Product |
|------|---------|
| Expose local service to internet | Tunnel |
| TCP/UDP proxy (non-HTTP) | Spectrum |
| WebRTC TURN server | TURN |

### Security
| Need | Product |
|------|---------|
| Web Application Firewall | WAF |
| DDoS protection | DDoS |
| Bot detection/management | Bot Management |
| CAPTCHA alternative | Turnstile |

## ShamrockLeads-Specific Usage

### BlueBubbles Tunnel (Primary)
```bash
# Named tunnel for permanent iMessage bridge
cloudflared tunnel create bluebubbles
cloudflared tunnel route dns bluebubbles bb.shamrockbailbonds.biz
cloudflared service install
```

### Potential Future Deployments
- **Edge Functions**: Replace GAS proxy with Workers
- **KV**: Cache lead scores at edge
- **Turnstile**: Bot protection for intake forms
- **R2**: Store signed PDF backups

## Troubleshooting

If deployment fails due to network issues, ensure escalated network permissions are available. Deployments require outbound access to Cloudflare's API.
