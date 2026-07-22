"""
ShamrockLeads Dashboard — LEGACY MODULE NAME (FastAPI only)

Flask has been fully removed. Production and local both run:

    uvicorn dashboard.main:app --host 0.0.0.0 --port 5050
    # or:
    python -m dashboard.run

This module only re-exports the FastAPI app so any leftover import of
``dashboard.app`` still resolves to the live FastAPI instance.
"""
from __future__ import annotations

import sys
import warnings

warnings.warn(
    "dashboard.app is a FastAPI re-export. Use dashboard.main:app "
    "(uvicorn dashboard.main:app). Flask is no longer part of this build.",
    DeprecationWarning,
    stacklevel=2,
)

from dashboard.main import app  # noqa: E402, F401

__all__ = ["app"]


if __name__ == "__main__":
    print(
        "\n"
        "☘️  Flask was removed. Start the dashboard with FastAPI:\n"
        "\n"
        "    python -m dashboard.run\n"
        "    # or\n"
        "    uvicorn dashboard.main:app --host 0.0.0.0 --port 5050\n"
        "\n",
        file=sys.stderr,
    )
    sys.exit(2)
