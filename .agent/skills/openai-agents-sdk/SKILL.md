---
name: openai-agents-sdk
description: "Build multi-agent systems using the OpenAI Agents SDK. Use when designing, implementing, or refactoring any of our 9 AI agents (The Concierge, Shannon, The Clerk, The Analyst, The Investigator, The Closer, Manus Brain, The Watchdog, Bounty Hunter). Covers agent definitions, tool binding, handoffs, guardrails, and orchestration patterns."
source: "https://developers.openai.com/api/docs/guides/agents"
compatibility: Requires openai-agents Python package and OPENAI_API_KEY.
---

# OpenAI Agents SDK

## Overview

The Agents SDK provides a framework for building multi-agent systems with tool execution, specialist handoffs, guardrails, and state management. This directly maps to ShamrockLeads' 9-agent Digital Workforce.

## Installation

```bash
pip install openai-agents
export OPENAI_API_KEY=sk-...
```

## Core Concepts

### Agent Definition
```python
from agents import Agent, run

agent = Agent(
    name="The Analyst",
    instructions="You are The Analyst for Shamrock Bail Bonds. Score every arrest lead 0-100 based on bond amount, bond type, custody status, and data completeness.",
    model="gpt-4o-mini",
)

result = await run(agent, input_data)
print(result.final_output)
```

### Tools
```python
from agents import Agent, run, tool
from pydantic import BaseModel

class ScoreInput(BaseModel):
    bond_amount: float
    bond_type: str
    custody_status: str

@tool
def score_lead(input: ScoreInput) -> dict:
    """Score an arrest lead for bail bond qualification."""
    score = 0
    if 500 <= input.bond_amount <= 50000:
        score += 30
    if input.bond_type in ["Cash", "Surety"]:
        score += 25
    if input.custody_status == "In Custody":
        score += 20
    return {"score": score, "status": classify_score(score)}
```

### Multi-Agent Orchestration (Handoffs)
```python
from agents import Agent

# Specialist agents
clerk = Agent(
    name="The Clerk",
    instructions="Parse jail roster HTML into structured ArrestRecord JSON.",
)

analyst = Agent(
    name="The Analyst",
    instructions="Score each ArrestRecord for bail bond lead qualification.",
)

# Triage agent routes to specialists
triage = Agent(
    name="Intake Router",
    instructions="Route incoming data to the correct specialist agent.",
    handoffs=[clerk, analyst],
)
```

### Guardrails
```python
from agents import Agent

agent = Agent(
    name="The Concierge",
    instructions="""You are The Concierge for Shamrock Bail Bonds.
    
    GUARDRAILS:
    - Never provide legal advice
    - Never guarantee bond approval
    - Never share PII of other clients
    - Always recommend consulting an attorney for legal questions
    - Escalate to human when: bond > $100K, violent felony, or client distress
    """,
)
```

## ShamrockLeads Agent Mapping

| Agent | SDK Pattern | Primary Tool |
|-------|------------|-------------|
| The Concierge | Single agent + tools | FAQ lookup, intake form, schedule callback |
| Shannon | Voice agent (ElevenLabs) | send_paperwork, capture_lead |
| The Clerk | Tool-heavy agent | parse_booking, extract_charges |
| The Analyst | Scoring agent | score_lead, classify_risk |
| The Investigator | Research agent | background_check, contact_discovery |
| The Closer | Outreach agent | draft_message, schedule_followup |
| Manus Brain | Conversational agent | intent_classification, response_generation |
| The Watchdog | Monitoring agent | check_health, send_alert |
| Bounty Hunter | Filter agent | filter_high_value, surface_leads |

## Key Patterns

### State Management
```python
# Keep full history for multi-turn conversations
result = await run(agent, user_input)
# result.history contains the full conversation
# result.last_agent tracks which specialist handled it
```

### Results
```python
result = await run(agent, input_data)
print(result.final_output)    # The agent's response
print(result.history)          # Full run history
print(result.last_agent.name)  # Which agent responded
```

## Reading Order

1. [Quickstart](https://developers.openai.com/api/docs/guides/agents/quickstart)
2. [Agent Definitions](https://developers.openai.com/api/docs/guides/agents/define-agents)
3. [Models and Providers](https://developers.openai.com/api/docs/guides/agents/models)
4. [Running Agents](https://developers.openai.com/api/docs/guides/agents/running-agents)
5. [Orchestration and Handoffs](https://developers.openai.com/api/docs/guides/agents/orchestration)
6. [Guardrails and Human Review](https://developers.openai.com/api/docs/guides/agents/guardrails-approvals)
7. [Results and State](https://developers.openai.com/api/docs/guides/agents/results)
8. [Voice Agents](https://developers.openai.com/api/docs/guides/voice-agents) (for Shannon)

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `OPENAI_API_KEY` | ✅ | OpenAI API authentication |
| `OPENAI_MODEL` | Optional | Default model (gpt-4o-mini) |
