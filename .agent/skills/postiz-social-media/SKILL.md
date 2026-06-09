---
name: postiz-social-media
description: Social media automation via Postiz. Covers CLI, API, MCP, video uploads, auto-hashtags, and Shamrock branding. Use for any social posting, scheduling, or analytics task.
---

# Postiz Social Media Skill

> Automate Shamrock Bail Bonds social media across 5 connected platforms via Postiz.

## Connected Platforms

| Platform | Integration ID | Profile |
|----------|---------------|---------|
| Facebook | `cmpzsi3ir0003k36vw5hrex7x` | ShamrockBail |
| Instagram | `cmpzsilta0005k36vzt951r2y` | shamrock_bail_bonds |
| X (Twitter) | `cmpztrc580001k36p5kz86taa` | ShamrockBail_FL |
| YouTube | `cmpzshji10001k36v6o88d0n7` | Shamrock Bail |
| Google My Business | `cmpzsktm50007k36vw1cxyrzb` | Shamrock Bail Bonds |

## Authentication

```bash
# Environment variables (already set in VPS .bashrc)
export POSTIZ_API_KEY="cf7a289051fb18ecd18d4aac78e204779c0e833606ba28127e8d4a94354e0524"
export POSTIZ_API_URL="http://localhost:5200/api"

# Verify auth
postiz auth:status
```

**API Key** (for Python `httpx` calls): Use `Authorization: <key>` header (NO `Bearer` prefix).
**Base URL** (for Python): `https://social.shamrockbailbonds.biz/api/public/v1/` (external) or `http://localhost:5200/api/public/v1/` (internal on VPS).

## CLI Reference (Installed on VPS: v2.0.15)

### Upload Media (REQUIRED before posting with media)

> **HARD RULE:** Every file must go through `postiz upload` first. Raw paths and URLs are rejected.

```bash
# Upload video/image
RESULT=$(postiz upload /path/to/video.mp4)
URL=$(echo "$RESULT" | jq -r '.path')

# Then use $URL in the post
postiz posts:create -c "Content" -m "$URL" -s "2026-06-15T14:00:00Z" -i "integration-id"
```

Supported media types: `.mp4`, `.mov`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`

### Create Posts

```bash
# Simple scheduled post
postiz posts:create -c "Content here" -s "2026-06-15T14:00:00.000Z" -i "cmpztrc580001k36p5kz86taa"

# Draft
postiz posts:create -c "Content" -s "2026-06-15T14:00:00Z" -t draft -i "integration-id"

# Multi-platform (comma-separated IDs)
postiz posts:create -c "Content" -s "2026-06-15T14:00:00Z" \
  -i "cmpztrc580001k36p5kz86taa,cmpzsilta0005k36vzt951r2y,cmpzsi3ir0003k36vw5hrex7x"

# With uploaded media
postiz posts:create -c "Content" -m "$UPLOADED_URL" -s "2026-06-15T14:00:00Z" -i "integration-id"

# From JSON file
postiz posts:create --json post.json
```

### List & Manage Posts

```bash
# List recent posts
postiz posts:list

# List in date range
postiz posts:list --startDate "2026-06-01T00:00:00Z" --endDate "2026-07-01T00:00:00Z"

# Delete
postiz posts:delete <post-id>

# Change status
postiz posts:status <post-id> --status draft
postiz posts:status <post-id> --status schedule
```

### Analytics

```bash
# Platform-level (last 30 days)
postiz analytics:platform <integration-id> -d 30

# Post-level
postiz analytics:post <post-id> -d 7

# If missing releaseId (common with TikTok):
postiz posts:missing <post-id>
postiz posts:connect <post-id> --release-id "<content-id>"
```

### Integration Discovery

```bash
postiz integrations:list
postiz integrations:settings <integration-id>
postiz integrations:trigger <integration-id> <method-name>
```

## MCP Server (For AI Agents)

Postiz exposes 9 MCP tools. For self-hosted, the endpoint is:

```
URL: https://social.shamrockbailbonds.biz/api/mcp/cf7a289051fb18ecd18d4aac78e204779c0e833606ba28127e8d4a94354e0524
Transport: Streamable HTTP
```

Available tools: `integrationList`, `groupList`, `integrationSchema`, `triggerTool`, `schedulePostTool`, `generateImageTool`, `generateVideoOptions`, `videoFunctionTool`, `generateVideoTool`

## Python Client (Existing)

The async Python client lives at `social/postiz_client.py` — use `PostizPublicClient` for server-side automation:

```python
from social.postiz_client import get_public_postiz_client

