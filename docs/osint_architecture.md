# OSINT Intelligence Module Architecture

## Overview
The OSINT Intelligence Module provides deep background research on defendants and indemnitors to mitigate financial risk. It integrates three external tools:
1. **Maigret**: Username search across 3000+ sites.
2. **Blackbird**: Fast username/email search with AI profiling.
3. **Trape**: Real-time social engineering and tracking.

This module is strictly **admin-only** and must log all actions via `AuditService` to maintain SOC II compliance.

## Components

### 1. Data Models (`dashboard/models/osint.py`)
- `OSINTTarget`: Represents the individual being investigated (Defendant or Indemnitor).
- `OSINTReport`: The generated report containing findings from Maigret and Blackbird.
- `TrapeSession`: Represents an active tracking session.

### 2. Service Layer (`dashboard/services/osint_service.py`)
- Wraps CLI calls to Maigret and Blackbird using `asyncio.create_subprocess_exec`.
- Parses JSON outputs from these tools.
- Manages Trape payload generation and webhook callbacks.
- Stores reports in the `osint_profiles` MongoDB collection.

### 3. API Router (`dashboard/routers/osint_api.py`)
- `POST /api/osint/scan`: Initiates a Maigret/Blackbird scan.
- `GET /api/osint/report/{id}`: Retrieves a generated report.
- `POST /api/osint/trape/generate`: Generates a Trape tracking link.
- **Security**: Enforces admin access via a custom header or role check on top of `PinAuthMiddleware`.

### 4. UI Integration (`dashboard/sl-osint.js` & `index.html`)
- A new "Intelligence" or "OSINT" tab in the Command Center.
- Displays risk signals, social footprint graphs, and Trape tracking data.
- Adheres to "Premium" design directives (glassmorphism, micro-animations).

## Execution Flow
1. Admin selects a Defendant/Indemnitor and clicks "Run OSINT Scan".
2. Frontend calls `/api/osint/scan` with target details.
3. `osint_service.py` executes Blackbird (fast) and Maigret (deep) asynchronously.
4. Results are parsed, scored for risk, and saved to MongoDB.
5. `AuditService` logs the scan execution.
6. Frontend polls or receives WebSocket update and renders the report.

## Risk Mitigation
- PII must not be logged in plaintext application logs.
- All OSINT data must be strictly access-controlled.
