---
name: self-improving-agent
description: Enables the agent to learn from every session — log lessons, create skills from patterns, maintain knowledge base, and auto-improve scraper infrastructure. Adapted from charon-fan/agent-playbook.
source: https://skills.sh/charon-fan/agent-playbook/self-improving-agent
---

# Self-Improving Agent

> The agent that learns from every session and gets better at its job.

## Purpose
1. **Log lessons learned** from each session into a persistent knowledge base
2. **Create new micro-skills** when repeatable patterns are discovered
3. **Update existing skills** when better approaches are found
4. **Track scraper-specific metrics** — success rates, failure modes, recovery patterns
5. **Auto-update documentation** when infrastructure changes (new counties, new scrapers)
6. **Feed self-healing data** back into BaseScraper failure classification

## When to Use
- At the END of every significant session
- When discovering a new scraping pattern that could be reused
- When a workaround is found for a recurring problem
- When the agent makes an error and wants to prevent it in the future
- After deploying new scrapers to production
- After fixing a broken scraper

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

### Infrastructure Changes
- New scrapers added
- Configuration changes
- Dashboard updates

### Knowledge Gaps
- Areas needing more investigation (e.g., "Miami-Dade has custom API")
```

### Step 2: Pattern Detection

| Pattern Type | Action | Example |
|---|---|---|
| **Repeated fix** | Create a workflow | "Every time JailTracker 403s, rotate UA" |
| **Vendor pattern** | Update `county-jms-patterns` | "Odyssey pagination always uses `?page=X`" |
| **Scoring insight** | Update `lead-scoring-tuning` | "Weekend arrests score higher" |
| **Error resolution** | Update `scraper-debugger` Knowledge Base | "SSL errors → check cert expiry first" |
| **URL migration** | Update `COUNTY_REGISTRY.md` | "Charlotte moved to HTTPS" |
| **New base class** | Create shared base (e.g., `SmartCOPBaseScraper`) | Multiple counties share same JMS |

### Step 3: Auto-Generated Skill
When a pattern is detected ≥2 times, create a new skill:

```bash
mkdir -p .agent/skills/<skill-name>
# Write SKILL.md with frontmatter, instructions, examples
```

### Step 4: Documentation Auto-Update

After every session that changes infrastructure, update:

| File | When to Update | What to Change |
|------|---------------|----------------|
| `docs/COUNTY_REGISTRY.md` | New scraper added/fixed | Mark ✅, update interval, "Last Verified" date |
| `AGENTS.md` | Architecture change | Update counts, add new agents, fix diagrams |
| `GEMINI.md` | Repo structure change | Update file tree, tech stack, skill list |
| `docs/SCHEMAS.md` | ArrestRecord fields change | Sync model with docs |
| `scraper-debugger` KB | New failure mode | Add row to Knowledge Base table |
| `county-jms-patterns` | New vendor pattern | Add vendor section or update existing |

### Step 5: Scraper Performance Tracking

Maintain performance metrics in session retrospectives:

```markdown
## Scraper Performance — [DATE]

| County | Status | Records | Avg Time | Failures (24h) | Error Type |
|--------|--------|---------|----------|-----------------|------------|
| Lee | ✅ Healthy | 47 | 8.3s | 0 | — |
| Charlotte | ⚠️ Stale | 23 | 5.1s | 2 | network |
| Hendry | ✅ Healthy | 12 | 15.2s | 0 | — |
```

Data source: `GET /api/scraper-health` endpoint.

### Step 6: Failure Pattern Recognition

When reviewing failure_history from BaseScraper health checks:

| Pattern | Diagnosis | Auto-Fix |
|---------|-----------|----------|
| Same error 5x in a row | Persistent issue, not transient | Follow scraper-debugger Phase 2 |
| Alternating network/timeout | ISP or county server instability | Increase interval, add retry delay |
| `parse_error` after county site update | HTML structure changed | Re-fetch page, update selectors |
| `url_changed` + `redirect` | County migrated domain | Google for new URL, update scraper |
| `anti_bot` spike | Scraping too fast | Reduce frequency, add random delay |
| `ssl_error` recurring | Certificate expired or misconfigured | Try HTTP fallback, add verify=False |

## Auto-Improvement Triggers

1. ❌ **Scraper error** → Log county, error type, fix in scraper-debugger KB
2. 🔄 **Same fix applied twice** → Create workflow in `.agent/workflows/`
3. 💡 **New JMS pattern discovered** → Update `county-jms-patterns`
4. 📊 **Scoring accuracy changes** → Update `lead-scoring-tuning`
5. 🆕 **New county added** → Log the approach for similar counties
6. 🚫 **Scraper auto-disabled** → Immediate investigation + post-mortem
7. 📝 **Documentation drift** → Sync COUNTY_REGISTRY, AGENTS.md, GEMINI.md
8. 🏗️ **Infrastructure deploy** → Log what changed, verify health post-deploy

## Continuous Improvement Checklist

After every significant session, verify:
- [ ] All active scrapers are ✅ in COUNTY_REGISTRY.md
- [ ] Scraper counts in AGENTS.md and GEMINI.md match reality
- [ ] Any new failure modes are logged in scraper-debugger KB
- [ ] Dashboard health endpoint shows all counties "healthy" or "stale" (not "offline")
- [ ] Git changes are committed with descriptive messages
- [ ] Hetzner deployment is up-to-date with local changes
