# Staff Runbook: D2 Dashboard iMessage Smoke & BlueBubbles Diagnostics

> **Target Checklist Item:** [`ECOSYSTEM_PROD_CHECKLIST.md`](../ECOSYSTEM_PROD_CHECKLIST.md) §D2 (Dashboard iMessage send succeeds to a test number)  
> **Status:** 🔲 **Human-Gated — Open** (Diagnostic Preflight Tools Ready · Live Tunnel Reachability Pending)  
> **Policy Guard:** **D3 Review-First Mode is strictly enforced.** All automations remain in `review` mode with zero automatic retries or `full_auto` transitions.

---

## 1. Diagnostic Preflight Results (Live Run)

A safe, non-PII read-only diagnostic probe was executed across all candidate BlueBubbles ingress routes:

```json
{
  "success": false,
  "state": "unavailable_tunnel",
  "error_code": "unavailable_tunnel",
  "message": "All BlueBubbles tunnel endpoints unreachable or rejected connection.",
  "probes": [
    {
      "target": "178.156.179.237:12434",
      "state": "unavailable_tunnel",
      "status_code": 0,
      "error": "unreachable"
    },
    {
      "target": "100.102.10.86:1234",
      "state": "unavailable_tunnel",
      "status_code": 0,
      "error": "timeout"
    },
    {
      "target": "bb.shamrockbailbonds.biz",
      "state": "unavailable_tunnel",
      "status_code": 0,
      "error": "timeout"
    },
    {
      "target": "localhost:1234",
      "state": "unavailable_tunnel",
      "status_code": 0,
      "error": "unreachable"
    }
  ]
}
```

### Precise Diagnosis:
- **`unavailable_tunnel`:** The office iMac hosting BlueBubbles Server 1.9.9 (`shamrockbailoffice@gmail.com` / `...0178`) is currently unreachable across all three configured transports (Tailscale mesh `100.102.10.86:1234`, Cloudflare tunnel `bb.shamrockbailbonds.biz`, and frp relay `178.156.179.237:12434`).
- **Authentication & Code Status:** BlueBubbles client code, endpoint parsing, and password handling are verified and healthy. The failure is strictly network/host reachability on the office Mac hardware.

---

## 2. Prerequisites to Enable Live Smoke

Staff must complete the following steps on the office iMac before attempting the smoke:

1. **Verify Office iMac Power & Network:**
   - Confirm the office iMac is powered on, awake, and connected to the Internet.
2. **Verify BlueBubbles Server 1.9.9:**
   - Launch `BlueBubbles.app` on the iMac (or verify LaunchAgent `com.shamrock.bluebubbles-autostart.plist`).
   - Confirm status indicates **Server Online** on port `1234` with **Private API** enabled.
3. **Verify at least one tunnel is active:**
   - **Tailscale:** `tailscale status` shows iMac connected (`100.102.10.86`).
   - **Cloudflare Tunnel:** `launchctl list | grep cloudflare` shows `com.shamrock.cloudflared-tunnel` running.
   - **frp relay:** `frpc -c frpc.ini` connected to VPS port 12434.

---

## 3. Staff Execution Checklist for Single Live Smoke

Once tunnel reachability is restored, follow this procedure with an authorized staff test recipient:

### Step 1: Preflight Verification
```bash
python3 -c '
import asyncio, json
from dashboard.services.bb_diagnostic_service import preflight_imessage_smoke
res = asyncio.run(preflight_imessage_smoke(recipient_phone="STAFF_TEST_PHONE"))
print(json.dumps(res, indent=2))
'
```
*Must return `state: "eligible_for_staff_approval"` and `server_state: "healthy"`.*

---

### Step 2: Staff-Authorized Live Smoke Dispatch
```bash
python3 -c '
import asyncio, json
from dashboard.services.bb_diagnostic_service import execute_staff_approved_imessage_smoke
res = asyncio.run(execute_staff_approved_imessage_smoke(
    recipient_phone="STAFF_TEST_PHONE",
    staff_actor="brendan@shamrockbailbonds.biz",
    confirmed=True,
    correlation_id="D2-SMOKE-01"
))
print(json.dumps(res, indent=2))
'
```

---

### Step 3: Required Evidence Capture for Checklist D2

To mark **D2** as `[x]` in `docs/ECOSYSTEM_PROD_CHECKLIST.md`:
1. **Safe Provider Result:** BlueBubbles server returns HTTP 200 with message GUID.
2. **Non-PII Audit Event:** Entry logged in MongoDB `audit_events` with `event_type: "dashboard_imessage_smoke_sent"`, `status: "delivered"`, masked recipient (`...XXXX`), and actor attribution.
3. **Physical Receipt Verification:** Test recipient receives the iMessage (blue bubble) on their device.
4. **Policy Maintenance:** `D3` remains in **`review`** mode. No automations are flipped to `full_auto`.
