# ============================================
# ShamrockLeads — Docker Image
# Python 3.12 + APScheduler + MongoDB
# ============================================
FROM python:3.12-slim

# Labels
LABEL maintainer="Shamrock Active Software"
LABEL description="Florida Arrest Intelligence Platform"

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Working directory
WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Health check
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# Entry point
CMD ["python", "main.py"]
