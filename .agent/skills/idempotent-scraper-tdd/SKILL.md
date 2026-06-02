---
name: idempotent-scraper-tdd
description: Test-Driven Development workflow for building BaseScraper extensions.
---

# idempotent-scraper-tdd

## Mission
You enforce a Test-Driven approach to building county jail scrapers.

## Directives
1. **Mock First**: Always capture real HTML/JSON responses from the county jail and save them as test fixtures. Write parsing tests against these fixtures.
2. **Test Idempotency**: Ensure that running the scraper twice against the same data results in exactly zero duplicate records.
3. **Test Evasion**: Write tests for backoff logic, retry limits, and error classification (`network`, `anti_bot`, `parse_error`).

## When to use
When adding a new county to `scrapers/counties/` or refactoring `BaseScraper`.
