# ============================================
# ShamrockLeads — Docker Image
# Python 3.12 + APScheduler + MongoDB
# + Chromium for DrissionPage + patchright (Charlotte) + nodriver (Seminole, Lake)
# + OSINT Tools: Maigret (3000+ site recon), Blackbird (email/username recon)
# ============================================
FROM python:3.12-slim

# Labels
LABEL maintainer="Shamrock Active Software"
LABEL description="Florida Arrest Intelligence Platform"

# System deps — Chromium for DrissionPage, xvfb for patchright (Charlotte), ffmpeg for reCAPTCHA audio solver
# git + wget required for Blackbird clone; tor for optional anonymous OSINT routing
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    git \
    ffmpeg \
    xvfb \
    xauth \
    chromium \
    chromium-driver \
    fonts-liberation \
    libnss3 \
    libxss1 \
    libasound2 \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    libgbm1 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libpango-1.0-0 \
    libcairo2 \
    tor \
    && rm -rf /var/lib/apt/lists/*

# Tell DrissionPage / Chromium where to find the browser
ENV CHROMIUM_PATH=/usr/bin/chromium
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROME_PATH=/usr/bin/chromium

# Working directory
WORKDIR /app

# Install Python deps (no pip cache layer — Hetzner volume is tight)
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && rm -rf /root/.cache/pip /tmp/pip-*

# Install patchright's Chromium browser (for Charlotte County Cloudflare bypass)
RUN python -m patchright install chromium || true

# Install nodriver's Chromium (for Seminole, Lake — JS-rendered jail portals)
RUN python -m nodriver install || true

# ── OSINT Tools ────────────────────────────────────────────────────────────────
# These tools are invoked as subprocesses by dashboard/services/osint_service.py
# All failures are non-fatal (|| echo WARNING) so the main app still starts.

# Maigret: username recon across 3000+ social/forum/dating/gaming sites
# Invoked as: maigret <username> -J simple -fo /tmp/sl_osint_xxx/
RUN pip install --no-cache-dir maigret \
    || echo "WARNING: maigret install failed — OSINT username scans will be degraded"

# Blackbird: email + username recon (separate site database from Maigret)
# Cloned to /opt/blackbird; invoked as:
#   python3 /opt/blackbird/blackbird.py --username <u> --json --no-update
#   (writes JSON under /opt/blackbird/results/ — --json is a boolean flag)
RUN git clone --depth 1 https://github.com/p1ngul1n0/blackbird /opt/blackbird \
    && pip install --no-cache-dir -r /opt/blackbird/requirements.txt \
    && chmod +x /opt/blackbird/blackbird.py \
    || echo "WARNING: blackbird install failed — OSINT email/username scans will be degraded"

# Expose OSINT tool paths as env vars (overridable at runtime via docker-compose .env)
ENV BLACKBIRD_DIR=/opt/blackbird
ENV MAIGRET_PATH=/usr/local/bin/maigret
# Note: Trape runs on the HOST VPS (not inside this container).
#       Set TRAPE_SERVER_URL in .env to the public Trape subdomain.
# ──────────────────────────────────────────────────────────────────────────────

# Copy application
COPY . .

# ── Security: Non-root user ──
# Chromium needs audio/video groups; xvfb needs write to /tmp
# /opt/blackbird must be readable by appuser
RUN groupadd -r appuser && \
    useradd -r -g appuser -G audio,video -s /sbin/nologin appuser && \
    chown -R appuser:appuser /app && \
    chown -R appuser:appuser /opt/blackbird && \
    mkdir -p /app/logs && chown appuser:appuser /app/logs
# USER appuser

# Health check
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# Entry point
CMD ["python", "main.py"]
