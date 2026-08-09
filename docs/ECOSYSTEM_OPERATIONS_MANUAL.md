# Shamrock Bail Bonds — Employee Operations Guide

> **Last updated:** 2026-08-09  
> **Who this is for:** Office staff, licensed bondsmen, new hires — **not** engineers  
> **Open in the app:** Press **F1** on the dashboard, or open  
> `https://leads.shamrockbailbonds.biz/guide`

This guide explains, in plain English, how to run day-to-day work across:

| What | Where you go |
|------|----------------|
| **Leads dashboard (write bonds, paperwork, cases)** | https://leads.shamrockbailbonds.biz |
| **Client signing portal** | https://paperwork.shamrockbailbonds.biz |
| **E-sign system (DocuSeal)** | https://sign.shamrockbailbonds.biz |
| **Bail School website** | https://school.shamrockbailbonds.biz |
| **Social media manager (Postiz)** | https://social.shamrockbailbonds.biz |
| **Public company site / intake** | https://www.shamrockbailbonds.biz |

---

## Quick start (first day on the job)

1. Open the **Leads dashboard** → enter the office PIN when asked.  
2. Press **F1** anytime for the slide-out help drawer (CRM + Social guides).  
3. Bookmark the table of links above.  
4. Never invent case numbers, POA numbers, or payment status — if you’re unsure, ask a licensed bondsman or Brendan.

---

## Chapter 1 — Leads dashboard (ShamrockLeads)

### Log in
1. Go to **https://leads.shamrockbailbonds.biz**
2. Enter your **agency PIN**
3. You land on the main CRM (leads, bonds, paperwork, ops)

### Keyboard shortcuts
| Key | What it does |
|-----|----------------|
| **F1** | Open / close Help drawer |
| **Ctrl+K** or **Cmd+K** | Omnibar search (arrests, defendants, cases) |
| **Esc** | Close modal or drawer |

### Lead Explorer (arrests)
- The system **scrapes county jail rosters** and scores every arrest **0–100**.
- **Hot (80–100)** — strong bond candidate. Slack `#leads` may fire; prioritize outreach.
- **Warm (50–79)** — follow up when you can.
- **Cold / Disqualified** — often $0 bond, ROR, released, or capital/federal; do not waste time unless a human overrides.

**How to filter:** Use the **State** and **County** dropdowns at the top (FL, GA, SC, NC, TN, TX, LA, AL, CT, MS). Same county names exist in multiple states (e.g. Lee FL ≠ Lee SC) — always check the state.

---

## Chapter 2 — How to write a bond (step by step)

This is the core job: turn an arrest + cosigner into a **bonded case** with paperwork and signatures.

### Before you start — checklist
- [ ] You know **who is in jail** (defendant)
- [ ] You know **who is signing / paying** (indemnitor / cosigner)
- [ ] You know **bond amount(s)** and charges (or will pull from the lead)
- [ ] You know which **surety** to use: **OSI** (preferred in Florida) or **Palmetto** (multi-state / when OSI inventory is short)

### Step 1 — Open Write Bond
1. From **Lead Explorer**, click the arrest row **or** click **➕ Record Bond** / **✍️ Write Bond**.
2. Search for the defendant by name if needed.
3. Confirm booking number, county, and facility look correct.

### Step 2 — Review auto-filled fields
The form should fill:
- Name, DOB, booking #, county, facility, case #  
- Court date / time / location (or **TBN** if unknown)  
- Charges and bond amounts  
- **Premium** (Florida style: about **10%** of bond, **minimum $100 per charge**)

**If something is wrong, fix it before generating paperwork.** Do not guess POA numbers.

### Step 3 — Surety and POA (power of attorney)
1. Choose **OSI** or **Palmetto**.
2. The system suggests the **next available POA** from inventory for that surety.
3. Confirm the power number on the physical book / inventory if your office process requires it.
4. For multi-charge bonds, each charge may get its own POA row (up to 4 on the form).

### Step 4 — Indemnitor (cosigner)
1. Enter indemnitor name, phone, email, address.  
2. Prefer **scanning their ID** (portal or staff ID scanner) so address/DL auto-fill.  
3. If there is a **co-indemnitor**, add them too — they will get their own signing role.

