# ============================================
# ShamrockLeads — OSINT Worker v2
# Writable FS: Maigret · Sherlock · Blackbird · SpiderFoot · Ignorant · Toutatis
# Internal network only (no published ports)
# ============================================
FROM python:3.12-slim

LABEL maintainer="Shamrock Active Software"
LABEL description="ShamrockLeads OSINT Worker v2 (Maigret + Sherlock + Blackbird + SpiderFoot + Ignorant + Toutatis)"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/appuser \
    BLACKBIRD_DIR=/opt/blackbird \
    MAIGRET_PATH=/usr/local/bin/maigret \
    TOOKIE_PATH=/opt/tookie-osint/brib.py \
    SHERLOCK_PATH=/usr/local/bin/sherlock

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git gcc libffi-dev exiftool \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY osint-worker/requirements.txt /app/requirements.txt
# requirements.txt includes ignorant + toutatis (+ httpx/trio/phonenumbers/…)
RUN pip install --no-cache-dir -r /app/requirements.txt

# ── Maigret ───────────────────────────────────────────────────────────
RUN pip install --no-cache-dir maigret \
    || echo "WARNING: maigret install failed"

# ── Tookie-OSINT ──────────────────────────────────────────────────────
RUN git clone --depth 1 https://github.com/alfredredbird/tookie-osint /opt/tookie-osint \
    && (pip install --no-cache-dir -r /opt/tookie-osint/requirements.txt || true) \
    || echo "WARNING: tookie-osint install failed"

# ── Sherlock ──────────────────────────────────────────────────────────
RUN pip install --no-cache-dir sherlock-project \
    || echo "WARNING: sherlock install failed"

# ── SpiderFoot ────────────────────────────────────────────────────────
RUN git clone --depth 1 https://github.com/smicallef/spiderfoot /opt/spiderfoot \
    && pip install --no-cache-dir -r /opt/spiderfoot/requirements.txt \
    && chmod +x /opt/spiderfoot/sf.py \
    || echo "WARNING: spiderfoot install failed"

# ── Ignorant (phone → IG / Snap / Amazon) — required, fail build if missing ──
# Does not alert the target number.
RUN pip install --no-cache-dir "ignorant>=1.2" \
    && python -c "import ignorant; from ignorant.core import import_submodules, get_functions, launch_module"

# ── Toutatis (IG username enrichment) — package required; runtime needs cookie ──
# Set INSTAGRAM_SESSION_ID / TOUTATIS_SESSION_ID on the worker to enable scans.
RUN pip install --no-cache-dir "toutatis>=1.31" phonenumbers pycountry \
    && python -c "import toutatis; from toutatis.core import getInfo, advanced_lookup"

# ── Instaloader (IG profile bio, avatar & metrics) ────────────────────
RUN pip install --no-cache-dir instaloader \
    && python -c "import instaloader"

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

# Final import smoke (catches COPY overwriting nothing; packages must resolve)
RUN python -c "\
import ignorant, toutatis; \
from runners import resolve_ignorant, resolve_toutatis, probe_tools; \
assert resolve_ignorant(), 'ignorant not resolvable'; \
assert resolve_toutatis(), 'toutatis not resolvable'; \
p = probe_tools(); \
assert p['ignorant']['available'] is True, p['ignorant']; \
assert p['toutatis']['package_installed'] is True, p['toutatis']; \
print('OSINT engines OK:', p['version'])"

RUN groupadd -r appuser && \
    useradd -r -g appuser -s /sbin/nologin -m -d /home/appuser appuser && \
    mkdir -p /opt/spiderfoot && \
    chown -R appuser:appuser /app /home/appuser /opt/blackbird /opt/spiderfoot

USER appuser

EXPOSE 5065

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:5065/health || exit 1

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5065", "--workers", "1", "--log-level", "info"]
