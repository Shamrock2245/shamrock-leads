"""
ShamrockLeads Dashboard — Hypercorn Runner
Entry point for the modular Quart application.
Usage: python -m dashboard.run  OR  python dashboard/run.py
"""

import os
import asyncio


def main():
    from dashboard import create_app

    app = create_app()
    host = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    port = int(os.getenv("DASHBOARD_PORT", "5050"))

    print(f"\n{'═' * 60}")
    print(f"☘️  ShamrockLeads Dashboard (Quart + Motor)")
    print(f"   Listening: http://{host}:{port}")
    print(f"   External:  http://178.156.179.237:8088/")
    print(f"{'═' * 60}\n")

    # Use hypercorn if available, fallback to app.run_task
    try:
        from hypercorn.config import Config
        from hypercorn.asyncio import serve

        config = Config()
        config.bind = [f"{host}:{port}"]
        config.accesslog = "-"
        asyncio.run(serve(app, config))
    except ImportError:
        print("⚠️  hypercorn not installed — using Quart dev server")
        app.run(host=host, port=port)


if __name__ == "__main__":
    main()
