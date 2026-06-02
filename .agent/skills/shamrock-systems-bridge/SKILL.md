---
name: shamrock-systems-bridge
description: Establishes patterns for building and interacting with MCP servers and external APIs.
---

# shamrock-systems-bridge

## Mission
You build and standardize integrations with external systems via MCP and APIs.

## Directives
1. **Secure Access**: All MCP servers must authenticate securely and never expose raw API keys to the context window unless requested.
2. **Idempotent Actions**: External tools (like sending a Twilio SMS or triggering a SignNow template) must have idempotency keys to prevent double-billing.
3. **Graceful Degradation**: If an external API (like BlueBubbles) is down, the system must queue the action and retry, not crash.

## When to use
When building or interacting with MCP servers, Twilio, Slack, SignNow, SwipeSimple, or BlueBubbles.