client = get_public_postiz_client()
integrations = await client.list_integrations()
media_id = await client.upload_media("/path/to/video.mp4")
result = await client.create_post(
    post_type="schedule",
    platform_posts=[...],
    schedule_date=datetime(2026, 6, 15, 14, 0),
)
```

## Shamrock Posting Script (CLI wrapper)

For one-shot posting from the command line, use `social/shamrock_poster.py`:

```bash
# Post with video to all platforms
python social/shamrock_poster.py --video ~/Downloads/reel.mp4 --caption "Know your rights!"

# Schedule for later
python social/shamrock_poster.py --video reel.mp4 --caption "Tips" --schedule "2026-06-15 10:00"

# Dry run
python social/shamrock_poster.py --caption "Test" --dry-run
```

## Shamrock Branding Rules (Non-Negotiable)

### Logo (Auto-Attached)
- **URL:** `https://social.shamrockbailbonds.biz/uploads/2026/06/09/3e34676c610c1a7b297b8ed399b86a474.png`
- **Local:** `social/assets/shamrock_logo_transparent.png`
- **Behavior:** Every post automatically includes the logo as media attachment
- **Override:** Use `--no-logo` flag to skip

### Phone Number
- **Correct (vanity):** `239-332-BAIL`
- **Numeric equivalent:** `(239) 332-2245`
- **WRONG:** `(239) 552-1349` (was hardcoded in 14 places — now fixed)
- Every post MUST include `239-332-BAIL`

### Content Footer
Every post auto-appends:
```
🍀 Shamrock Bail Bonds
📍 1528 Broadway, Ft. Myers, FL 33901
📞 239-332-BAIL
```

### Hashtag Strategy

| Platform | Max Tags | Strategy |
|----------|---------|----------|
| X | 5 | Core + 2 contextual |
| Instagram | 20 | Full spread |
| Facebook | 8 | Core + contextual |
| YouTube | 10 | Core + contextual |
| TikTok | 8 | Core + contextual |
| GMB | 0 | No hashtags |

**Core tags (always include):** `#ShamrockBailBonds` `#BailBonds` `#FloridaBail` `#FortMyers` `#SWFL` `#BailBondAgent`

### Video Workflow (End-to-End)

1. **Local → VPS:** `scp video.mp4 root@178.156.179.237:/tmp/`
2. **Upload to Postiz:** `postiz upload /tmp/video.mp4` → get `path`
3. **Create post:** `postiz posts:create -c "Content" -m "$path" -s "date" -i "ids"`
4. **Cleanup:** `rm /tmp/video.mp4`

Or use `shamrock_poster.py` which does all 4 steps automatically (including logo).

## File Map

| File | Purpose |
|------|---------|
| `social/config.py` | Engine configuration, platform limits, compliance disclaimer |
| `social/postiz_client.py` | Async Python HTTP client for Postiz Public API v1 |
| `social/shamrock_poster.py` | CLI posting tool with auto-logo, auto-hashtags, 239-332-BAIL |
| `social/assets/shamrock_logo_transparent.png` | Transparent logo (528×510) for post attachments |
| `social/scheduler.py` | APScheduler-based post scheduling |
| `social/ingestion.py` | Content ingestion pipeline |
| `social/humanizer.py` | AI detection avoidance (29 patterns) |
| `social/grok_client.py` | Grok/xAI content generation |
| `social/media_pipeline.py` | Media processing and optimization |
| `social/image_gen.py` | Image generation via DALL·E or Grok |
| `social/repurposer.py` | Content repurposing across platforms |
