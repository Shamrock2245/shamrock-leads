# ============================================
# ShamrockLeads — Dashboard (FastAPI + Uvicorn)
# Lightweight image — no Chromium/xvfb needed
# OSINT CLIs run in osint-worker (not here)
# ============================================
FROM python:3.12-slim

LABEL maintainer="Shamrock Active Software"
LABEL description="ShamrockLeads Dashboard API (FastAPI/Uvicorn)"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl libxml2-dev libxslt-dev libyaml-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# ── Security: Non-root user ──
RUN groupadd -r appuser && \
    useradd -r -g appuser -s /sbin/nologin appuser && \
    chown -R appuser:appuser /app && \
    mkdir -p /app/dashboard/uploads && chown appuser:appuser /app/dashboard/uploads
USER appuser

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:5050/health || exit 1

# Launch FastAPI app via Uvicorn ASGI server (workers=1 is required for in-process asyncio.Event triggers)
CMD ["python", "-m", "uvicorn", "dashboard.main:app", "--host", "0.0.0.0", "--port", "5050", "--access-log", "--log-level", "info", "--workers", "1"]
