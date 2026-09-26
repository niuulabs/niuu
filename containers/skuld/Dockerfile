FROM ubuntu:26.04@sha256:da6fc2be547864451aa253836dd926da33623312df4a9a243e35dc877c378a78 AS builder

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
    UV_VERSION="0.12.13"; \
    case "${TARGETARCH}" in \
      amd64) UV_ARCHIVE="uv-x86_64-unknown-linux-gnu.tar.gz"; UV_SHA256="745765a3b6e360ad76743599ae5c42e9278c7edf8bbff9fc76d05bf2623a04dd" ;; \
      arm64) UV_ARCHIVE="uv-aarch64-unknown-linux-gnu.tar.gz"; UV_SHA256="2eaa5d94f5db7b3a1a092156b9420459e42ab0217d917fe74a876309cef9b5e9" ;; \
      *) echo "Unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${UV_ARCHIVE}" -o "/tmp/${UV_ARCHIVE}"; \
    echo "${UV_SHA256}  /tmp/${UV_ARCHIVE}" | sha256sum -c -; \
    tar -xzf "/tmp/${UV_ARCHIVE}" -C /tmp; \
    install -m 0755 "/tmp/${UV_ARCHIVE%.tar.gz}/uv" /usr/local/bin/uv; \
    rm -rf "/tmp/${UV_ARCHIVE}" "/tmp/${UV_ARCHIVE%.tar.gz}"

# Dependency venv only. It resolves from the lockfile alone, so it survives
# source edits; the runtime image copies it as one layer that session nodes
# keep cached until uv.lock changes.
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
COPY pyproject.toml uv.lock README.md /app/
# --extra dsh bundles the DeepSeek Harness runtime (single-file exe, exact-pinned)
# for DshJsonRpcTransport; the binary needs no Node at runtime.
RUN uv sync --frozen --no-dev --no-editable --no-install-project --extra otel --extra dsh

# ---------------------------------------------------------------------------
# Project wheel — the only part of the Python install a source edit changes
# ---------------------------------------------------------------------------
FROM builder AS project
COPY src/ /app/src/
# vendor/envoy_authz_protos/ provides the envoy.service.auth.v3 ext_authz
# bindings identity/authz_main.py needs (see its README); vendored instead of
# depending on xds-protos, which bundled a copy of opentelemetry/proto/** that
# collided with the otel extra installed above.
COPY vendor/ /app/vendor/
RUN uv build --wheel --out-dir /dist

# ---------------------------------------------------------------------------
# Runtime — Node.js stays (needed for claude-code/codex CLI at runtime)
# ---------------------------------------------------------------------------
FROM ubuntu:26.04@sha256:da6fc2be547864451aa253836dd926da33623312df4a9a243e35dc877c378a78


LABEL org.opencontainers.image.title="skuld"
LABEL org.opencontainers.image.description="Skuld — remote AI coding session broker (Claude Code, Codex, Grok, OpenCode, DeepSeek Harness)"
LABEL org.opencontainers.image.vendor="Niuu Labs"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.source="https://github.com/niuulabs/volundr"

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Copy the dependency venv before the daily OS refresh below, so that refresh
# does not rebuild (and make every session node re-pull) this large layer.
ENV VIRTUAL_ENV=/opt/venv
COPY --from=builder /opt/venv /opt/venv
# Repository startup hooks also need the pinned tooling installer at runtime.
COPY --from=builder /usr/local/bin/uv /usr/local/bin/uv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# CI passes the UTC date, so the registry layer cache cannot keep this upgrade
# (and the security fixes it pulls) for more than a day.
ARG OS_PACKAGES_REFRESH=
# Install runtime dependencies. The upgrade picks up base-image security
# fixes (glibc, gnupg) published after the pinned digest was cut.
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    git \
    file \
    iproute2 \
    jq \
    libatomic1 \
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

