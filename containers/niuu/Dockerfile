FROM ubuntu:24.10@sha256:cdf755952ed117f6126ff4e65810bf93767d4c38f5c7185b50ec1f1078b464cc AS builder

ARG TARGETARCH


# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    python3 \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Install uv from a pinned release archive and resolve from the committed lockfile.
RUN set -eux; \
    UV_VERSION="0.11.10"; \
    case "${TARGETARCH}" in \
      amd64) UV_ARCHIVE="uv-x86_64-unknown-linux-gnu.tar.gz"; UV_SHA256="077e1a0777bcf516e02f4ef245e269c8d1baa780438e4c50e09c5c997f85538a" ;; \
      arm64) UV_ARCHIVE="uv-aarch64-unknown-linux-gnu.tar.gz"; UV_SHA256="91d5f4583539640765662ef86edcf3bf4db07439b622c7bed50c961240162046" ;; \
      *) echo "Unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${UV_ARCHIVE}" -o "/tmp/${UV_ARCHIVE}"; \
    echo "${UV_SHA256}  /tmp/${UV_ARCHIVE}" | sha256sum -c -; \
    tar -xzf "/tmp/${UV_ARCHIVE}" -C /tmp; \
    install -m 0755 "/tmp/${UV_ARCHIVE%.tar.gz}/uv" /usr/local/bin/uv; \
    rm -rf "/tmp/${UV_ARCHIVE}" "/tmp/${UV_ARCHIVE%.tar.gz}"

# Copy package and install
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
COPY pyproject.toml uv.lock README.md /app/
COPY src/ /app/src/
RUN uv sync --frozen --no-dev --no-editable --extra k8s

# ---------------------------------------------------------------------------
# Runtime — minimal image with no build tools or pip
# ---------------------------------------------------------------------------
FROM ubuntu:24.10@sha256:cdf755952ed117f6126ff4e65810bf93767d4c38f5c7185b50ec1f1078b464cc


LABEL org.opencontainers.image.title="niuu"
LABEL org.opencontainers.image.description="Niuu — unified Python control plane services"
LABEL org.opencontainers.image.vendor="Niuu Labs"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.source="https://github.com/niuulabs/volundr"

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install only runtime dependencies (no pip, no build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    git \
    python3 \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Create app user
RUN useradd -m -s /bin/bash niuu

# Copy the pre-built venv from builder.
ENV VIRTUAL_ENV=/opt/venv
COPY --from=builder /opt/venv /opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Copy entrypoint
COPY containers/niuu/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Set working directory
WORKDIR /app

# Shared runtime paths used by the unified control-plane services.
RUN mkdir -p /data/mimir && chown niuu:niuu /data/mimir

# Switch to non-root user
USER niuu

# Expose API port
EXPOSE 8082

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8082/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
