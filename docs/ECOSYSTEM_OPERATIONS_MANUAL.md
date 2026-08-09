# Shamrock Bail Bonds — Ecosystem Master Operations Manual (SOP)

> **Last Updated:** 2026-08-09  
> **Target Audience:** Agency Staff, Licensed Bondsmen, Office Personnel, and AI Digital Workforce  
> **Master URL:** `https://leads.shamrockbailbonds.biz/guide`  

---

## 🧭 Executive Overview: The 7 Surfaces of Shamrock

Shamrock Bail Bonds operates on a unified **7-Surface Digital Ecosystem**. The core principle of the agency is: **"The Website is a Clipboard; The Backend is the Brain."**

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           SHAMROCK DIGITAL ECOSYSTEM                             │
├──────────────────────┬──────────────────────┬───────────────────────────────────┤
│ Surface              │ Domain / Channel     │ Purpose                           │
├──────────────────────┼──────────────────────┼───────────────────────────────────┤
│ 1. Public Portal     │ shamrockbailbonds.biz│ Client intake, magic link, chat   │
│ 2. Shannon Voice AI  │ 239-955-0178 (Phone) │ 24/7 Phone intake & paperwork SMS │
│ 3. ShamrockLeads CRM │ leads.shamrock...    │ Arrest intelligence & bond ops    │
│ 4. DocuSeal E-Sign   │ sign.shamrock...     │ 14-doc mobile e-signature packet  │
│ 5. Active Bond Desk  │ Kanban / Traccar GPS │ 7-status lifecycle & check-ins    │
│ 6. iMessage Bridge   │ BlueBubbles iMac     │ Direct SMS/iMessage drip outreach │
│ 7. Postiz Social Hub │ social.shamrock...   │ Multi-platform social media manager│
└──────────────────────┴──────────────────────┴───────────────────────────────────┘
```

---

## 📖 Chapter 1: ShamrockLeads Auto-CRM (`leads.shamrockbailbonds.biz`)

### 1.1 Logging In & Navigating
* **Access**: Navigate to `https://leads.shamrockbailbonds.biz`.
* **PIN Authentication**: Enter your assigned agency PIN (e.g. `2245`).
* **Keyboard Shortcuts**:
  * `F1`: Open/Close the **Contextual Help Drawer**.
  * `Ctrl + K` (or `Cmd + K`): Open Omnibar Search across all arrest records, cases, and defendants.
  * `Esc`: Close any open modal or drawer.

### 1.2 Lead Explorer & Scoring Engine
* **Every arrest is scored 0–100** automatically upon scraping:
  * 🔥 **Hot Leads (80–100)**: Immediate Slack alert to `#leads` + queued for iMessage outreach.
  * 🟡 **Warm Leads (50–79)**: Stored in DB, medium-priority follow up.
  * ❄️ **Cold / Disqualified (<50)**: $0 bond, ROR, or capital/federal charges.
* **Filtering Data**: Use the top bar to filter by **State** (FL, GA, SC, NC, TN, TX, LA, AL, CT, MS) or specific **County**.

---

## 📜 Chapter 2: Bond Creation & Appearance Bonds

### 2.1 Selecting a Defendant
1. Click **➕ Record Bond** or click any arrest record row in Lead Explorer.
2. Type the defendant's name in the search bar.
3. Click the matching defendant record.

### 2.2 Auto-Completion Workflow
When selected, the system automatically populates:
* Full Name, Phone, Address, DOB, Booking Number, County, Facility, Case Number.
* Court Date, Court Time, and Court Location.
* **Statutory 10% Florida Premium**: Auto-calculates 10% of total bond amount with the **$100 statutory minimum per charge**.
* **Sequential POA Suggestion**: Queries live inventory for the selected surety (`OSI` or `Palmetto`) and reserves the next available power number.
* **Per-Charge Breakdown**: Hydrates rows 1 through 4 (`offense_1` to `_4`, `case_number_1` to `_4`, `poa_number_1` to `_4`, `bond_amount_1` to `_4`).

---

## ✍️ Chapter 3: DocuSeal E-Signature & Document Archiving

### 3.1 Generating a Packet
1. Once the bond form is filled, click **Generate Paperwork Packet**.
2. Select e-sign provider: **DocuSeal** (`https://sign.shamrockbailbonds.biz`).
3. The system generates Template ID `1` (OSI 14-document packet).
4. Send the signing link to the indemnitor via SMS, iMessage, or WhatsApp.

### 3.2 Mobile Signing Experience
* Indemnitor opens link on mobile → uploads Government ID → system OCR extracts DL data → Indemnitor signs.
* Upon signature completion, DocuSeal fires a webhook back to ShamrockLeads.
* The signed PDF is automatically uploaded to Google Drive formatted as:  
  `<LastName>_<MMDDYY>_<SURETY>.pdf` (e.g. `Doe_080926_OSI.pdf`).

