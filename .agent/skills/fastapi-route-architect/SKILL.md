---
name: fastapi-route-architect
description: Instructs the digital workforce on building, extending, or maintaining API routes under FastAPI constraints, mandating Pydantic schemas, dependency injection, and zero loose string parsing.
---

# FastAPI Route Architect — Code Style & Standard Enforcer

> Mandates clean, performant, and type-safe routing practices across the ShamrockLeads FastAPI environment.

---

## Prime Directives

1. **Always Use Pydantic for Request/Query Validation**: Never accept unorganized query strings or raw request dicts and manually type-cast them with `try...except` blocks.
2. **Standardize Dependency Injection**: Access MongoDB collections, database clients, and application settings exclusively through `fastapi.Depends` and `dashboard/deps.py`.
3. **No Loose String Parsing**: Parameter validation must be shifted to Pydantic schemas or custom path parameters so FastAPI blocks invalid types early with `422 Unprocessable Entity`.
4. **Enforce Async paradigms**: All route handlers must be `async def`, utilizing async Motor cursor pagination (`async for`) or bulk operations.

---

## 🛠 Pydantic Query Parameters Pattern

When a route accepts multiple query parameters for filtering, define a schema using Pydantic `BaseModel` and bind it using `Depends()`.

### The Bad (Manual parsing in path)
```python
# ❌ DEPRECATED PARADIGM
@router.get("/leads")
async def api_leads(days: str = "", min_bond: str = ""):
    q = {}
    if days:
        try:
            d = int(days) # ❌ Manual parsing in router
            q["scraped_at"] = {"$gte": cutoff}
        except ValueError:
            pass
```

### The Good (Pydantic Dependency)
```python
# ✅ FASTAPI PARADIGM
from pydantic import BaseModel, Field
from typing import Optional

class LeadsQuery(BaseModel):
    days: Optional[int] = Field(None, ge=1, le=30, description="Recent days cutoff")
    min_bond: Optional[float] = Field(None, ge=0.0, description="Minimum bond amount")

@router.get("/leads")
async def api_leads(query: LeadsQuery = Depends()):
    # FastAPI automatically validates days and min_bond!
    # Non-numeric inputs are blocked before running route code.
    q = {}
    if query.days:
        ...
```

---

## 🏗 Dependency Injection Patterns

Database collections must be injected via `deps.py` helper functions, which return the Motor collection handles.

```python
from fastapi import APIRouter, Depends
from dashboard.deps import get_collection

router = APIRouter(prefix="/api", tags=["active_bonds"])

@router.get("/bonds")
async def get_active_bonds(
    limit: int = 50,
    active_bonds = Depends(lambda: get_collection("active_bonds")) # ✅ Standard injection
):
    results = []
    async for doc in active_bonds.find({}).limit(limit):
        results.append(doc)
    return results
```

---

## 🚀 Performance & Memory Safety

1. **Progressive CSV Streaming**: For raw data exports exceeding 500 records, always stream database chunks progressively using `StreamingResponse` combined with an asynchronous stream generator (e.g. `async_csv_streamer`). Never parse query lists directly into a giant string in memory.
2. **Projection-First Queries**: Never fetch the whole document (`{}`) if you only need 3-4 fields. Specify exact field projections in `.find(query, projection)` to minimize network overhead and database CPU consumption.
