# Shannon — AI Auto-Reply Agent

> **Status:** `[IMPLEMENTED]`
> **Implementation:** `dashboard/api/agent_brain.py`, `dashboard/api/agent_brain_api.py`

---

## Role

Shannon is the AI-powered auto-reply agent that handles incoming iMessage conversations from potential clients. She responds naturally and professionally, qualifying leads, answering bail bond questions, and routing hot prospects to human bondsmen.

---

## How It Works

```
Incoming iMessage (via BlueBubbles webhook)
    → Message context loaded (last 10 messages in thread)
    → OpenAI GPT-4o generates contextual response
    → Response sent via BlueBubbles iMessage
    → Conversation logged for human review
```

---

## Key Files

| File | Purpose |
|------|---------|
| `dashboard/api/agent_brain.py` | Core AI agent logic |
| `dashboard/api/agent_brain_api.py` | Agent API endpoints |
| `dashboard/api/bb_webhook_receiver.py` | Incoming message handler |
| `dashboard/services/bb_client.py` | BlueBubbles message sending |
| `dashboard/api/imessage_automation.py` | Automation rules |
| `dashboard/sl-imessage.js` | iMessage control center frontend |

---

## Personality

- Professional but warm
- Knowledgeable about Florida bail bond process
- Never provides legal advice
- Always directs to office for complex questions
- Uses Shamrock branding consistently

---

## Safety Rules

- **Human override**: Any message can be flagged for human review
- **Auto-reply toggle**: Can be disabled globally or per-conversation
- **PII protection**: Never reveals internal system details
- **Escalation**: Complex legal questions auto-escalate to human
- **Rate limiting**: Max 1 auto-reply per 60 seconds per conversation
