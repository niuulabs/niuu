FROM ubuntu:26.04@sha256:3131b4cc82a783df6c9df078f86e01819a13594b865c2cad47bd1bca2b7063bb AS builder

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

# Copy volundr package and install
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
COPY pyproject.toml uv.lock README.md /app/
COPY src/ /app/src/
RUN uv sync --frozen --no-dev --no-editable --extra otel

# ---------------------------------------------------------------------------
# Runtime — Node.js stays (needed for claude-code/codex CLI at runtime)
# ---------------------------------------------------------------------------
FROM ubuntu:26.04@sha256:3131b4cc82a783df6c9df078f86e01819a13594b865c2cad47bd1bca2b7063bb


LABEL org.opencontainers.image.title="skuld"
LABEL org.opencontainers.image.description="Skuld — remote AI coding session broker (Claude Code, Codex, OpenCode)"
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
    iproute2 \
    jq \
    tmux \
    vim \
    python3 \
    python3-venv \
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
COPY containers/skuld/npm-tools/package.json containers/skuld/npm-tools/package-lock.json /opt/skuld-tools/
RUN cd /opt/npm-bootstrap \
    && npm ci --omit=dev \
    && rm -rf /usr/lib/node_modules/npm \
    && ln -s /opt/npm-bootstrap/node_modules/npm /usr/lib/node_modules/npm \
    && ln -sf /usr/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && cd /opt/skuld-tools \
    && npm ci --omit=dev \
    && ln -sf /opt/skuld-tools/node_modules/.bin/claude /usr/local/bin/claude \
    && ln -sf /opt/skuld-tools/node_modules/.bin/codex /usr/local/bin/codex \
    && ln -sf /opt/skuld-tools/node_modules/.bin/opencode /usr/local/bin/opencode

# Grok Build CLI (ACP stdio transport — Scaldy / Grok pipeline) is NOT pre-installed:
# it is a large arch-specific binary distributed via the official xAI installer.
# Provide it at deploy time via one of:
#   - volumeMount the `grok` binary into PATH inside the pod, or
#   - a custom image extending this one that runs the xAI grok installer, plus
#   - XAI_API_KEY (and optional XAI_BASE_URL for the dev API) — see entrypoint.sh.
# GrokACPTransport fails gracefully at start() if `grok` is missing from PATH.

# Create app user with UID 1000 (matches helm chart runAsUser)
RUN existing=$(getent passwd 1000 | cut -d: -f1) && \
    [ -n "$existing" ] && userdel -r "$existing" || true && \
    useradd -m -s /bin/bash -u 1000 skuld && \
    groupadd -r sandbox && \
    useradd -m -s /bin/bash -g sandbox sandbox

# Copy the pre-built venv from builder.
ENV VIRTUAL_ENV=/opt/venv
COPY --from=builder /opt/venv /opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Copy entrypoint
COPY containers/skuld/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Copy svc CLI tool
COPY containers/skuld/svc /usr/local/bin/svc
RUN chmod +x /usr/local/bin/svc

# OpenShell local Docker proof bootstrap: starts the installed broker and exits
# after its health endpoint is ready, leaving the sandbox resident.
COPY scripts/openshell-run-installed-skuld.sh /usr/local/bin/openshell-run-installed-skuld
RUN chmod +x /usr/local/bin/openshell-run-installed-skuld

# Set working directory
WORKDIR /app

# Switch to non-root user
USER skuld

# Expose broker port (nginx is the entry point at 8080)
EXPOSE 8081

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8081/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