---

## 📲 Chapter 4: Postiz for Normal People — Complete Social Media Guide

> **Domain:** `https://social.shamrockbailbonds.biz`  
> **Purpose:** Manage all Shamrock Bail Bonds social media accounts in one place without needing technical skills.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                      POSTIZ SOCIAL MANAGER — QUICK FLOW                         │
├─────────────────────────────────────────────────────────────────────────────────┤
│ 1. Connect Channels ──► 2. Create Post ──► 3. Upload Media ──► 4. Schedule/Publish│
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 4.1 Step 1: Connecting Social Media Accounts
Before posting, ensure your social media accounts are connected:
1. Log into `https://social.shamrockbailbonds.biz`.
2. Click **Integrations** on the left menu.
3. Click **Connect** next to the platform you want to add:
   * 📘 **Facebook**: Connects to the official Shamrock Bail Bonds Facebook Page.
   * 📸 **Instagram**: Connects to `@shamrock_bail_bonds`.
   * 🪶 **X / Twitter**: Connects to `@ShamrockBail_FL`.
   * 📺 **YouTube**: Connects to `@Shamrock2245`.
   * 📍 **Google My Business**: Connects to Ft. Myers office listing.
4. Follow the prompt to authorize Postiz. A green checkmark ✅ will appear when connected.

### 4.2 Step 2: Creating Your First Social Post
1. Click **Create Post** (top left button or pencil icon).
2. **Select Social Channels**: Click the icons at the top of the post editor (e.g., select Facebook + Instagram + X).
3. **Write Your Message**:
   * Keep text engaging, professional, and helpful.
   * Example: *"In custody in Lee County? Shamrock Bail Bonds is open 24/7 at 1528 Broadway, Ft. Myers. Call (239) 334-2245 or start online in 60 seconds."*
4. **Hashtags**: Add 3–5 relevant local hashtags (e.g. `#BailBonds #FortMyers #LeeCounty #ShamrockBailBonds`).

### 4.3 Step 3: Media & Image Rules
* **Images**: Click **Add Media** to upload a PNG or JPG photo.
  * *Recommended dimension*: 1080x1080px (Square) or 1080x1350px (Portrait).
* **Videos**: Keep video files under 100MB in `.mp4` format.
* **Preview**: Look at the right side of the screen to see exactly how your post will look on mobile phones before publishing!

### 4.4 Step 4: Scheduling vs. Publishing Now
You have two choices when finished writing:
* 🚀 **Publish Now**: Click **Post Now** to send the message immediately to all selected networks.
* 📅 **Schedule**: Click **Schedule**, pick a date and time (e.g. Tomorrow at 9:00 AM), and click **Schedule Post**.
* View all upcoming scheduled posts anytime by clicking **Calendar** on the left menu!

### 4.5 Step 5: Automated AI Posting (Postiz MCP)
* Our system is connected to **Postiz MCP** (`https://social.shamrockbailbonds.biz/api/mcp`).
* Our AI agents automatically generate localized safety updates, county jail reports, and bail education posts without staff needing to type them manually!
* You can review AI-drafted posts anytime in the **Queue** tab.

---

## 🔄 Chapter 5: Active Bond Kanban & Compliance

### 5.1 The 7 Lifecycle Statuses
1. **Active**: Bond posted, defendant released, check-ins current.
2. **Monitoring**: High-value bond or court date within 7 days.
3. **Alert**: Missed check-in or failed contact attempt.
4. **Exonerated**: Case closed by court. **POA automatically released back to available inventory**.
5. **Forfeited**: Defendant missed court (FTA). Destructive action modal required.
6. **Surrendered**: Defendant surrendered. POA automatically released.
7. **Reinstated**: Court set aside forfeiture.

### 5.2 Client Check-Ins & Traccar GPS
* Defendants receive automated weekly check-in SMS.
* Self-service portal captures photo + GPS coordinates.
* Live vehicle/GPS trackers stream to `shamrock-traccar` (port 8082).

---

## 💬 Chapter 6: iMessage Outreach & AI Agent (Shannon)

* **BlueBubbles iMessage Bridge**: Connects to office iMac (`239-955-0178`).
* **Shannon (AI Agent)**: Handles incoming text inquiries with natural language responses using GPT-4o and Mem0 long-term memory.
* **Human Takeover**: If a client asks for a licensed bondsman, staff can take over the conversation directly from the dashboard inbox.

---

## 🛠 Support & Emergency Escalation

* **Dashboard URL**: `https://leads.shamrockbailbonds.biz`
* **DocuSeal Portal**: `https://sign.shamrockbailbonds.biz`
* **Social Hub**: `https://social.shamrockbailbonds.biz`
* **After-Hours Emergency Support**: Call (239) 334-2245 or alert `#shamrock` on Slack.
