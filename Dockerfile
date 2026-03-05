# =============================================
# Builder stage — clean dependency install
# =============================================
FROM python:3.11-slim AS builder

# Isolated venv = reliable + tiny
RUN python -m venv /venv

WORKDIR /app

COPY requirements.txt .
RUN /venv/bin/pip install --no-cache-dir --no-compile -r requirements.txt

# =============================================
# Final stage — ultra-small + secure
# =============================================
FROM gcr.io/distroless/python3-debian12:nonroot

# Copy only the venv (this fixes the ModuleNotFoundError)
COPY --from=builder /venv /venv

# Copy script
COPY sync.py /app/

WORKDIR /app

# Runtime config
ENV PATH="/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Runs as non-root (uid 65532) by default
# No shell, no apt, no extra tools → minimal attack surface
ENTRYPOINT ["python", "sync.py"]
