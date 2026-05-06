FROM ubuntu:24.04@sha256:c4a8d5503dfb2a3eb8ab5f807da5bc69a85730fb49b5cfca2330194ebcc41c7b AS builder

ARG TARGETARCH


# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    python3.12 \
    python3.12-venv \
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

# Copy volundr package and install
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
COPY pyproject.toml uv.lock README.md /app/
COPY src/ /app/src/
RUN uv sync --frozen --no-dev --no-editable --extra k8s

# ---------------------------------------------------------------------------
# Runtime — Node.js stays (needed for claude-code CLI at runtime)
# ---------------------------------------------------------------------------
FROM ubuntu:24.04@sha256:c4a8d5503dfb2a3eb8ab5f807da5bc69a85730fb49b5cfca2330194ebcc41c7b


LABEL org.opencontainers.image.title="volundr"
LABEL org.opencontainers.image.description="Volundr — self-hosted AI-native development platform"
LABEL org.opencontainers.image.vendor="Niuu Labs"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.source="https://github.com/niuulabs/volundr"

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    git \
    python3.12 \
    python3.12-venv \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 24 LTS (pinned keyring + apt source instead of curl|bash)
RUN curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
    -o /usr/share/keyrings/nodesource.asc \
    && echo "deb [signed-by=/usr/share/keyrings/nodesource.asc] https://deb.nodesource.com/node_24.x nodistro main" \
    > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Upgrade npm and install the CLI tools from checked-in lockfiles.
COPY containers/node/npm-bootstrap/package.json containers/node/npm-bootstrap/package-lock.json /opt/npm-bootstrap/
COPY containers/volundr/npm-tools/package.json containers/volundr/npm-tools/package-lock.json /opt/volundr-tools/
RUN cd /opt/npm-bootstrap \
    && npm ci --omit=dev \
    && rm -rf /usr/lib/node_modules/npm \
    && ln -s /opt/npm-bootstrap/node_modules/npm /usr/lib/node_modules/npm \
    && ln -sf /usr/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && cd /opt/volundr-tools \
    && npm ci --omit=dev \
    && ln -sf /opt/volundr-tools/node_modules/.bin/claude /usr/local/bin/claude

# Create app user
RUN useradd -m -s /bin/bash volundr

# Copy the pre-built venv from builder.
ENV VIRTUAL_ENV=/opt/venv
COPY --from=builder /opt/venv /opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Copy entrypoint
COPY containers/volundr/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Set working directory
WORKDIR /app

# Switch to non-root user
USER volundr

# Expose API port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
