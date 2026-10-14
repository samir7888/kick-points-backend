FROM python:3.12-slim

WORKDIR /app

# Set environment defaults
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=UTC \
    PORT=4000

# Install system dependencies (ca-certificates for SSL, tzdata, curl)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    tzdata \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Create logs directory
RUN mkdir -p logs

# Copy source code and entrypoint
COPY src/ ./src/
COPY main.py .
COPY config.example.json .

# Expose web dashboard port
EXPOSE 4000

CMD ["python", "main.py"]
