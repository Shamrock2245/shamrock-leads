# Antigravity Handoff Prompt — May 07, 2026

**Copy and paste the following prompt to Antigravity to continue the work:**

***

> **Prompt for Antigravity:**
>
> "Antigravity, Manus has just completed a critical bug fix and deployment for the Shamrock Bail Bonds dashboard. All code has been pushed to the `Shamrock2245/shamrock-leads` repository (`main` branch) and is live on the Hetzner VPS.
>
> **What was just fixed:**
> The Active Bonds tab was showing 'Loading active bonds...' despite the API returning data. The root cause was a scoping issue where `sl-active-bonds-ext.js` was trying to read from `window._abBonds`, but the data was only stored locally in `sl-active-bonds.js`. We exposed `window._abBonds` and `window._abFilter`, and fixed the `sendBondImessage` function to correctly use `window.SLiMessage` and the tab switching API. The Active Bonds table now renders perfectly with all 14 columns, action buttons, and KPI badges.
>
> **Your immediate priorities for the next phase of work are:**
>
> 1. **SignNow Integration (Critical Path):**
>    - We need to get the SignNow integration fully functioning within the Wix website portal. This is our highest priority for business expansion.
>    - Verify the `TEMPLATE_MAP` in `dashboard/services/signnow_packet_service.py` against the live SignNow account (`admin@shamrockbailbonds.biz`).
>    - Ensure all PDF paperwork (Appearance Bond, Indemnity Agreement, SSA Release, POA) is appropriately tagged for SignNow in all signature spots.
>    - Confirm the data hydration logic from `Dashboard.html` (via GAS) to the PDFs is mapping correctly without adding non-signature fields.
>
> 2. **Wix Portal & CMS Schema Fixes:**
>    - Review `docs/archive/2026-02-01_Cleanup/MANUS_SCHEMA_FIX.md`. We need to define missing fields in the Wix CMS Dashboard to prevent database errors ('Yellow Triangles') and ensure session persistence for portal logins.
>    - Ensure the sign-in links for different user roles (defendant, staff, indemnitor) on the Wix portal-landing page are fully functional and route correctly.
>    - Verify the login flow supports WhatsApp/Email in the main input box alongside the existing Gmail social login.
>
> 3. **Payment Integration (SwipeSimple):**
>    - We recently added a 'Send Pay Link' button to the Active Bonds cards (commit `727329a`). Ensure this integration with SwipeSimple is fully robust for the indemnitor workflow (sending payment links, text-to-pay, or invoice emails during the document signing process).
>
> 4. **Intake Queue to Cases Workflow:**
>    - Implement the logic where processed intake queue records are copied to the 'cases' collection.
>    - Once power numbers/case numbers are confirmed and custody status is verified (out of custody), the original intake queue record should be moved or deleted. Ensure staff cannot edit intake records after posting, but admins retain privileges.
>
> 5. **Infrastructure & Environment:**
>    - The BlueBubbles tunnel has been permanently migrated to a static ngrok domain (`https://pseudospherical-etta-untactually.ngrok-free.dev`). Ensure all `.env` variables reflect this.
>    - Verify `GAS_WEB_APP_URL` and `WIX_WEBHOOK_SECRET` are correctly set in the production environment.
>
> **Crucial Reminders:**
> - **Action Over Discussion:** Prioritize fixing issues programmatically and moving past 'demo' versions to a production-ready state.
> - **End-to-End Integration:** Ensure seamless flow across GAS, SignNow, Twilio, and Google Drive.
> - **No Hardcoded Secrets:** Ensure all credentials remain in `.env` and are never hardcoded.
> - **SOC II Compliance:** Keep our strategic goal of SOC II compliance in mind for all architectural decisions (reference `strongdm/comply` or `getprobo/probo` if needed).
>
> Let's keep pushing to make this the most advanced bail bond platform in the industry, surpassing Captira and Bail Books. What is your plan of attack for these priorities?"
