# MongoDB Backup/Restore, `MONGO_PROXY_V2_URL` Failover, and Index Proposals

> **Database:** MongoDB Atlas (`ShamrockBailDB`)
> **Primary Writer:** `writers/mongo_writer.py` (direct `MONGODB_URI` via `pymongo.MongoClient`; does **not** fail over to `MONGO_PROXY_V2_URL`)
> **Dashboard API:** `dashboard/extensions.py` (`motor` async client via `MONGODB_URI`)
> **GAS / External HTTP Proxy:** `MONGO_PROXY_V2_URL` (`cloud-functions/mongo-proxy`, used by Google Apps Script / non-TCP callers)

---

## 1. Non-Negotiable Data Safety Rules

1. **Never delete `audit_events`** — Audit history is immutable.
2. **Never overwrite stored ID scans** — `indemnitors.id_scans[]` and `paperwork_packets.id_photos` / `kyc_uploads` are append-only evidence records (enforced by PR #299).
3. **Never run destructive migrations, backfills, or index builds in production without explicit human sign-off from Brendan.**

---

## 2. MongoDB Backup & Restore Runbook

### 2.1 Pre-Maintenance Snapshot (`mongodump` + AES-256 Encryption)
Before any approved schema or index maintenance, create a restricted (`0700`) staging directory, dump `ShamrockBailDB` as a single compressed stream, encrypt it with AES-256 (`gpg --symmetric --cipher-algo AES256`), and remove any unencrypted staging artifacts:

```bash
umask 077
BACKUP_ROOT="/var/backups/shamrock-mongo"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE_ENC="${BACKUP_ROOT}/ShamrockBailDB-${STAMP}.archive.gz.gpg"
mkdir -p -m 700 "$BACKUP_ROOT"

# Stream compressed mongodump archive directly into AES-256 GPG encryption (no plaintext files written to disk)
mongodump --uri="$MONGODB_URI" --db=ShamrockBailDB --archive --gzip \
  | gpg --batch --yes --passphrase-fd 3 --symmetric --cipher-algo AES256 -o "$ARCHIVE_ENC" 3<"$BACKUP_PASS_FILE"
chmod 600 "$ARCHIVE_ENC"
```

Verify the encrypted backup archive exists, has `0600` permissions, and is non-empty (log byte sizes only, never document contents or PII):
```bash
ls -lh "$ARCHIVE_ENC"
```

### 2.2 Point-in-Time / Collection Restore (`mongorestore`)
To restore a collection into a staging database first (never overwrite production collections blindly):

```bash
# 1. Decrypt stream and restore into ShamrockBailDB_RestoreVerify first
gpg --batch --passphrase-fd 3 --decrypt "$ARCHIVE_ENC" 3<"$BACKUP_PASS_FILE" \
  | mongorestore --uri="$MONGODB_URI" \
      --archive --gzip \
      --nsFrom="ShamrockBailDB.*" \
      --nsTo="ShamrockBailDB_RestoreVerify.*"

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

## 3. `MONGO_PROXY_V2_URL` vs. Direct `MONGODB_URI` Failure Runbook

### 3.1 Architecture & Failure Boundary (No Automatic Cross-Failover)
- **Hetzner VPS (`shamrock-leads` & `shamrock-dashboard`):** `MongoWriter.__init__` (`writers/mongo_writer.py`) and `dashboard/extensions.py` read **only** `MONGODB_URI` and connect directly to MongoDB Atlas via TLS (`pymongo` / `motor`). **There is no automatic failover from `MONGODB_URI` to `MONGO_PROXY_V2_URL` in `shamrock-leads`.** If direct `MONGODB_URI` connectivity fails on the VPS, scraper writes fail closed (`records_write_failed`) and alert `#scraper-errors` until direct Atlas connectivity is restored.
- **Google Cloud Function Bridge (`MONGO_PROXY_V2_URL`):** Used by Google Apps Script (`shamrock-bail-portal-site/backend-gas`) and external HTTP callers that cannot open raw TCP `27017` connections to MongoDB Atlas (`cloud-functions/mongo-proxy`).

### 3.2 Symptoms
- **Direct `MONGODB_URI` outage on VPS:** `MongoWriter` raises `ServerSelectionTimeoutError` / `PyMongoError`, `BaseScraper` marks `records_write_failed_at` (without advancing `last_success_at`), and `scripts/check_lee_arrest_freshness.py` emits `records_write_failed`.
- **`MONGO_PROXY_V2_URL` outage:** HTTP `500` / `502` / `503` / `403` from the Google Cloud Function URL when GAS or external scripts call the proxy, while VPS scrapers and the FastAPI dashboard continue operating normally over direct `MONGODB_URI`.

### 3.3 Triage & Recovery Steps
1. **Verify Direct `MONGODB_URI` Health on VPS (Read-Only):**
   ```bash
   ssh root@178.156.179.237 "docker exec shamrock-dashboard python3 -c '
   import os, pymongo
   c = pymongo.MongoClient(os.environ[\"MONGODB_URI\"], serverSelectionTimeoutMS=5000)
   print(c.admin.command(\"ping\"))
   '"
   ```
   - If direct `MONGODB_URI` fails: verify Hetzner VPS IP (`178.156.179.237`) is present in MongoDB Atlas Network Access, check Atlas cluster health, and verify `MONGODB_URI` credentials in `/opt/shamrock-leads/.env`.
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
3. **Recovering `MONGO_PROXY_V2_URL` (Cloud Function):**
   - Check Google Cloud Functions logs for `mongo-proxy` (`gcloud functions logs read mongo-proxy --limit 30`).
   - Verify the Cloud Function's `MONGODB_URI` secret in GCP Secret Manager matches the active Atlas cluster URI and that the Cloud Function egress is permitted in Atlas Network Access.

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
| `ingestion_log` | `[("county", 1), ("state", 1), ("timestamp", -1)]` | `background=True` | Per-county/state ingestion history queries (`MongoWriter.log_ingestion` stores `county`, `state`, and `timestamp`) and retention cleanup (`maintenance/cleanup.py`). |

### Command to Inspect Existing Indexes (Read-Only)
```bash
ssh root@178.156.179.237 "docker exec shamrock-dashboard python3 -c '
import os, pymongo
db = pymongo.MongoClient(os.environ[\"MONGODB_URI\"])[\"ShamrockBailDB\"]
for name in (\"arrests\", \"prospective_bonds\", \"active_bonds\", \"paperwork_packets\", \"ingestion_log\"):
    print(name, list(db[name].index_information().keys()))
'"
```
