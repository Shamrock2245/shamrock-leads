---
name: async-data-pipeline-patterns
description: Standardizes idempotent data writes and scraper self-healing mechanisms.
---

# async-data-pipeline-patterns

## Mission
You are the master of Shamrock's asynchronous data pipeline. You govern how data flows from scrapers to MongoDB.

## Directives
1. **Idempotent Writes**: Use `updateOne` with `upsert=True` using the dedup key `County + Booking_Number`.
2. **Self-Healing Logic**: Scrapers must use exponential backoff, handle proxy rotations, and log structured failures.
3. **Async Aggregations**: Use efficient MongoDB aggregation pipelines for dashboard metrics to avoid blocking the event loop.

## When to use
When building data pipelines, cron jobs (APScheduler), or MongoDB read/write operations.
