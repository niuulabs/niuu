FROM ubuntu:26.04@sha256:c098726dd9b9733f3e885f8de0ef375206a311b0a317eadc40d347b1115fb944 AS builder

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
    UV_VERSION="0.13.5"; \
    case "${TARGETARCH}" in \
      amd64) UV_ARCHIVE="uv-x86_64-unknown-linux-gnu.tar.gz"; UV_SHA256="f68d291c75783122dad58ab77def0e13f3233f358731db5d6d7e8715603b928f" ;; \
      arm64) UV_ARCHIVE="uv-aarch64-unknown-linux-gnu.tar.gz"; UV_SHA256="4bb69cf95ed13f23713bb7bc09f241896ea0c75b7c557e615a20604b82f9c403" ;; \
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
# --extra nats: a resident's ravn sidecar runs this image, and a resident profile
# whose mesh uses NATS (Valhalla's ravn-helm) cannot start without nats-py.
RUN uv sync --frozen --no-dev --no-editable --no-install-project --extra otel --extra dsh --extra nats

# ---------------------------------------------------------------------------
# Project wheel — the only part of the Python install a source edit changes
# ---------------------------------------------------------------------------
FROM builder AS project
COPY src/ /app/src/
# migrations/ is force-included into the wheel as cli/migrations (see
# pyproject.toml); the build fails without it.
COPY migrations/ /app/migrations/
# vendor/envoy_authz_protos/ provides the envoy.service.auth.v3 ext_authz
# bindings identity/authz_main.py needs (see its README); vendored instead of
# depending on xds-protos, which bundled a copy of opentelemetry/proto/** that
# collided with the otel extra installed above.
COPY vendor/ /app/vendor/
RUN uv build --wheel --out-dir /dist

# ---------------------------------------------------------------------------
# gh v2.102.0 (the latest release) is built with Go 1.27.1 and golang.org/x/net
# v0.58.0 (GO-2026-6603/6604/6611/6612). Build the same release with Go 1.27.3 and
# the patched module; return to GitHub's apt package once a release ships them.
# ---------------------------------------------------------------------------
FROM --platform=$BUILDPLATFORM golang:1.27.3-alpine3.23@sha256:754d5200fc3edae76d3b6598a9a3fac7e74981ee6a5413ab955025f185753ef2 AS gh-build
ARG TARGETARCH
WORKDIR /src
RUN wget -q https://github.com/cli/cli/archive/refs/tags/v2.102.0.tar.gz -O /tmp/gh.tar.gz \
    && echo "08bf0ef8b4409893889175e0f5279d30d6edd96465816042c0d7c72eac598158  /tmp/gh.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/gh.tar.gz --strip-components=1 \
    && go get golang.org/x/net@v0.60.0 \
    && CGO_ENABLED=0 GOOS=linux GOARCH=$TARGETARCH go build -trimpath \
        -ldflags '-s -w -X github.com/cli/cli/v2/internal/build.Version=2.102.0-niuu.1 -X github.com/cli/cli/v2/internal/build.Date=2026-09-30' \
        -o /out/gh ./cmd/gh

# ---------------------------------------------------------------------------
# glab v1.124.0 (the latest release) ships binaries built with Go 1.27.2
# (GO-2026-6809). Build the same release with Go 1.27.3; the module proxy
# download is verified against sum.golang.org. Return to the release .deb once
# one is built with a patched Go.
# ---------------------------------------------------------------------------
FROM --platform=$BUILDPLATFORM golang:1.27.3-alpine3.23@sha256:754d5200fc3edae76d3b6598a9a3fac7e74981ee6a5413ab955025f185753ef2 AS glab-build
ARG TARGETARCH
ARG GLAB_VERSION=1.124.0
RUN cd "$(go mod download -json gitlab.com/gitlab-org/cli@v${GLAB_VERSION} | sed -n 's/.*"Dir": "\(.*\)".*/\1/p')" \
    && CGO_ENABLED=0 GOOS=linux GOARCH=$TARGETARCH go build -trimpath \
        -ldflags "-s -w -X main.version=${GLAB_VERSION}-niuu.1" \
        -o /out/glab ./cmd/glab

# ---------------------------------------------------------------------------
# Runtime — Node.js stays (needed for claude-code/codex CLI at runtime)
# ---------------------------------------------------------------------------
FROM ubuntu:26.04@sha256:c098726dd9b9733f3e885f8de0ef375206a311b0a317eadc40d347b1115fb944


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
# bubblewrap: Codex looks for bwrap on PATH at startup for its Linux sandbox.
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    bubblewrap \
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

# The Ubuntu rock ships Pebble as its service manager. Skuld runs its own
# entrypoint and never starts it, so drop the unused binary and its Go CVEs.
RUN rm -f /usr/bin/pebble

# Install Node.js 24 LTS (pinned keyring + apt source instead of curl|bash)
RUN curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
    -o /usr/share/keyrings/nodesource.asc \
    && echo "deb [signed-by=/usr/share/keyrings/nodesource.asc] https://deb.nodesource.com/node_24.x nodistro main" \
    > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# GitHub CLI (gh): sessions open and inspect PRs with it.
COPY --from=gh-build /out/gh /usr/local/bin/gh
RUN gh --version

# GitLab CLI (glab): the GitLab counterpart of gh. /usr/bin is the path the
# release .deb used and the OpenShell egress policies name.
COPY --from=glab-build /out/glab /usr/bin/glab
RUN glab --version

# ARG values do not cross stages; TARGETARCH must be declared again here.
ARG TARGETARCH

# Upgrade npm and install the CLI tools from checked-in lockfiles.
COPY containers/node/npm-bootstrap/package.json containers/node/npm-bootstrap/package-lock.json containers/node/npm-bootstrap/replace-nested.sh /opt/npm-bootstrap/
COPY containers/skuld/npm-tools/package.json containers/skuld/npm-tools/package-lock.json /opt/skuld-tools/
RUN cd /opt/npm-bootstrap \
    && GROK_HOME=/tmp/grok-install npm ci --omit=dev --libc=glibc --cache /tmp/npm-cache \
    && rm -rf /tmp/npm-cache /tmp/grok-install \
    && sh /opt/npm-bootstrap/replace-nested.sh /opt/npm-bootstrap node_modules/npm/node_modules brace-expansion ip-address undici \
    && rm -rf /usr/lib/node_modules/npm \
    && ln -s /opt/npm-bootstrap/node_modules/npm /usr/lib/node_modules/npm \
    && ln -sf /usr/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && cd /opt/skuld-tools \
    && GROK_HOME=/tmp/grok-install npm ci --omit=dev --libc=glibc --cache /tmp/npm-cache \
    && rm -rf /tmp/npm-cache /tmp/grok-install \
    && sh /opt/npm-bootstrap/replace-nested.sh /opt/skuld-tools node_modules/@earendil-works/pi-coding-agent/node_modules undici \
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
RUN claude --version && codex --version && opencode --version && grok --version && pi --version && muse --version \
    && bwrap --version

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
