---
name: elevenlabs-agents
description: "Build and configure ElevenLabs Conversational AI voice agents. Use when creating, modifying, or debugging Shannon (our after-hours voice agent) or any ElevenLabs voice workflow. Covers agent creation, tool integration (webhook/client/system), widget embedding, and voice selection."
source: "https://github.com/elevenlabs/skills (agents/SKILL.md)"
compatibility: Requires ElevenLabs API key and Conversational AI access.
---

# ElevenLabs Conversational AI Agents

## Overview

Build real-time voice agents using ElevenLabs' Conversational AI platform. This is the backbone of **Shannon** — our 24/7 after-hours voice intake agent.

## Shannon Configuration (ShamrockLeads)

- **Agent Name**: Shannon
- **Role**: After-hours voice intake
- **Channel**: Phone (inbound calls)
- **Backend**: Netlify Edge Function proxy → GAS webhook
- **Actions**: Capture lead info, send SignNow links via SMS during calls

## Agent Creation

### Via API
```python
import requests

url = "https://api.elevenlabs.io/v1/convai/agents/create"
headers = {
    "xi-api-key": "YOUR_API_KEY",
    "Content-Type": "application/json"
}
payload = {
    "name": "Shannon",
    "conversation_config": {
        "agent": {
            "prompt": {
                "prompt": "You are Shannon, the after-hours receptionist for Shamrock Bail Bonds...",
            },
            "first_message": "Thank you for calling Shamrock Bail Bonds. My name is Shannon. How can I help you today?",
            "language": "en"
        },
        "tts": {
            "voice_id": "YOUR_VOICE_ID"
        }
    }
}
response = requests.post(url, json=payload, headers=headers)
```

## Tool Types

### 1. Webhook Tools (Server-side)
Call external APIs during conversation:
```json
{
    "type": "webhook",
    "name": "send_paperwork",
    "description": "Send SignNow paperwork link to the caller via SMS",
    "api_schema": {
        "url": "https://shamrock-telegram.netlify.app/api/gas-proxy",
        "method": "POST"
    }
}
```

### 2. Client Tools (Frontend-side)
Execute in the client's browser/app context.

### 3. System Tools (Built-in)
- `end_call` — Terminate the conversation
- `transfer_call` — Transfer to human agent

## Widget Embedding

```html
<elevenlabs-convai agent-id="YOUR_AGENT_ID"></elevenlabs-convai>
<script src="https://elevenlabs.io/convai-widget/index.js" async></script>
```

## Key Principles

1. **Netlify Edge Function proxy**: Required because GAS returns 302 redirects that break direct webhook calls
2. **Voice selection**: Choose voices that sound professional and warm — Shannon uses a specific female voice ID
3. **First message**: Always professional, state the company name immediately
4. **Tool calls during conversation**: Shannon can trigger SMS sends and GAS actions mid-call
5. **Fallback**: If voice agent fails, provide office number and business hours

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `ELEVENLABS_API_KEY` | ElevenLabs API authentication |
| `ELEVENLABS_AGENT_ID` | Shannon's agent ID |
| `NETLIFY_PROXY_URL` | Edge function proxy for GAS calls |
