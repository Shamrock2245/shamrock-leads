# ============================================
# ShamrockLeads — Docker Image
# Python 3.12 + APScheduler + MongoDB
# + Chromium for DrissionPage (Charlotte, Hendry)
# ============================================
FROM python:3.12-slim

# Labels
LABEL maintainer="Shamrock Active Software"
LABEL description="Florida Arrest Intelligence Platform"

# System deps — includes Chromium for DrissionPage browser automation
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
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

# Copy application
COPY . .

# Health check
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8088/health || exit 1

# Entry point
CMD ["python", "main.py"]
