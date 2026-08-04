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
    → Match lead + load Mongo thread history (~20 msgs for phone+booking)
    → Mem0 search (long-term facts for last-10 phone digits — shared with voice Shannon)
    → OpenAI GPT-4o-mini with history + KNOWN FACTS block
    → Response sent via BlueBubbles iMessage (human gates unchanged)
    → Conversation logged to Mongo + Mem0 remember_exchange
```

### Mem0 long-term memory

| Item | Detail |
|------|--------|
| Env | `MEMO_API_KEY` (GAS name) or `MEM0_API_KEY` |
| Same project as | GAS `ElevenLabs_WebhookHandler.js` → `saveMem0Memory_` |
| `user_id` | Last **10** phone digits (cross-channel with voice calls) |
| Fail mode | Missing key / API error → current Mongo-only behavior |
| Status | `GET /api/agent-brain/memory/status` |
| Service | `dashboard/services/mem0_service.py` |

**Ops:** Copy the Mem0 key from GAS Script Properties (`MEMO_API_KEY`) into Hetzner / leads `.env`. Do not commit the key.

---

## Key Files

| File | Purpose |
|------|---------|
| `dashboard/routers/agent_brain.py` | Core AI agent logic + Mem0 inject/store |
| `dashboard/routers/agent_brain_api.py` | Agent API endpoints + memory status |
| `dashboard/services/mem0_service.py` | Mem0 REST client (httpx) |
| `dashboard/routers/bb_webhook_receiver.py` | Incoming message handler |
| `dashboard/services/bb_client.py` | BlueBubbles message sending |
| `dashboard/routers/imessage_automation.py` | Automation rules |
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
