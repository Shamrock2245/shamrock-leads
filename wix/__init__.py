"""
Wix Integration Module — ShamrockLeads
=======================================
Provides direct REST API access to:
  - Wix CMS (Data Items API v2) — read/write CMS collections
  - Wix CRM (Contacts API v4) — manage contacts and labels
  - Wix Blog (Blog API v3) — auto-publish posts (see blog/ module)

All modules share the WixClient base with unified auth, retry, and logging.

Environment Variables:
    WIX_BLOG_API_KEY  — Wix API Key (grants CMS + CRM + Blog permissions)
    WIX_SITE_ID       — Wix Site ID (defaults to Shamrock production)
"""
