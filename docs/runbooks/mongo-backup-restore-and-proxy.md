# MongoDB Backup/Restore, `MONGO_PROXY_V2_URL` Failover, and Index Proposals

> **Database:** MongoDB Atlas (`ShamrockBailDB`)
> **Primary Writer:** `writers/mongo_writer.py` (direct `MONGODB_URI`, with `MONGO_PROXY_V2_URL` HTTP fallback)
> **Dashboard API:** `dashboard/extensions.py` (`motor` async client via `MONGODB_URI`)

---

## 1. Non-Negotiable Data Safety Rules

1. **Never delete `audit_events`** — Audit history is immutable.
2. **Never overwrite stored ID scans** — `indemnitors.id_scans[]` and `paperwork_packets.id_photos` / `kyc_uploads` are append-only evidence records (enforced by PR #299).
3. **Never run destructive migrations, backfills, or index builds in production without explicit human sign-off from Brendan.**

---

## 2. MongoDB Backup & Restore Runbook

### 2.1 Pre-Maintenance Snapshot (`mongodump`)
Before any approved schema or index maintenance, take an encrypted logical backup of `ShamrockBailDB`:

```bash
BACKUP_DIR="/var/backups/shamrock-mongo/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_DIR"
mongodump --uri="$MONGODB_URI" --db=ShamrockBailDB --gzip --out="$BACKUP_DIR"
```

Verify the backup archive exists and is non-empty (log byte sizes only, never document contents or PII):
```bash
ls -lh "$BACKUP_DIR/ShamrockBailDB"
```

### 2.2 Point-in-Time / Collection Restore (`mongorestore`)
To restore a collection into a staging database first (never overwrite production collections blindly):

```bash
# 1. Restore into ShamrockBailDB_RestoreVerify first
mongorestore --uri="$MONGODB_URI" \
  --nsFrom="ShamrockBailDB.*" \
  --nsTo="ShamrockBailDB_RestoreVerify.*" \
  --gzip "$BACKUP_DIR"

# 2. Verify document counts (counts only — zero PII)
python3 -c '
import os, pymongo
c = pymongo.MongoClient(os.environ["MONGODB_URI"])
for col in ("arrests", "active_bonds", "prospective_bonds", "indemnitors", "paperwork_packets", "payments", "audit_events"):
    print(col, c["ShamrockBailDB_RestoreVerify"][col].estimated_document_count())
'
```

When restoring into production after human approval:
- Never pass `--drop` on `audit_events`, `payments`, `paperwork_packets`, or `indemnitors`.
- Use upsert/merge semantics so existing `audit_events` and stored ID scans are preserved.

---

## 3. `MONGO_PROXY_V2_URL` Failure Runbook

### 3.1 How `MONGO_PROXY_V2_URL` Is Used
- **Primary path on Hetzner VPS (`shamrock-leads` & `shamrock-dashboard`):** Direct TLS connection to MongoDB Atlas via `MONGODB_URI` (`pymongo` / `motor`).
- **Fallback / Cloud Function bridge (`MONGO_PROXY_V2_URL`):** Used by `writers/mongo_writer.py` only when direct `MONGODB_URI` connection is unavailable or when GAS / external scrapers push arrest batches through the Google Cloud Functions HTTP proxy (`cloud-functions/mongo-proxy`).

### 3.2 Symptoms When `MONGO_PROXY_V2_URL` Fails
- HTTP `500` / `502` / `503` / `403` from the Google Cloud Function URL.
- If `MONGODB_URI` is healthy on the VPS, **VPS scrapers and the FastAPI dashboard continue operating normally** over direct `MONGODB_URI`.
- Only proxy-only callers (or VPS runs when Atlas IP allowlist blocks direct `27017` egress) fail when `MONGO_PROXY_V2_URL` is down.

### 3.3 Triage & Recovery Steps
1. **Verify Direct `MONGODB_URI` Health on VPS (Read-Only):**
   ```bash
   ssh root@178.156.179.237 "docker exec shamrock-dashboard python3 -c '
   import os, pymongo
   c = pymongo.MongoClient(os.environ[\"MONGODB_URI\"], serverSelectionTimeoutMS=5000)
   print(c.admin.command(\"ping\"))
   '"
   ```
2. **Check `MONGO_PROXY_V2_URL` Presence and Reachability (Status Code Only):**
   ```bash
   ssh root@178.156.179.237 "docker exec shamrock-leads python3 -c '
   import os, requests
   url = os.getenv(\"MONGO_PROXY_V2_URL\", \"\").strip()
   print({\"configured\": bool(url)})
   if url:
       r = requests.get(url, timeout=10)
       print({\"status_code\": r.status_code})
   '"
   ```
3. **Recovering the Proxy:**
   - Check Google Cloud Functions logs for `mongo-proxy` (`gcloud functions logs read mongo-proxy --limit 30`).
   - Verify the Cloud Function's `MONGODB_URI` secret in GCP Secret Manager matches the active Atlas cluster URI and that `0.0.0.0/0` (or the Cloud Function egress NAT) is permitted in Atlas Network Access.
   - Until the proxy is restored, ensure `MONGODB_URI` is set in `/opt/shamrock-leads/.env` on the Hetzner VPS (`178.156.179.237` is allowlisted in Atlas) so `MongoWriter` uses direct Atlas writes without touching the proxy.

---

## 4. Proposed Production Indexes (For Human Review — Do Not Auto-Create)

Per production safety rules, no new indexes are created automatically in production. Below are the proposed index specifications for hot query paths identified in `ShamrockBailDB`, together with the query shapes they serve:

| Collection | Proposed Index Key | Options | Hot Query Shape Served |
|---|---|---|---|
| `arrests` | `[("county", 1), ("booking_number", 1), ("state", 1)]` | `unique=True, background=True` | Natural-key deduplication in `MongoWriter.write_records`, `_provider_prior`, and multi-state arrest lookups. |
| `arrests` | `[("booking_number", 1)]` | `background=True` | Single-booking lookup across `/api/leads`, `/api/contacts/{booking_number}`, `prospective_bonds.py`, and `cosigner_link_service._find_bond`. |
| `arrests` | `[("first_seen_at", -1), ("lead_score", -1)]` | `background=True` | `/api/stats` (`today_new`), Command Center hot leads feed, and `FirstAppearanceWatcher`. |
| `prospective_bonds` | `[("booking_number", 1)]` | `background=True` | Pipeline lookups (`GET /api/prospective-bonds`, `POST /api/indemnitors/create`, `_provider_prior` same-case refresh). |
| `active_bonds` | `[("booking_number", 1)]` | `background=True` | Payment recorder (`payment_log_claims._matching_bonds`), `cosigner_link_service._find_bond`, and re-arrest detection. |
| `active_bonds` | `[("Booking_Number", 1)]` | `sparse=True, background=True` | Legacy `Booking_Number` fallback in `payment_log_claims._matching_bonds` and `cosigner_link_service._booking_query`. |
| `paperwork_packets` | `[("packet_id", 1)]` | `unique=True, sparse=True, background=True` | DocuSeal webhook completion handler, Shannon ID upload (`/paperwork/shannon/id/{token}`), and co-signer link resolution. |
| `ingestion_log` | `[("timestamp", -1), ("county_label", 1)]` | `background=True` | Retention cleanup (`maintenance/cleanup.py`) and per-county run history queries. |

### Command to Inspect Existing Indexes (Read-Only)
```bash
ssh root@178.156.179.237 "docker exec shamrock-dashboard python3 -c '
import os, pymongo
db = pymongo.MongoClient(os.environ[\"MONGODB_URI\"])[\"ShamrockBailDB\"]
for name in (\"arrests\", \"prospective_bonds\", \"active_bonds\", \"paperwork_packets\", \"ingestion_log\"):
    print(name, list(db[name].index_information().keys()))
'"
```
