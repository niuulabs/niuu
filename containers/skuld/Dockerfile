FROM ubuntu:26.04@sha256:f3d28607ddd78734bb7f71f117f3c6706c666b8b76cbff7c9ff6e5718d46ff64 AS builder


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

# Copy volundr package and install
COPY pyproject.toml README.md /app/
COPY src/ /app/src/
RUN pip install --no-cache-dir /app

# Remove pip from the venv so it is absent from the runtime image
RUN pip uninstall -y pip

# ---------------------------------------------------------------------------
# Runtime — Node.js stays (needed for claude-code/codex CLI at runtime)
# ---------------------------------------------------------------------------
FROM ubuntu:26.04@sha256:f3d28607ddd78734bb7f71f117f3c6706c666b8b76cbff7c9ff6e5718d46ff64


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
    jq \
    tmux \
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

# Upgrade bundled npm to fix GHSA-qffp-2rhf-9h96 (tar) and GHSA-*-minimatch CVEs
RUN npm install -g npm@11.13.0

# Install Claude Code CLI globally via npm
RUN npm install -g @anthropic-ai/claude-code@2.1.119

# Install OpenAI Codex CLI globally via npm
RUN npm install -g @openai/codex@0.125.0

# Install OpenCode (opencode.ai) globally via npm
RUN npm install -g opencode-ai@1.14.29

# Create app user with UID 1000 (matches helm chart runAsUser)
RUN existing=$(getent passwd 1000 | cut -d: -f1) && \
    [ -n "$existing" ] && userdel -r "$existing" || true && \
    useradd -m -s /bin/bash -u 1000 skuld

# Copy the pre-built venv from builder (no pip, no hatchling)
ENV VIRTUAL_ENV=/opt/venv
COPY --from=builder /opt/venv /opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Copy entrypoint
COPY containers/skuld/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Copy svc CLI tool
COPY containers/skuld/svc /usr/local/bin/svc
RUN chmod +x /usr/local/bin/svc

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
