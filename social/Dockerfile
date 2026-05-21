# ============================================
# Shamrock Social Engine — Content Repurposer & Publisher
# Lightweight FastAPI microservice — no heavy deps needed
# Port: 5060 (internal) → proxied through dashboard
# ============================================
FROM python:3.12-slim

LABEL maintainer="Shamrock Active Software"
LABEL description="Social media content engine — repurposer, Grok integration, multi-platform posting"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies
COPY social/requirements.txt /app/social/requirements.txt
RUN pip install --no-cache-dir -r /app/social/requirements.txt

# Copy full project (social/ imports from social.*)
COPY . .

# ── Security: Non-root user ──
RUN groupadd -r appuser && \
    useradd -r -g appuser -s /sbin/nologin appuser && \
    chown -R appuser:appuser /app
USER appuser

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:5060/health || exit 1

EXPOSE 5060

# Launch Social Engine via Uvicorn
CMD ["uvicorn", "social.main:app", "--host", "0.0.0.0", "--port", "5060", "--access-log", "--log-level", "info", "--workers", "1"]
