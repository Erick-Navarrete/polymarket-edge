# ---- Build stage: install deps + build frontend ----
FROM python:3.12-slim AS builder

WORKDIR /app

# Install Node.js for frontend build
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir ".[dashboard,ai]"

# Build React frontend
COPY src/dashboard/frontend/package.json src/dashboard/frontend/package-lock.json src/dashboard/frontend/
RUN cd src/dashboard/frontend && npm ci
COPY src/dashboard/frontend/ src/dashboard/frontend/
RUN cd src/dashboard/frontend && npm run build

# ---- Runtime stage: minimal image ----
FROM python:3.12-slim AS runtime

WORKDIR /app

# Install only runtime Python deps (no Node.js, no build tools)
COPY pyproject.toml .
RUN pip install --no-cache-dir ".[dashboard,ai]" && \
    rm -rf /root/.cache/pip

# Copy source code (no tests, no scripts)
COPY src/ src/

# Copy built frontend from builder stage
COPY --from=builder /app/src/dashboard/frontend/dist /app/src/dashboard/frontend/dist

# Health check
HEALTHCHECK --interval=10s --timeout=5s --retries=3 --start-period=15s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/')" || exit 1

EXPOSE 8000

CMD ["uvicorn", "src.dashboard.app:app", "--host", "0.0.0.0", "--port", "8000"]
