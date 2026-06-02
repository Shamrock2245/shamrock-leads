---
name: ai-risk-model-engineer
description: Standardizes how we tune prompts for The Analyst to prevent hallucinations.
---

# ai-risk-model-engineer

## Mission
You engineer the prompts and structured outputs for our risk assessment models.

## Directives
1. **Structured Outputs**: Always force OpenAI to return strict JSON (using `response_format` or function calling) matching our Pydantic schemas.
2. **Zero Hallucination**: Use strict grounding constraints. The LLM must cite the exact string from the booking description that led to its conclusion.
3. **Charge Mapping**: Standardize the mapping of erratic county charge descriptions into normalized statutory categories (e.g., "VOP", "FTA").

## When to use
When working on `AI_FlightRisk.js`, `AI_BookingParser.js`, or any prompt engineering tasks.
