FROM ubuntu:24.04@sha256:186072bba1b2f436cbb91ef2567abca677337cfc786c86e107d25b7072feef0c AS builder


# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    python3.12 \
    python3.12-venv \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Set up Python virtual environment and upgrade pip immediately
ENV VIRTUAL_ENV=/opt/venv
RUN python3.12 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Upgrade pip to latest to avoid GHSA-4xh5-x5gv-qwph / GHSA-6vgw-5pg2-w6jp,
# then install build backend
RUN pip install --no-cache-dir --upgrade pip==26.1.1 \
    && pip install --no-cache-dir hatchling==1.29.0

# Copy package and install
COPY pyproject.toml README.md /app/
COPY src/ /app/src/
RUN pip install --no-cache-dir '/app[k8s]'

# Remove pip from the venv so it is absent from the runtime image
RUN pip uninstall -y pip

# ---------------------------------------------------------------------------
# Runtime — minimal image with no build tools or pip
# ---------------------------------------------------------------------------
FROM ubuntu:24.04@sha256:186072bba1b2f436cbb91ef2567abca677337cfc786c86e107d25b7072feef0c


LABEL org.opencontainers.image.title="tyr"
LABEL org.opencontainers.image.description="Tyr — saga coordinator service"
LABEL org.opencontainers.image.vendor="Niuu Labs"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.source="https://github.com/niuulabs/volundr"

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install only runtime dependencies (no pip, no build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    python3.12 \
    python3.12-venv \
    && rm -rf /var/lib/apt/lists/*

# Create app user
RUN useradd -m -s /bin/bash tyr

# Copy the pre-built venv from builder (no pip, no hatchling)
ENV VIRTUAL_ENV=/opt/venv
COPY --from=builder /opt/venv /opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Copy entrypoint
COPY containers/tyr/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Set working directory
WORKDIR /app

# Switch to non-root user
USER tyr

# Expose API port
EXPOSE 8081

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8081/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
