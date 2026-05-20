"""
ShamrockLeads Dashboard — Uvicorn Runner
Entry point for the modular FastAPI application.
Usage: python -m dashboard.run  OR  python dashboard/run.py
"""
from __future__ import annotations

import os
import uvicorn


def main():
    host = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    port = int(os.getenv("DASHBOARD_PORT", "5050"))

    print(f"\n{'═' * 60}")
    print(f"☘️  ShamrockLeads Dashboard (FastAPI + Uvicorn)")
    print(f"   Listening: http://{host}:{port}")
    print(f"   External:  http://178.156.179.237:8088/")
    print(f"{'═' * 60}\n")

    # Launch FastAPI app via Uvicorn (single-worker required for asyncio.Event cron triggers)
    uvicorn.run(
        "dashboard.main:app",
        host=host,
        port=port,
        workers=1,
        access_log=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