### Step 5 — Generate / send paperwork (DocuSeal)
1. Click **Generate Paperwork** / **Flatten & Send** (wording may vary by tab).
2. Provider should be **DocuSeal** (not SignNow — SignNow is legacy only).
3. The system creates a multi-party signing packet and **sign links**.
4. Send the indemnitor their link by:
   - **iMessage / SMS** from the dashboard, or  
   - Handing them the **portal** / kiosk on an iPad, or  
   - Opening the link on an office tablet for in-person sign.

### Step 6 — After signatures
- When everyone required has signed, DocuSeal notifies the system.
- The signed PDF is filed to **Google Drive → Completed Bonds**  
  filename style: `LastName_MMDDYY_SURETY.pdf` (example: `Doe_080926_OSI.pdf`).
- Move the case on the **Active Bonds** board to **Active** (or your office’s next status).

### Step 7 — Payment
- Collect premium via **SwipeSimple** (card link / terminal) or log cash per office policy.
- Never mark a bond “paid” unless payment is confirmed in the tool or receipt.

### Common “Write Bond” problems

| Problem | What to do |
|---------|------------|
| No POA suggested | Check surety inventory; switch surety only if policy allows |
| Prefill looks empty | Confirm defendant + indemnitor names; use **Prefill Preview** on Paperwork |
| Cosigner can’t open link | Resend from Paperwork tab, or use portal PIN flow |
| Wrong defendant on packet | **Stop.** Bind/match correctly before more signatures |
| DocuSeal not configured | Call tech: needs `DOCUSEAL_API_KEY` on the server |

