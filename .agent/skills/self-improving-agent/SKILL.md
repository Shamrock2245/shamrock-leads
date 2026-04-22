---
name: self-improving-agent
description: Enables the agent to learn from every session — log lessons, create skills from patterns, maintain knowledge base. Adapted from charon-fan/agent-playbook.
source: https://skills.sh/charon-fan/agent-playbook/self-improving-agent
---

# Self-Improving Agent

> The agent that learns from every session and gets better at its job.

## Purpose
1. **Log lessons learned** from each session into a persistent knowledge base
2. **Create new micro-skills** when repeatable patterns are discovered
3. **Update existing skills** when better approaches are found
4. **Track scraper-specific metrics** — success rates, new failure modes

## When to Use
- At the END of every significant session
- When discovering a new scraping pattern that could be reused
- When a workaround is found for a recurring problem
- When the agent makes an error and wants to prevent it in the future

## Self-Improvement Workflow

### Step 1: Session Retrospective
After completing a task, generate:

```markdown
## Session Retrospective — [DATE]

### What Worked
- Techniques, scraping patterns, and tools that were effective

### What Didn't Work
- Failed approaches, dead ends, wrong vendor assumptions

### Lessons Learned
- Actionable insights (e.g., "Hendry County requires 2s delays")

### Patterns Discovered
- Repeatable patterns that could become skills (e.g., "JailTracker CAPTCHA bypass")

### Knowledge Gaps
- Areas needing more investigation (e.g., "Miami-Dade has custom API")
```

### Step 2: Pattern Detection

| Pattern Type | Action | Example |
|---|---|---|
| **Repeated fix** | Create a workflow | "Every time JailTracker 403s, rotate UA" |
| **Vendor pattern** | Update `county-jms-patterns` | "Odyssey pagination always uses `?page=X`" |
| **Scoring insight** | Update `lead-scoring-tuning` | "Weekend arrests score higher" |
| **Error resolution** | Update `scraper-debugger` | "SSL errors → check cert expiry first" |

### Step 3: Auto-Generated Skill
When a pattern is detected ≥2 times, create a new skill:

```bash
mkdir -p .agent/skills/<skill-name>
# Write SKILL.md with frontmatter, instructions, examples
```

### Step 4: Metrics Tracking

```markdown
## Scraper Performance

| County | Success Rate | Avg Records | Avg Time | Last Failure |
|--------|-------------|-------------|----------|--------------|
| Lee | 99.8% | 47 | 8.3s | 2026-04-01 (API schema) |
| Charlotte | 97.2% | 23 | 5.1s | 2026-04-15 (HTTPS) |
```

## Auto-Improvement Triggers

1. ❌ **Scraper error** → Log county, error type, fix
2. 🔄 **Same fix applied twice** → Create workflow
3. 💡 **New JMS pattern discovered** → Update `county-jms-patterns`
4. 📊 **Scoring accuracy changes** → Update `lead-scoring-tuning`
5. 🆕 **New county added** → Log the approach for similar counties
