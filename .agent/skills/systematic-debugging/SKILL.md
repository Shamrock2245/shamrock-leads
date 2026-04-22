---
name: systematic-debugging
description: A rigorous, step-by-step process for solving complex technical issues without guessing. Adapted from obra/superpowers for ShamrockLeads.
version: 1.0.0
source: https://skills.sh/obra/superpowers/systematic-debugging
---

# Systematic Debugging

## Overview
Random fixes waste time and create new bugs. Quick patches mask underlying issues.

**Core principle:** ALWAYS find root cause before attempting fixes. Symptom fixes are failure.

## The Iron Law
```
NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST
```

If you haven't completed Phase 1, you cannot propose fixes.

## When to Use
Use for ANY technical issue:
- Scraper failures or empty results
- MongoDB connection issues
- Lead scoring inaccuracies
- Docker container crashes
- Slack notification failures
- Unexpected data in records

**Use this ESPECIALLY when:**
- Under time pressure (emergencies make guessing tempting)
- "Just one quick fix" seems obvious
- You've already tried multiple fixes
- Previous fix didn't work

## The Four Phases

### Phase 1: Root Cause Investigation
**Goal**: Identify the exact line of code, configuration, or data causing the issue.
**Output**: A confirmed hypothesis backed by evidence (logs, reproduction).

1. **Reproduce**: Can you make it fail consistently? If not, add logging first.
2. **Isolate**: Does it happen for all counties or just one? With mocked data?
3. **Trace**: Follow the execution path. `docker logs`, `--county X --once`, stack traces.

### Phase 2: Pattern Analysis
**Goal**: Understand WHY the error happened, not just WHERE.

1. **History**: Has this code or the county's roster site changed recently?
2. **Similarities**: Are other scrapers using the same JMS vendor also broken?
3. **Assumptions**: What assumption turned out to be false?

### Phase 3: Hypothesis and Testing
**Goal**: Propose a fix and PROVE it works before deploying.

1. **Propose**: Design the fix.
2. **Verify**: `python main.py --county <name> --once` — does it produce correct records?

### Phase 4: Implementation
**Goal**: Apply the fix and prevent regression.

1. **Apply**: Write the code.
2. **Test**: Run verification.
3. **Future-proof**: Add the failure pattern to `scraper-debugger` knowledge base.

## Red Flags — STOP and Follow Process
- "It should work." (It doesn't.)
- "I'll just try changing this." (Guessing.)
- "That's impossible." (Reality disagrees.)
- "It works locally." (The server environment differs.)
