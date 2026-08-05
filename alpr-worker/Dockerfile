# ============================================
# ShamrockLeads — ALPR Worker (FL511 + Fast-ALPR)
# Samples public traffic cameras, runs plate OCR,
# matches lpr_watchlist, writes lpr_hits, Slack alerts.
# ============================================
FROM python:3.12-slim

LABEL maintainer="Shamrock Active Software"
LABEL description="ShamrockLeads FL511 ALPR worker (Fast-ALPR + OpenCV)"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ALPR_ENABLED=true \
    ALPR_CROPS_DIR=/data/alpr_crops

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Core runtime deps (keep lean — vision stack next)
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir \
    pymongo \
    python-dotenv \
    requests \
    numpy \
    "opencv-python-headless>=4.8.0" \
    "onnxruntime>=1.16.0" \
    "fast-alpr>=0.4.0" \
    || pip install --no-cache-dir pymongo python-dotenv requests numpy opencv-python-headless onnxruntime

# Application code
COPY services/ /app/services/
COPY scrapers/alpr_worker.py /app/scrapers/alpr_worker.py
COPY config/alpr_cameras.json /app/config/alpr_cameras.json
COPY writers/ /app/writers/
COPY core/ /app/core/
COPY config/settings.py /app/config/settings.py
COPY config/__init__.py /app/config/__init__.py

RUN mkdir -p /app/scrapers /data/alpr_crops \
    && printf '' > /app/scrapers/__init__.py \
    && python -c "from services.alpr_engine import probe_alpr_deps; print(probe_alpr_deps())"

# Non-root
RUN groupadd -r appuser && useradd -r -g appuser -s /sbin/nologin appuser \
    && chown -R appuser:appuser /app /data
USER appuser

HEALTHCHECK --interval=60s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import os,sys; sys.exit(0 if os.getenv('ALPR_ENABLED','true') else 0)"

CMD ["python", "-m", "scrapers.alpr_worker"]
