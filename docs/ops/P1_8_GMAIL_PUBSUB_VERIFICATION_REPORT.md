# P1.8 Gmail Pub/Sub Webhook Production Authentication & Readiness Report

> **Target Checklist Item:** [`ECOSYSTEM_PROD_CHECKLIST.md`](../ECOSYSTEM_PROD_CHECKLIST.md) §P1.8 (Gmail Pub/Sub authenticated push configuration)  
> **Status:** 🔲 **Human-Gated — Open** (Code & Test Suite Complete · Live VPS Fail-Closed 503 · GCP Admin Setup Required)  
> **Security Directives:**  
> 1. No PIN, GAS API key, or query-string credential bypass is allowed.  
> 2. Zero raw JWTs, mailboxes, email bodies, or payloads are logged.  
> 3. Rejected pushes produce zero audit events and trigger zero background jobs.

---

## 1. Required Configuration Categories

In accordance with [`docs/agents/court-clerk-agent.md`](../agents/court-clerk-agent.md), the Gmail Pub/Sub push authentication contract requires four coherent environment variables in `/opt/shamrock-leads/.env` on the VPS:

| # | Configuration Key | Requirement & Purpose | Validation Rule |
|---|---|---|---|
| **1** | `GMAIL_PUBSUB_AUDIENCE` | Exact OIDC audience configured in the Google Cloud Pub/Sub push subscription. | Must match token `aud` claim exactly (`https://leads.shamrockbailbonds.biz/api/webhooks/gmail`). |
| **2** | `GMAIL_PUBSUB_SERVICE_ACCOUNT_EMAIL` | Dedicated Google Service Account used exclusively for this push subscription. | Token `email` claim must match and `email_verified` must be `True`. |
| **3** | `GMAIL_PUBSUB_SUBSCRIPTION` | Full Google Cloud resource name of the subscription. | Request body `subscription` field must match `projects/<project>/subscriptions/<subscription>`. |
| **4** | `GMAIL_MONITORED_MAILBOX` | Exact registered Gmail address for court notifications. | Decoded Pub/Sub payload `emailAddress` must match case-insensitively. |

---

## 2. Endpoint Implementation & Security Controls

