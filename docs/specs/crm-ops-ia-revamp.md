# CRM Ops IA Revamp — Bond Desk First

> **Status:** Design approved for implementation · Twenty-inspired · Shamrock terminology  
> **Date:** 2026-07-24  
> **Problem:** Outreach + Intake Queue are redundant; sidebar is a feature dump; bond writing path is unclear.

---

## 1. Diagnosis

| Pain | Cause |
|------|--------|
| Outreach vs Intake confusion | Two parallel “work the lead” surfaces (`tabProspective` + `tabIntake`) with overlapping stages |
| Slow ops | Sidebar has 20+ equal-weight tabs; primary bond path is not top-of-nav |
| Paperwork feels disconnected | Packet hydration lives in services; Bond Desk does not surface **required autofill checklist** |
| Active Bonds used too early | Agents promote before match + signatures + payment |

**North star (user):**

> Intake Queue is how the bond is done for the defendant.  
> Once an indemnitor is paired and all paperwork is completed, the case goes to **Active Bonds**.

---

## 2. Inspiration from Twenty CRM

Mapped from [twentyhq/twenty](https://github.com/twentyhq/twenty) product patterns (not a clone of UI chrome):

| Twenty pattern | Shamrock equivalent |
|----------------|---------------------|
| **Sidebar folders** (Sales / Ops / Settings) | Group nav by *job*, not by feature age |
| **Favorites + primary objects first** | Bond Desk objects above Intelligence/Integrations |
| **Opportunities Kanban** | **Bond Desk** stages (pending → match → paperwork → ready) |
| **Record page tabs/widgets** | One case surface: defendant · indemnitor · charges · money · packet · timeline |
| **Command menu (⌘K)** | Existing Omnibar — keep as universal jump |
| **Side panel peek** | Quick peek on row click (later phase) |
| **Workflows on stage change** | Lifecycle automations: Slack, SignNow, court reminders, POA release |
| **Hide unused objects** | Collapse Intelligence / Integrations under “More” |
| **Closed-won automation** | Paperwork complete + payment logged → **create Active Bond** |

Twenty’s rule of thumb: *one primary pipeline object, many supporting objects.*  
Ours is **Bond Case in writing** (Intake / Bond Desk), not “Outreach.”

---

## 3. Canonical ops flow (single chain)

```
Jail roster scrape
    → Hot Leads (score / custody / bond $)
        → Contact cosigners (iMessage / Shannon — action, not a nav destination)
            → Bond Desk (Intake)
                1. New intake (Wix / phone / walk-in / manual)
                2. Match defendant (human gate if ambiguous)
                3. Indemnitor verified (PII for packet)
                4. Surety + POA assigned
                5. Packet generated & SignNow sent
                6. Signatures + premium
                7. PROMOTE → Active Bonds
                    → Kanban: active · monitoring · alert · exonerated / forfeited / surrendered
```

**Rules (unchanged from AGENTS.md, made UI-visible):**

1. No paperwork without validated match + bonded case fields (surety, case #, POA).
2. No Active Bond until packet complete (or explicit human override with audit note).
3. Outreach sequences are **actions on a lead**, not a parallel CRM home.

---

## 4. New sidebar IA

### 4.1 Primary — Bond Desk (daily work)

| Nav label | Tab ID | Role |
|-----------|--------|------|
| **Today** | `tabCommand` | Command Center — KPIs, tasks due, hot counts |
| **Hot Leads** | `tabLeads` | Arrest intelligence / scoreboard (rename label only) |
| **Bond Desk** | `tabIntake` | **Write the bond** — intake → match → packet → promote |
| **Active Bonds** | `tabActiveBonds` | Posted bonds lifecycle Kanban |
| **Court Calendar** | `tabCalendar` | Appearances, reminders |

### 4.2 People

| Nav label | Tab ID |
|-----------|--------|
| Defendants | `tabDefendants` |
| Indemnitors | `tabIndemnitor` |

### 4.3 Money & Risk

| Nav label | Tab ID |
|-----------|--------|
| Revenue | `tabAnalytics` |
| Accounting | `tabAccounting` |
| FTA Alerts | `tabFTA` |
| Tracking | `tabTracking` |

### 4.4 Workspace (secondary)

| Nav label | Tab ID / action |
|-----------|-----------------|
| iMessage | `tabImessage` |
| POA Inventory | modal `SLInventory.open()` |
| Automations | `tabAutomations` |
| Paperwork Config | `tabPaperwork` |
| Reports | `tabReports` |
| Client Portal | `tabPortal` |

### 4.5 Intelligence (collapsed by default)

Multi-State Ops, Bond Intelligence, Scraper Health, OSINT, Enrichment, Alpha Intel, Legal NLP, Social, Intelligence.

### 4.6 Demote / absorb Outreach

| Change | Detail |
|--------|--------|
| Remove top-level **Outreach** from primary nav | Moves under Workspace as **Lead Pipeline** (power-user / legacy) |
| Preferred contact path | Hot Leads row → “Message” / sequence, or Bond Desk detail → communications widget |
| Prospective stages | Treated as optional pre-intake; **do not** promote to Active Bonds from Outreach |

Badge: review-queue count can surface on Bond Desk or iMessage instead of a competing home.

---

## 5. Bond Desk stages (Twenty Kanban applied to Intake)

Replace vague pending/in_progress mental model with explicit bond-writing stages:

| Stage | Meaning | Exit criteria |
|-------|---------|---------------|
| `new` | Intake landed | Agent claims |
| `matching` | Finding defendant | Match validated |
| `indemnitor` | Cosigner PII complete | Fields for packet green |
| `underwriting` | Surety + premium + POA tier | POA reserved |
| `paperwork` | SignNow packet out | All required docs signed |
| `payment` | Premium / plan | Payment logged or plan created |
| `ready` | Ready to post | Agent confirms posted |
| → **Active Bonds** | Posted | `active_bonds` record + audit |

Legacy statuses `pending` / `in_progress` / `promoted` map into these stages without a hard DB migration on day one (UI labels + filters first).

---

## 6. Paperwork autofill contract (what Bond Desk must collect)

Source of truth for hydration: `dashboard/services/signnow_packet_service.py` → `_build_prefill_fields`.

### Packet docs (`templates/blanks/` + SignNow)

| Phase | Documents |
|-------|-----------|
| Phase 1 (cosigner) | Header, FAQ cosigners, Indemnity Agreement, Promissory Note, Disclosure, SSA Release, Master Waiver |
| Phase 2 (defendant / post-POA) | FAQ defendants, Defendant Application, Surety Terms, Master Waiver, SSA, Collateral Receipt, Payment Plan |
| Surety variants | OSI vs Palmetto blanks for appearance bond, indemnity, defendant app, collateral, payment plan, surety terms |

### Autofill field groups (must be green before “Send packet”)

**Defendant**

- Full name (first / middle / last)
- DOB, sex, race, height, weight, hair, eyes
- Address, city, state, zip, phone, email
- DL # + state
- Employer + phone + address
- Booking #, county, facility, arrest date
- Charges, case #, court date / time / location
- Bond amount → premium (10% default math)

**Indemnitor (cosigner)**

- Full name, DOB, SSN (SSA release), relation
- Address, city, state, zip, phone, email
- DL # + state
- Employer + phone + address
- Vehicle make / model / year / color
- Reference 1 & 2: name, phone, relation, address

**Bond / agency (system)**

- Surety ID (`osi` \| `palmetto`)
- POA number (phase 2)
- Agent name, license, agency name/phone/address
- Receipt / intake ID, dates

**Naming conventions already multi-mapped:** kebab-case, PascalCase, `Ind*`, `Def*` — keep sending all variants.

### Bond Desk UI: “Packet readiness”

Show a Twenty-style checklist widget on the intake record:

```
☐ Defendant identity
☐ Booking + charges
☐ Bond $ / premium
☐ Indemnitor identity + contact
☐ Indemnitor DL / SSN (if required docs)
☐ Match validated
☐ Surety selected
☐ POA assigned
→ [Generate & Send Packet]
```

Missing fields block send (fail closed); override requires note + audit.

---

## 7. Automations to port (Twenty workflow → bail ops)

| Trigger | Actions |
|---------|---------|
| Hot lead score ≥ 80 | Slack `#leads`; optional sequence draft in review queue |
| Intake created (Wix/Telegram) | Bond Desk badge++; notify agent |
| Match validated | Unlock surety/POA step |
| POA assigned | Enable phase-2 docs |
| All signatures complete | Prompt payment step |
| Payment logged + docs signed | **Promote → Active Bond**; start court reminder workflow |
| Status → exonerated/forfeited/surrendered | POA release + accounting hooks |
| Stale Bond Desk card (N days no update) | Digest / task (Twenty “stale opportunity”) |

---

## 8. Implementation phases

### Phase A — IA only ✅ done

1. Restructure sidebar groups + labels in `dashboard/index.html`
2. Collapse Intelligence under expandable “More”
3. Demote Outreach to Workspace → “Lead Pipeline”
4. Rename Intake Queue → **Bond Desk**
5. CSS polish in `sl-twenty-ux.css` for collapsible nav groups
6. Update `docs/SUPER_CRM.md` capability map

### Phase B — Bond Desk as single write surface ✅ in progress

1. ✅ Embed Match / Write packet / Promote in Bond Desk modal (no hop to Outreach)
2. ✅ Packet readiness checklist from prefill field groups
3. ✅ Promote gated on required readiness (override with confirm)
4. ✅ Lead Pipeline banner: “not the bond desk”
5. ⬜ Soft-require SignNow signed status before promote (ops preference)
6. ⬜ Inline field edit without leaving modal

### Phase C — Absorb Outreach

1. Move sequence controls into Hot Leads + Bond Desk
2. Hide Lead Pipeline nav behind feature flag after 2 weeks of dual-run
3. Migrate prospective stages into pre-intake or archive collection

### Phase D — Record page (Twenty record layout)

1. Unified case page: tabs for Overview · People · Packet · Money · Court · Timeline · Messages
2. Side panel peek from any table row

---

## 9. Non-goals

- Full React rewrite / embedding Twenty runtime
- Changing Mongo identity model (`arrests` / `intake_queue` / `active_bonds`)
- New GAS Web App URL
- Auto-matching without human gate on ambiguity

---

## 10. Success metrics

| Metric | Target |
|--------|--------|
| Time from intake open → packet send | ↓ |
| Wrong-tab escalations (“which queue?”) | Near zero |
| Active bonds created without signed packet | 0 (except audited override) |
| Primary nav items visible without scroll | ≤ ~12 |

---

## 11. File touch list (Phase A)

- `dashboard/index.html` — sidebar markup
- `dashboard/sl-twenty-ux.css` — collapsible groups
- `dashboard/sl-core.js` — optional group toggle helpers
- `docs/SUPER_CRM.md` — capability map
- `docs/specs/crm-ops-ia-revamp.md` — this doc
