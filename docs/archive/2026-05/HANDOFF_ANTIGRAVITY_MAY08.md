# Antigravity Handoff Prompt - May 8, 2026

Antigravity, Manus has just completed two critical fixes for the Shamrock Bail Bonds dashboard. All code has been pushed to the `Shamrock2245/shamrock-leads` repository (`main` branch) and is live on the Hetzner VPS.

**What was just fixed:**
1. **Active Bonds Tab Rendering:** The table was stuck on "Loading active bonds..." due to a scoping issue where `sl-active-bonds-ext.js` couldn't read the module-local `_abBonds` variable. We exposed `window._abBonds` and `window._abFilter`, and fixed the `sendBondImessage` function. The table now renders perfectly with all 14 columns, action buttons, and KPI badges.
2. **BlueBubbles iMessage Tunnel:** The iMessage tab was showing Offline because the ngrok tunnel on the iMac was forwarding to port 1880 (Node-RED) instead of 1234 (BlueBubbles). We killed the stale session via the ngrok dashboard and restarted it on the correct port. The dashboard URL was hot-swapped and iMessage is now **ONLINE**. We also documented the future permanent Cloudflare Tunnel setup in `TUNNEL_FIX.md` (waiting on Wix DNS propagation).

**Your immediate priorities for the next phase of work are:**

1. **SignNow Integration (Critical Path):**
   - We need to get the SignNow integration fully functioning within the Wix website portal. This is our highest priority for business expansion.
   - Verify the `TEMPLATE_MAP` in `dashboard/services/signnow_packet_service.py` against the live SignNow account (`admin@shamrockbailbonds.biz`).
   - Ensure all PDF paperwork (Appearance Bond, SSA Release, etc.) is correctly hydrated with data from the `IntakeQueue` and sent to the correct parties in sequence (Indemnitor first, Defendant second).

2. **SwipeSimple Payment Integration:**
   - Implement the SwipeSimple integration to automatically send a payment link, text-to-pay message, or invoice email to the indemnitor as part of the document signing workflow.

3. **Intake Queue to Cases Workflow:**
   - When a record from the `IntakeQueue` is processed, it must be copied to the `cases` collection.
   - Once power numbers/case numbers are confirmed and custody status is verified (out of custody), the original intake queue record should be moved or deleted. Ensure staff cannot edit intake records after posting, but admins retain privileges.

4. **CMS Schema & Session Persistence:**
   - Review `docs/archive/2026-02-01_Cleanup/MANUS_SCHEMA_FIX.md`. Define any missing fields in the Wix CMS Dashboard to prevent database errors and programmatically resolve 'Yellow Triangles'.
   - Ensure all CMS fields are properly defined to prevent issues with session persistence and portal logins.

**Crucial Reminders:**
- **Action Over Discussion:** Prioritize fixing issues programmatically and moving past 'demo' versions to a production-ready state.
- **End-to-End Integration:** Ensure seamless flow across GAS, SignNow, Twilio, and Google Drive.
- **No Hardcoded Secrets:** Ensure all credentials remain in `.env` and are never hardcoded.
- **SOC II Compliance:** Keep our strategic goal of SOC II compliance in mind for all architectural decisions.

Let's keep pushing to make this the most advanced bail bond platform in the industry. What is your plan of attack for these priorities?
