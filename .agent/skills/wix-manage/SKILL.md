---
name: wix-manage
description: "Wix business solution management via REST API — CRUD operations for stores, bookings, CMS, contacts, events, forms, media, pricing plans, payments, domains, and site properties. Use when managing Wix site data programmatically via API calls."
source: "https://github.com/wix/skills/tree/main/skills/wix-manage"
compatibility: Requires Wix REST API access (API key or OAuth).
---

# Wix Management Recipes

REST API operations for configuring and managing Wix business entities.

## Recipe Categories

### App Installation
- **Install Wix Apps** — Install apps using Apps Installer API
- **List Installed Apps** — Verify app installations, diagnose auth errors

### CMS (Content Management)
- **CMS Data Items CRUD** — Add, query, update, delete items in collections
- **CMS Data Operations Extended** — Count, upsert, update by filter
- **CMS References & Relationships** — Multi-reference field management
- **CMS Schema Management** — Create/modify collection structures

### Contacts
- **Bulk Delete Contacts** — Filter-based deletion with GDPR compliance
- **Bulk Label/Unlabel** — Add/remove labels with batch processing

### eCommerce
- **Apply Shipping Recommendations** — AI-generated shipping configuration
- **Setup Store Pickup** — Configure in-store pickup at checkout

### Media
- **Upload Media** — Import files to Wix Media Manager from URLs

### Payments
- **Create Payment Links** — Collect payments without checkout flow
- **Setup Wix Payments** — Configure payment provider
- **Payment Links for Bookings** — Link booking IDs to payment requests

### Sites
- **Create Site from Template** — Programmatic site creation
- **Query Sites** — List all sites with cursor-based pagination

## ShamrockLeads Integration Points

### CMS Collections (Active)
Our Wix portal uses these CMS collections that can be managed via REST API:
- `IntakeQueue` — Client intake submissions
- `Cases` — Active bond cases
- `PortalSessions` — Magic link sessions
- `MagicLinks` — Authentication tokens
- `PendingDocuments` — Unsigned paperwork
- `MemberDocuments` — Signed documents

### REST API Pattern
```python
import requests

headers = {
    "Authorization": "Bearer YOUR_API_KEY",
    "wix-site-id": "YOUR_SITE_ID",
    "Content-Type": "application/json"
}

# Query CMS collection
response = requests.post(
    "https://www.wixapis.com/wix-data/v2/items/query",
    headers=headers,
    json={
        "dataCollectionId": "IntakeQueue",
        "query": {
            "filter": {"status": {"$eq": "pending"}}
        }
    }
)
```

### Key Endpoints
| Operation | Endpoint |
|-----------|----------|
| Query items | `POST /wix-data/v2/items/query` |
| Insert item | `POST /wix-data/v2/items` |
| Update item | `PATCH /wix-data/v2/items/{id}` |
| Bulk insert | `POST /wix-data/v2/bulk/items/insert` |
| Upload media | `POST /site-media/v1/files/import` |

## Authentication

Two methods:
1. **API Key** — For server-to-server (dashboard → Wix CMS)
2. **OAuth** — For user-context operations

Always include `wix-site-id` header for site-specific operations.
