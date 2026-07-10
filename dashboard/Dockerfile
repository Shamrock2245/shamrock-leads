# ============================================
# ShamrockLeads — Dashboard (FastAPI + Uvicorn)
# Lightweight image — no Chromium/xvfb needed
# Includes OSINT CLI tools (Maigret + Blackbird)
# ============================================
FROM python:3.12-slim

LABEL maintainer="Shamrock Active Software"
LABEL description="ShamrockLeads Dashboard API (FastAPI/Uvicorn)"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git libxml2-dev libxslt-dev libyaml-dev gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── OSINT Tools (invoked by dashboard/services/osint_service.py) ──────────────
# Maigret v0.6+: maigret <user> -J simple -fo /tmp/dir
# Blackbird v2:  python blackbird.py --username u --json --no-update
RUN pip install --no-cache-dir maigret \
    || echo "WARNING: maigret install failed — OSINT username scans will be degraded"

RUN git clone --depth 1 https://github.com/p1ngul1n0/blackbird /opt/blackbird \
    && pip install --no-cache-dir -r /opt/blackbird/requirements.txt \
    && chmod +x /opt/blackbird/blackbird.py \
    && curl -fsSL -o /opt/blackbird/data/wmn-data.json \
         https://raw.githubusercontent.com/WebBreacher/WhatsMyName/main/wmn-data.json \
    && test -s /opt/blackbird/data/wmn-data.json \
    || echo "WARNING: blackbird install failed — OSINT email/username scans will be degraded"

ENV BLACKBIRD_DIR=/opt/blackbird \
    MAIGRET_PATH=/usr/local/bin/maigret
# Trape runs on the host VPS (not this container). Set TRAPE_SERVER_URL in .env.
# ──────────────────────────────────────────────────────────────────────────────

COPY . .

# ── Security: Non-root user ──
# Maigret needs a real writable HOME (creates ~/.maigret). Create /home/appuser.
RUN groupadd -r appuser && \
    useradd -r -g appuser -s /sbin/nologin -m -d /home/appuser appuser && \
    chown -R appuser:appuser /app && \
    mkdir -p /app/dashboard/uploads /home/appuser/.maigret /home/appuser/.cache && \
    chown -R appuser:appuser /home/appuser /app/dashboard/uploads && \
    (chown -R appuser:appuser /opt/blackbird 2>/dev/null || true) && \
    mkdir -p /opt/blackbird/results && chown -R appuser:appuser /opt/blackbird/results 2>/dev/null || true
ENV HOME=/home/appuser
USER appuser

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:5050/health || exit 1

# Launch FastAPI app via Uvicorn ASGI server (workers=1 is required for in-process asyncio.Event triggers)
CMD ["python", "-m", "uvicorn", "dashboard.main:app", "--host", "0.0.0.0", "--port", "5050", "--access-log", "--log-level", "info", "--workers", "1"]
