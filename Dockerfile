# cache-bust: 2026-03-05-v3 — fixes venv + runtime compatibility
FROM python:3.11-slim@sha256:7032841967d07aa3353905945dc186901c89b6b319c61425f4439f677a4a8a34

# Install the available security fix in the pinned Debian base.
RUN apt-get update \
    && apt-get install -y --only-upgrade --no-install-recommends libpcre2-8-0 zlib1g \
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
