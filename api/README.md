# ShamrockLeads — Netlify API

This directory will contain the Netlify Edge Functions that expose
the MongoDB data as REST endpoints for:

1. **Node-RED** — Operations dashboard queries
2. **SPA Dashboard** — Licensee-facing arrest/lead views
3. **External integrations** — Webhook callbacks, health checks

## Planned Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/arrests` | Query arrests by county, date range |
| GET | `/api/leads` | Query scored leads by tenant |
| GET | `/api/stats` | Aggregated scraper stats |
| GET | `/api/health` | Scraper health status |
| POST | `/api/tenants` | Create/update tenant config |

## Tech

Built with Netlify Edge Functions (Deno-based) reading from MongoDB Atlas.
Deployed as a separate Netlify site: `shamrock-leads-api.netlify.app`