# GitHub CLI (gh) from GitHub's apt repository: sessions open and inspect PRs with it.
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod 0644 /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/* \
    && gh --version

# GitLab CLI (glab), pinned release .deb per architecture: the GitLab counterpart of gh.
# ARG values do not cross stages; TARGETARCH must be declared again here.
ARG TARGETARCH
ARG GLAB_VERSION=1.117.0
RUN curl -fsSL -o /tmp/glab.deb \
    "https://gitlab.com/api/v4/projects/gitlab-org%2Fcli/packages/generic/glab/${GLAB_VERSION}/glab_${GLAB_VERSION}_linux_${TARGETARCH}.deb" \
    && dpkg -i /tmp/glab.deb && rm -f /tmp/glab.deb \
    && glab --version

# Upgrade npm and install the CLI tools from checked-in lockfiles.
COPY containers/node/npm-bootstrap/package.json containers/node/npm-bootstrap/package-lock.json /opt/npm-bootstrap/
COPY containers/skuld/npm-tools/package.json containers/skuld/npm-tools/package-lock.json /opt/skuld-tools/
RUN cd /opt/npm-bootstrap \
    && GROK_HOME=/tmp/grok-install npm ci --omit=dev --libc=glibc --cache /tmp/npm-cache \
    && rm -rf /tmp/npm-cache /tmp/grok-install \
    && rm -rf /usr/lib/node_modules/npm \
    && ln -s /opt/npm-bootstrap/node_modules/npm /usr/lib/node_modules/npm \
    && ln -sf /usr/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && cd /opt/skuld-tools \
    && GROK_HOME=/tmp/grok-install npm ci --omit=dev --libc=glibc --cache /tmp/npm-cache \
    && rm -rf /tmp/npm-cache /tmp/grok-install \
    && ln -sf /opt/skuld-tools/node_modules/.bin/claude /usr/local/bin/claude \
    && ln -sf /opt/skuld-tools/node_modules/.bin/codex /usr/local/bin/codex \
    && ln -sf /opt/skuld-tools/node_modules/.bin/opencode /usr/local/bin/opencode \
    && ln -sf /opt/skuld-tools/node_modules/.bin/grok /usr/local/bin/grok \
    && ln -sf /opt/skuld-tools/node_modules/.bin/pi /usr/local/bin/pi

# Grok Build CLI (ACP stdio transport and the xAI device sign-in) ships from the
# @xai-official/grok npm package above. Sessions authenticate with XAI_API_KEY or
# the ~/.grok/auth.json the platform mounts after a wizard sign-in.

# Pinned Muse is shared with the OpenShell image.
COPY containers/skuld/install-muse.sh /tmp/install-muse.sh
RUN sh /tmp/install-muse.sh "$TARGETARCH" && rm /tmp/install-muse.sh

# Create app user with UID 1000 (matches helm chart runAsUser)
RUN existing=$(getent passwd 1000 | cut -d: -f1) && \
    [ -n "$existing" ] && userdel -r "$existing" || true && \
    useradd -m -s /bin/bash -u 1000 skuld && \
    groupadd -r sandbox && \
    useradd -m -s /bin/bash -g sandbox sandbox

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

# Install the project last, into the dependency venv: a source edit then ships
# only this small layer. Dependencies are already present from the lockfile.
RUN --network=none --mount=type=bind,from=project,source=/dist,target=/tmp/dist \
    uv pip install --python /opt/venv/bin/python --no-deps --no-cache --offline /tmp/dist/*.whl

# Fail the image build if an advertised CLI is missing or cannot execute.
RUN claude --version && codex --version && opencode --version && grok --version && pi --version && muse --version

# Switch to non-root user
USER skuld
RUN --network=none --mount=type=bind,source=tests/container/mesh_runtime.py,target=/tmp/verify_mesh.py \
    python /tmp/verify_mesh.py
RUN --network=none --mount=type=bind,source=tests/container/observability_runtime.py,target=/tmp/verify_observability.py \
    python /tmp/verify_observability.py

# Expose broker port (nginx is the entry point at 8080)
EXPOSE 8081

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8081/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
