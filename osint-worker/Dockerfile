# ============================================
# ShamrockLeads — OSINT Worker v2
# Writable FS for Maigret + Sherlock + Blackbird + SpiderFoot
# Internal network only (no published ports)
# ============================================
FROM python:3.12-slim

LABEL maintainer="Shamrock Active Software"
LABEL description="ShamrockLeads OSINT Worker v2 (Maigret + Sherlock + Blackbird + SpiderFoot)"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/appuser \
    BLACKBIRD_DIR=/opt/blackbird \
    MAIGRET_PATH=/usr/local/bin/maigret \
    SHERLOCK_PATH=/usr/local/bin/sherlock

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git gcc libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY osint-worker/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# ── Maigret ───────────────────────────────────────────────────────────
RUN pip install --no-cache-dir maigret \
    || echo "WARNING: maigret install failed"

# ── Sherlock ──────────────────────────────────────────────────────────
RUN pip install --no-cache-dir sherlock-project \
    || echo "WARNING: sherlock install failed"

# ── SpiderFoot ────────────────────────────────────────────────────────
RUN pip install --no-cache-dir spiderfoot \
    || echo "WARNING: spiderfoot install failed"

# ── Blackbird ─────────────────────────────────────────────────────────
RUN git clone --depth 1 https://github.com/p1ngul1n0/blackbird /opt/blackbird \
    && pip install --no-cache-dir -r /opt/blackbird/requirements.txt \
    && chmod +x /opt/blackbird/blackbird.py \
    && curl -fsSL -o /opt/blackbird/data/wmn-data.json \
         https://raw.githubusercontent.com/WebBreacher/WhatsMyName/main/wmn-data.json \
    && test -s /opt/blackbird/data/wmn-data.json \
    && mkdir -p /opt/blackbird/results /opt/blackbird/logs \
    || echo "WARNING: blackbird install failed"

COPY osint-worker/ /app/

RUN groupadd -r appuser && \
    useradd -r -g appuser -s /sbin/nologin -m -d /home/appuser appuser && \
    chown -R appuser:appuser /app /home/appuser /opt/blackbird

USER appuser

EXPOSE 5065

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:5065/health || exit 1

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5065", "--workers", "1", "--log-level", "info"]
