# ── Build stage ────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS base

# System deps (tzdata for IST timezone, curl for healthchecks)
RUN apt-get update && apt-get install -y --no-install-recommends \
        tzdata curl \
    && rm -rf /var/lib/apt/lists/*

# Set timezone to IST so market-hours logic works correctly on the host
ENV TZ=Asia/Kolkata

WORKDIR /app

# Install Python dependencies (cached layer — only rebuilt when requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Create persistent directories (bind-mounted in production)
RUN mkdir -p logs

# Environment & Port configuration for Railway Cloud proxy
ENV PORT=8080
EXPOSE 8080 8765

# ── Runtime ─────────────────────────────────────────────────────────────────────
# Default: PAPER mode. Override with --mode LIVE via CMD / Railway start command.
CMD ["python", "main.py", "--mode", "PAPER"]
