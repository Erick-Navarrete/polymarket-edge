FROM python:3.12-slim AS base

WORKDIR /app

# Install Node.js for building frontend
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir ".[dashboard,ai]"

# Copy source
COPY src/ src/
COPY scripts/ scripts/
COPY tests/ tests/

# Build React frontend
COPY src/dashboard/frontend/package.json src/dashboard/frontend/package-lock.json src/dashboard/frontend/
RUN cd src/dashboard/frontend && npm ci
COPY src/dashboard/frontend/ src/dashboard/frontend/
RUN cd src/dashboard/frontend && npm run build

# Runtime
EXPOSE 8000

CMD ["uvicorn", "src.dashboard.app:app", "--host", "0.0.0.0", "--port", "8000"]
