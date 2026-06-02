---
name: ai-decision-tracer
description: Workflow for debugging LLM decisions by tracing inputs and confidence scores.
---

# ai-decision-tracer

## Mission
You help developers trace the "thought process" of our digital workforce (Shannon, The Analyst, etc.).

## Directives
1. **Trace Logs**: All LLM calls must log their `prompt`, `temperature`, and `raw_completion` to the DB for debugging.
2. **Score Explainability**: When The Analyst assigns a lead score, the rationale must be stored as an array of applied rules.
3. **Identify Hallucinations**: When a failure occurs, trace the exact prompt and identify missing context or context window overflow.

## When to use
When debugging why an AI agent made an incorrect decision, hallucinatory output, or parsing failure.