### Fast path: cosigner signs before we know the defendant
Sometimes a family member is ready to sign **before** the jail name is fully matched:
1. On the **paperwork portal**, they can scan ID → **Sign Paperwork Now (Defendant Matched Later)**.
2. Staff later **binds the defendant** to that packet in the CRM (name, booking #, county, case #).
3. Still complete match / bond case / POA rules before treating the bond as fully written.

---

## Chapter 3 — Paperwork & DocuSeal (for office staff)

### Where things live
| Tool | URL | Use it for |
|------|-----|------------|
| Paperwork tab in CRM | leads… → Paperwork | Packets, resend, status |
| Client portal | https://paperwork.shamrockbailbonds.biz | PIN login, ID scan, mobile sign |
| DocuSeal admin | https://sign.shamrockbailbonds.biz | Templates (managers/tech only) |

### Staff routine
1. Open **Paperwork** tab.  
2. Find packet by defendant or indemnitor name.  
3. Check status: sent / partially signed / signed.  
4. **Resend** if someone lost the link (do not create a second packet unless the first is voided).  
5. When signed, confirm Drive file exists under Completed Bonds.

### Client routine (what you tell them)
1. “We’ll text you a secure link.”  
2. “Open it on your phone, take a picture of your ID if asked, then initial and sign.”  
3. “When you’re done, we’ll get a copy automatically — you don’t need to email us the PDF.”

### Portal PIN
- Some flows use a **6-digit PIN** texted to the client.  
- Staff can re-send PIN from the portal tools if the office process allows.  
- Master PIN is for **staff smoke tests only** — never give it to clients.

---

## Chapter 4 — Active bonds (Kanban)

After the bond is posted, manage the case on **Active Bonds**:

| Status | Meaning |
|--------|---------|
| **Active** | Bond posted, defendant out, check-ins OK |
| **Monitoring** | Needs closer attention (big bond, court soon) |
| **Alert** | Missed check-in or contact failure — act today |
| **Exonerated** | Court discharged the bond — POA returns to inventory |
| **Forfeited** | FTA / forfeiture — use confirm dialog; serious action |
| **Surrendered** | Defendant surrendered — POA released after confirm |
| **Reinstated** | Court set aside forfeiture |

**Tip:** Drag-and-drop cards between columns. Destructive statuses ask for confirmation — read the dialog.

---

## Chapter 5 — Bail School website (for staff)

> **Site:** https://school.shamrockbailbonds.biz  
> **Purpose:** Pre-licensing education (not the bond CRM). Students buy courses and complete hours online / in person.

### What Bail School is
Shamrock Bail School trains people who want to **become bail bond agents** (and related education). It is a **separate product** from writing bonds for defendants, but same brand.

### Current product lines (typical)
| Course | Typical price | Notes |
|--------|---------------|--------|
| 20-hour | **$199** | Entry / shorter track |
| 120-hour | **$649** | Full pre-licensing track |

Prices can change — if the website and a flyer disagree, **trust the live website** after publish, and tell marketing if wrong.

### What staff might do
1. **Answer questions** — “How do I sign up?” → send **school.shamrockbailbonds.biz**.  
2. **Payment issues** — students pay through the school checkout (SwipeSimple / school payment path). Bond SwipeSimple receipts and school payments are tracked carefully so they don’t mix.  
3. **Do not** invent login credentials. Password resets go through the school portal / support process.  
4. **Marketing pages** live on Wix/public site; the **LMS** (lessons, progress) is the school app.

### What staff should **not** do
- Do not put school students into the **Write Bond** flow unless they are also cosigning a real bond.  
- Do not change GAS Web App URLs (technical policy — breaks Wix + school).  
- Do not quote old **$699** pricing if the site shows **$649**.

### If school is down
1. Confirm https://school.shamrockbailbonds.biz loads.  
2. Note the error message / time.  
3. Escalate to tech (Netlify / school repo / GAS secrets).  
4. Offer to take a call-back rather than guessing enrollment status.

---

## Chapter 6 — Social media (Postiz)

> **Hub:** https://social.shamrockbailbonds.biz  

### Connect accounts (once)
1. Log into Postiz.  
2. **Integrations** → connect Facebook Page, Instagram `@shamrock_bail_bonds`, X `@ShamrockBail_FL`, YouTube `@Shamrock2245`, Google Business Profile.  
3. Green check = connected.

### Create a post
1. **Create Post**  
2. Select channels  
3. Write caption (professional, 24/7 service, phone **(239) 334-2245**)  
4. Add media (1080×1080 image is safest)  
5. **Post Now** or **Schedule**

### Rules of the road
- No private arrest details / mugshots of clients without explicit legal + brand approval.  
- Prefer educational + service posts over sensational “crime news.”  
- Check **Calendar** / **Queue** so two people don’t double-post.

---

## Chapter 7 — iMessage & Shannon (AI texting)

- Office texts run through **BlueBubbles** on the office iMac (**239-955-0178**).  
- **Shannon** can auto-reply with natural language.  
- If a client wants a **human**, take over from the inbox — never argue with “I’m a bot.”  
- Don’t paste SSNs or full card numbers into chat logs.

---

## Chapter 8 — Support & escalations

| Need | Contact |
|------|---------|
| Dashboard / leads | https://leads.shamrockbailbonds.biz |
| Client sign links | Paperwork tab or portal |
| Social | https://social.shamrockbailbonds.biz |
| School | https://school.shamrockbailbonds.biz |
| After-hours emergency | **(239) 334-2245** or Slack `#shamrock` |

**Escalate immediately if:**
- Two defendants could match one cosigner  
- Wrong person got a signing link  
- POA already used on another active bond  
- Payment shows for wrong case  
- You would need to invent a case number or power number to continue  

---

## Appendix — One-page “Write Bond” cheat sheet

```
1. Open lead → Write Bond
2. Confirm defendant + charges + amounts
3. Pick surety (OSI preferred in FL) → accept/check POA
4. Enter indemnitor (scan ID if possible)
5. Send DocuSeal packet (SMS / iMessage / iPad)
6. Collect premium (SwipeSimple / cash log)
7. Wait for signed → Drive auto-file
8. Active Bonds board → Active
```

**URLs**
- CRM: leads.shamrockbailbonds.biz  
- Portal: paperwork.shamrockbailbonds.biz  
- Sign: sign.shamrockbailbonds.biz  
- School: school.shamrockbailbonds.biz  
- Social: social.shamrockbailbonds.biz  