Inspected [`dashboard/routers/webhooks.py`](file:///Users/brendan/Desktop/shamrock-active-software/shamrock-leads/dashboard/routers/webhooks.py) lines 330–420 (`POST /api/webhooks/gmail`):

```python
# 1. Configuration check — fail closed 503
if not all((audience, expected_service_account, expected_subscription, monitored_mailbox)):
    return JSONResponse({"error": "Webhook authentication not configured"}, status_code=503)

# 2. Bearer scheme check — fail closed 401
if not separator or scheme.lower() != "bearer" or not token.strip():
    return JSONResponse({"error": "Unauthorized"}, status_code=401)

# 3. Google OIDC JWT verification (issuer, audience, expiry) — fail closed 401
claims = verify_gmail_pubsub_token(token.strip(), audience)

# 4. Service account identity & verified email check — fail closed 403
if token_email != expected_service_account or claims.get("email_verified") is not True:
    return JSONResponse({"error": "Forbidden"}, status_code=403)

# 5. Subscription resource check — fail closed 403
if data.get("subscription") != expected_subscription:
    return JSONResponse({"error": "Forbidden"}, status_code=403)

# 6. Monitored mailbox check — fail closed 403
if not isinstance(email_address, str) or email_address.strip().lower() != monitored_mailbox:
    return JSONResponse({"error": "Forbidden"}, status_code=403)

# 7. Non-PII audit event inserted ONLY upon 200 acceptance:
await audit_events.insert_one({
    "source": "gmail_pubsub_webhook",
    "event_type": "court_email_notification",
    "history_id": history_id,
    "message_id": message_id,
    "timestamp": datetime.now(timezone.utc).isoformat(),
})
```

---

## 3. Live Production Probe Results

A live HTTPS probe was executed against the production deployment:

```bash
curl -sS -i -X POST https://leads.shamrockbailbonds.biz/api/webhooks/gmail
```

### Production Response:
```http
HTTP/2 503 
server: nginx/1.24.0 (Ubuntu)
date: Mon, 24 Aug 2026 14:27:48 GMT
content-type: application/json
content-length: 49
strict-transport-security: max-age=63072000; includeSubDomains; preload
x-content-type-options: nosniff
x-frame-options: SAMEORIGIN
referrer-policy: strict-origin-when-cross-origin

{"error":"Webhook authentication not configured"}
```

### Diagnosis:
- The endpoint is live, active, and securely failing closed with `HTTP 503`.
- No unauthenticated requests or unauthorized payloads can proceed because the four environment variables are not yet populated on the VPS.
- Zero audit records or background parsing tasks were triggered.

---

## 4. Automated Regression Test Suite

Test suite in [`tests/test_gmail_webhook.py`](file:///Users/brendan/Desktop/shamrock-active-software/shamrock-leads/tests/test_gmail_webhook.py) verified 100% of authentication states:

| Test Case | Scenario Tested | Expected Status | Audit Inserted? | Test Result |
|---|---|---|---|---|
| `test_gmail_pubsub_webhook_fails_closed_without_configuration` | Missing any of the 4 env vars | `503 Service Unavailable` | ❌ No | ✅ Passed |
| `test_gmail_pubsub_webhook_rejects_untrusted_requests` | Missing `Authorization` header | `401 Unauthorized` | ❌ No | ✅ Passed |
| `test_gmail_pubsub_webhook_rejects_untrusted_requests` | Invalid audience / bad JWT signature | `401 Unauthorized` | ❌ No | ✅ Passed |
| `test_gmail_pubsub_webhook_rejects_untrusted_requests` | Unexpected service account email | `403 Forbidden` | ❌ No | ✅ Passed |
| `test_gmail_pubsub_webhook_rejects_untrusted_requests` | Mismatched subscription resource | `403 Forbidden` | ❌ No | ✅ Passed |
| `test_gmail_pubsub_webhook_rejects_untrusted_requests` | Mismatched monitored mailbox | `403 Forbidden` | ❌ No | ✅ Passed |
| `test_gmail_pubsub_webhook_endpoint` | Valid Google OIDC token + matching claims | `200 OK` | ✅ Yes (non-PII) | ✅ Passed |

*Test Run: `9 passed in 0.75s`*.

---

## 5. Required GCP Operator Setup to Close P1.8

To enable live production push notifications and verify item **P1.8**:

1. **Google Cloud Console (`shamrock-bail-bonds` / GCP Project):**
   - Create dedicated service account: `shamrock-pubsub-push@shamrock-leads.iam.gserviceaccount.com`.
   - Grant `roles/iam.serviceAccountTokenCreator` to the Google Pub/Sub Service Agent (`service-<PROJECT_NUMBER>@gcp-sa-pubsub.iam.gserviceaccount.com`).
   - Create Pub/Sub Push Subscription:
     - **Push endpoint URL:** `https://leads.shamrockbailbonds.biz/api/webhooks/gmail`
     - **Enable authentication:** Checked
     - **Service account:** `shamrock-pubsub-push@shamrock-leads.iam.gserviceaccount.com`
     - **Audience:** `https://leads.shamrockbailbonds.biz/api/webhooks/gmail`
2. **VPS Environment (`/opt/shamrock-leads/.env`):**
   - Populate:
     ```bash
     GMAIL_PUBSUB_AUDIENCE=https://leads.shamrockbailbonds.biz/api/webhooks/gmail
     GMAIL_PUBSUB_SERVICE_ACCOUNT_EMAIL=shamrock-pubsub-push@shamrock-leads.iam.gserviceaccount.com
     GMAIL_PUBSUB_SUBSCRIPTION=projects/<PROJECT_ID>/subscriptions/gmail-court-notifications-push
     GMAIL_MONITORED_MAILBOX=court-notifications@shamrockbailbonds.biz # (or designated court mailbox)
     ```
   - Restart dashboard container: `docker compose restart dashboard`.
3. **Live Verification Steps:**
   - Probe 1 (Unauthenticated): `curl -i -X POST https://leads.shamrockbailbonds.biz/api/webhooks/gmail` → returns `HTTP 401 Unauthorized`.
   - Probe 2 (Authenticated Pub/Sub Test): Send test push from GCP Console → returns `HTTP 200 OK` with non-PII `audit_events` write.

---

## 6. Truthful Status

- **Code & Test Suite:** 🟢 **Production-Ready & Fully Verified**
- **Production Infrastructure:** 🔲 **Human-Gated / GCP Admin Setup Required**
- **Checklist Item P1.8:** **Remains OPEN** until GCP subscription and VPS environment variables are deployed by Brendan.
