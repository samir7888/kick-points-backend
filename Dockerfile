# Multi-stage Dockerfile for production and development

FROM python:3.12-slim as base

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install uv for fast dependency management
RUN pip install uv

# Copy dependency files
COPY pyproject.toml ./

# Development stage
FROM base as development
COPY . .
RUN uv pip install --system -e ".[dev]"
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# Production stage
FROM base as production
COPY . .
RUN uv pip install --system .
EXPOSE 8000

# Create the non-root user and the writable upload directory (the persistent
# disk mounts here). Creating it in the image keeps ownership correct for the
# app user when the disk is not attached.
RUN useradd --create-home --shell /bin/bash app \
    && mkdir -p /app/uploads/qr-proofs \
    && chown -R app:app /app
USER app

# Bind to Render's injected $PORT (falls back to 8000 for local runs). The
# "exec" keeps uvicorn as PID 1 so SIGTERM triggers a clean shutdown.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]