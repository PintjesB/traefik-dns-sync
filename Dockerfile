FROM python:3.13-alpine AS builder

WORKDIR /app

# Install deps into isolated prefix
RUN pip install --no-cache-dir --prefix=/install redis requests


FROM python:3.13-alpine

# No root — create dedicated user
RUN addgroup -S appgroup && adduser -S appuser -G appgroup

WORKDIR /app

# Copy only installed packages from builder stage
COPY --from=builder /install /usr/local

# Copy app with correct ownership
COPY --chown=appuser:appgroup sync.py .

USER appuser

# No shell entrypoint — exec form only
ENTRYPOINT ["python", "-u", "sync.py"]
