# cache-bust: 2026-03-05-v3 — fixes venv + runtime compatibility
FROM python:3.11-slim@sha256:a2bc8c35469b6fe37735f7c4dae39049470b2ce068e73f799c02452de31d24c6

# Install the available security fix in the pinned Debian base.
RUN apt-get update \
    && apt-get install -y --only-upgrade --no-install-recommends libpcre2-8-0 \
    && rm -rf /var/lib/apt/lists/*

# Security: dedicated non-root user (best practice 2026)
RUN useradd --create-home --shell /bin/false --uid 1000 appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --no-compile -r requirements.txt

COPY sync.py .

# Drop privileges
USER appuser

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "sync.py"]
