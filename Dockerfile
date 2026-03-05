# =============================================
# Builder stage (installs deps cleanly)
# =============================================
FROM python:3.11-slim AS builder

WORKDIR /app

# Create isolated virtual environment (the reliable way for distroless)
RUN python -m venv /venv

# Install the only dependency (redis-py is pure Python → tiny)
COPY requirements.txt .
RUN /venv/bin/pip install --no-cache-dir -r requirements.txt

# =============================================
# Final stage — tiny + ultra-secure
# =============================================
FROM gcr.io/distroless/python3-debian12:nonroot

# Copy only the virtual environment (this fixes the redis import error)
COPY --from=builder /venv /venv

WORKDIR /app

# Copy your script (nothing else)
COPY sync.py .

# Minimal runtime environment
ENV PATH="/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Already runs as non-root (uid 65532) thanks to :nonroot tag
# No shell, no package manager, no unnecessary files → minimal attack surface

ENTRYPOINT ["python", "sync.py"]
