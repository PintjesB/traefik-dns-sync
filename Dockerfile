# cache-bust: 2026-03-05-v3 — fixes venv + runtime compatibility
FROM python:3.14-slim@sha256:c3e521df8b2b498a7a682e7e18676771cb80c6b75b8699af886b2d554ce40151

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
