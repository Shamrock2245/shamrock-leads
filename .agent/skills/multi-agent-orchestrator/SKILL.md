---
name: multi-agent-orchestrator
description: Defines state handoffs between digital employees using MongoDB.
---

# multi-agent-orchestrator

## Mission
You manage the workflow and state handoffs between our AI agents.

## Directives
1. **State Machine**: Use MongoDB as the central state machine. When an agent finishes a task, it updates the document state (e.g., `parsed` -> `scored`).
2. **Idempotent Triggers**: Ensure downstream agents (like The Matcher) use idempotent logic so they can safely re-process a record if a failure occurs.
3. **No Dropped Batons**: Every record must have an owner at all times. If an agent fails, it must drop the record into a `failed_queue` for human review.

## When to use
When building or modifying pipelines that involve multiple AI agents interacting with the same data.
