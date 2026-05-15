# Outreach Agent — "The Closer"

> **Status:** `[IMPLEMENTED]`
> **Implementation:** `dashboard/api/outreach.py`, `dashboard/services/outreach_sequencer.py`, `dashboard/services/bb_client.py`

---

## Role

The Closer orchestrates automated iMessage outreach sequences to potential indemnitors (family/friends of arrestees). It sends human-feel messages via BlueBubbles bridge to the office iMac, managing drip campaigns with configurable delays and escalation paths.

---

## Pipeline

```
Contact Discovered (OSINT)
    → Outreach Sequence Created
        → Message 1: Introduction (immediate)
        → Message 2: Follow-up (4h delay)
        → Message 3: Urgency nudge (24h delay)
        → Message 4: Final attempt (48h delay)
    → Response Detected → Shannon AI auto-reply
    → No Response → Archive
```

---

## Key Files

| File | Purpose |
|------|---------|
| `dashboard/api/outreach.py` | Outreach API endpoints |
| `dashboard/services/outreach_sequencer.py` | Drip campaign state machine |
| `dashboard/services/bb_client.py` | BlueBubbles REST client |
| `dashboard/api/bb_prospecting.py` | iMessage-first prospecting |
| `dashboard/api/bb_scheduled_messages.py` | Scheduled message delivery |
| `dashboard/api/agent_brain.py` | Shannon AI auto-reply |
| `dashboard/sl-prospective.js` | Outreach Kanban frontend |

---

## Human-in-the-Loop

- **No automated outreach without human approval** (Prime Directive #6)
- All message templates are pre-approved
- Shannon AI auto-replies are logged for human review
- Outreach sequences can be paused/resumed manually

---

## Constraints

- iMessage only (BlueBubbles bridge) — SMS fallback via Twilio if iMessage fails
- Rate-limited: max 1 message per contact per 4 hours
- DNB/DNC flags respected — never contact flagged defendants/numbers
- All outreach logged in `outreach_sequences` collection
- PII never exposed in message templates
