# ============================================
# ShamrockLeads — Dashboard (Flask API)
# Lightweight image — no Chromium/xvfb needed
# ============================================
FROM python:3.12-slim

LABEL maintainer="Shamrock Active Software"
LABEL description="ShamrockLeads Dashboard API"

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:5050/health || exit 1

CMD ["python", "dashboard/app.py"]
