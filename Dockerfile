FROM python:3.13-slim AS builder

WORKDIR /app

COPY requirements.txt .

# Install only redis-py — requests replaced with stdlib urllib
RUN pip install --no-cache-dir --prefix=/install --no-compile -r requirements.txt


FROM gcr.io/distroless/python3-debian12:nonroot

# Prevent .pyc files and force stdout/stderr unbuffered
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy only installed packages from builder
COPY --from=builder /install/lib /usr/local/lib

# Copy app — distroless nonroot image already runs as uid 65532
COPY sync.py .

# No shell, no package manager, no root, no /bin/sh, no /bin/bash
ENTRYPOINT ["python", "sync.py"]
