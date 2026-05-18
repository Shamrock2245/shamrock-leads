# ============================================
# ShamrockLeads — Docker Image
# Python 3.12 + APScheduler + MongoDB
# + Chromium for DrissionPage + patchright (Charlotte) + nodriver (Seminole, Lake)
# ============================================
FROM python:3.12-slim

# Labels
LABEL maintainer="Shamrock Active Software"
LABEL description="Florida Arrest Intelligence Platform"

# System deps — Chromium for DrissionPage, xvfb for patchright (Charlotte)
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
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
    && rm -rf /var/lib/apt/lists/*

# Tell DrissionPage / Chromium where to find the browser
ENV CHROMIUM_PATH=/usr/bin/chromium
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROME_PATH=/usr/bin/chromium

# Working directory
WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install patchright's Chromium browser (for Charlotte County Cloudflare bypass)
RUN python -m patchright install chromium || true

# Install nodriver's Chromium (for Seminole, Lake — JS-rendered jail portals)
RUN python -m nodriver install || true

# Copy application
COPY . .

# ── Security: Non-root user ──
# Chromium needs audio/video groups; xvfb needs write to /tmp
RUN groupadd -r appuser && \
    useradd -r -g appuser -G audio,video -s /sbin/nologin appuser && \
    chown -R appuser:appuser /app && \
    mkdir -p /app/logs && chown appuser:appuser /app/logs
USER appuser

# Health check
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# Entry point
CMD ["python", "main.py"]
