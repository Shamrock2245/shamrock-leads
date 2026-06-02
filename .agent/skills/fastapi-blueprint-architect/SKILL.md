---
name: fastapi-blueprint-architect
description: Enforces mandatory Pydantic schemas, dependency injection for DB sessions, and async motor operations.
---

# fastapi-blueprint-architect

## Mission
You are the architect of Shamrock's FastAPI backend. You must ensure that every API endpoint strictly adheres to our "No guessing" rule.

## Directives
1. **Pydantic Everywhere**: Never accept raw dictionaries or arbitrary JSON. Every request and response must use Pydantic models.
2. **Dependency Injection**: Use FastAPI `Depends()` for MongoDB connections, authentication, and any shared services.
3. **Async Motor**: All database calls must be fully async.
4. **Idempotency**: All `POST` routes must check for duplicate keys (e.g., `Booking_Number + County`) before insertion.

## When to use
Whenever building or modifying API routes in `dashboard/routers/`.
