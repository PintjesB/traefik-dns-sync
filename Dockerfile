# =============================================
# Builder stage — clean dependency install
# =============================================
FROM python:3.11-slim AS builder

# Isolated venv (fixes the redis import forever)
RUN python -m venv /venv

WORKDIR /app
COPY requirements.txt .
RUN /venv/bin/pip install --no-cache-dir --no-compile -r requirements.txt

# =============================================
# Final stage — tiny + maximum security
# =============================================
FROM gcr.io/distroless/python3-debian12:nonroot

# Copy only the venv + script
COPY --from=builder /venv /venv
COPY sync.py /app/

WORKDIR /app

# Runtime config
ENV PATH="/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Absolute python path = bulletproof (no symlink issues in distroless)
ENTRYPOINT ["/venv/bin/python", "/app/sync.py"]
