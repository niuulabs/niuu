# Security fixes awaiting stable CPython releases; no interpreter ABI changes.
FROM --platform=$BUILDPLATFORM python:3.14-slim-bookworm@sha256:516b51cb9db2ef061e465c803e50f64361b82cefd91e525b889b967b83e83527 AS python-security
COPY containers/python-security/ /security/
RUN apt-get update && apt-get install -y --no-install-recommends patch \
    && python /security/build.py /patched-stdlib

# Compose v5.7.0 already requires the patched containerd v2.4.2 and golang.org/x/net
# v0.60.0 (GO-2026-6603/6611/6612) but still pins klauspost/compress v1.20.0
# (GO-2026-6865, fixed in v1.20.2), and its published binary is built with Go 1.26.10.
# Build the release with Go 1.27.3 and klauspost/compress v1.20.3 (the version gh
# v2.103.3 ships) until upstream moves past v1.20.0.
FROM --platform=$BUILDPLATFORM golang:1.27.3-alpine3.23@sha256:754d5200fc3edae76d3b6598a9a3fac7e74981ee6a5413ab955025f185753ef2 AS compose-build
ARG TARGETARCH
WORKDIR /src
RUN wget -q https://github.com/docker/compose/archive/refs/tags/v5.7.0.tar.gz -O /tmp/compose.tar.gz \
    && echo "98f5b7dd4a66d8a2d9289e918fa0f63e5ff9821623950e53e633571a78b30d63  /tmp/compose.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/compose.tar.gz --strip-components=1 \
    && go get github.com/klauspost/compress@v1.20.3 \
    && CGO_ENABLED=0 GOOS=linux GOARCH=$TARGETARCH go build -trimpath -tags e2e \
        -ldflags '-w -X github.com/docker/compose/v5/internal.Version=v5.7.0-niuu.1' \
        -o /out/docker-compose ./cmd

# gh v2.103.3 requires klauspost/compress v1.20.3 in its go.mod
# (GO-2026-6865, fixed in v1.20.2) and golang.org/x/net v0.60.0 (GO-2026-6603/6604/
# 6611/6612), so no module override is needed. Build it from the checksum-verified
# source with Go 1.27.3, which satisfies its `toolchain go1.27.3`, to keep the
# version pinned and the toolchain current.
FROM --platform=$BUILDPLATFORM golang:1.27.3-alpine3.23@sha256:754d5200fc3edae76d3b6598a9a3fac7e74981ee6a5413ab955025f185753ef2 AS gh-build
ARG TARGETARCH
WORKDIR /src
RUN wget -q https://github.com/cli/cli/archive/refs/tags/v2.103.3.tar.gz -O /tmp/gh.tar.gz \
    && echo "bb148138a3a2fe023e57c78042fe3b42fc09171c0e8fff4cf983b227b7a2eade  /tmp/gh.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/gh.tar.gz --strip-components=1 \
    && CGO_ENABLED=0 GOOS=linux GOARCH=$TARGETARCH go build -trimpath \
        -ldflags '-s -w -X github.com/cli/cli/v2/internal/build.Version=2.103.3-niuu.1 -X github.com/cli/cli/v2/internal/build.Date=2026-10-26' \
        -o /out/gh ./cmd/gh

# BuildKit keeps the native wheel in its existing per-platform CI cache.
# Only the installed Python environment reaches the final image.
FROM rust:1.99.0-slim-trixie@sha256:2752b332db73fdbb7dc576f06c82ed1f312005784ef913d7e04a28f5f55dc581 AS rust-toolchain

FROM python:3.14-slim-trixie@sha256:cd89c30eda26ee911a4611b18680086e0ea42e6f5f8cbe653d07fa7958c9cec8 AS python-build

ARG TARGETARCH

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    UV_VERSION="0.13.7"; \
    case "${TARGETARCH}" in \
      amd64) UV_ARCHIVE="uv-x86_64-unknown-linux-gnu.tar.gz"; UV_SHA256="c5221ff41b7892bfc223543883600125f6a47fe845093e0f5bdff4f359196d83" ;; \
      arm64) UV_ARCHIVE="uv-aarch64-unknown-linux-gnu.tar.gz"; UV_SHA256="71279f8d3c67241e807b9d8db872d1bf541579fb9f08b7d1eb1682196d1e57dc" ;; \
      *) echo "Unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${UV_ARCHIVE}" -o "/tmp/${UV_ARCHIVE}"; \
    echo "${UV_SHA256}  /tmp/${UV_ARCHIVE}" | sha256sum -c -; \
    tar -xzf "/tmp/${UV_ARCHIVE}" -C /tmp; \
    install -m 0755 "/tmp/${UV_ARCHIVE%.tar.gz}/uv" /usr/local/bin/uv; \
    rm -rf "/tmp/${UV_ARCHIVE}" "/tmp/${UV_ARCHIVE%.tar.gz}"

FROM python-build AS switchyard-wheel
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git cmake pkg-config \
    && rm -rf /var/lib/apt/lists/*
COPY --from=rust-toolchain /usr/local/cargo /usr/local/cargo
COPY --from=rust-toolchain /usr/local/rustup /usr/local/rustup
ENV CARGO_HOME=/usr/local/cargo RUSTUP_HOME=/usr/local/rustup \
    RUSTUP_TOOLCHAIN=1.96.1 PATH="/usr/local/cargo/bin:$PATH"
WORKDIR /build
COPY uv.lock /build/uv.lock
COPY containers/niuu/build_switchyard.py containers/niuu/switchyard-build-constraints.txt /build/
RUN python build_switchyard.py

# Web UI assets are baked into the platform image so one container serves the
# API and the SPA on a single port (spark mode). Same build as containers/niuu-web.
FROM node:24-alpine@sha256:06d7adf3e0ef61a13f1d28682726366f97a15aa1888c934d3ecec86c33918b53 AS web-build
WORKDIR /app
RUN corepack enable
ENV NODE_OPTIONS="--max-old-space-size=4096"
COPY web-next/package.json web-next/pnpm-lock.yaml web-next/pnpm-workspace.yaml ./
# Fetch needs only the lockfile, so the package store survives source edits.
RUN pnpm fetch
COPY web-next/tsconfig.base.json web-next/tsconfig.json ./
COPY web-next/build ./build
COPY web-next/apps ./apps
COPY web-next/packages ./packages
RUN pnpm install --frozen-lockfile --offline
# Declared last: callers pass the commit sha, which must not invalidate the install.
ARG APP_VERSION=dev
ENV VITE_APP_VERSION=${APP_VERSION}
RUN pnpm build

FROM python-build AS builder
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
COPY pyproject.toml uv.lock README.md /app/
# Build the pinned OpenShell SDK from local source, without release downloads.
COPY vendor/openshell/ /app/vendor/openshell/
# Dependencies resolve from the lockfile alone, so this layer survives source
# edits; only the project itself is installed after src/ is copied.
RUN uv sync --frozen --no-dev --no-editable --no-install-project --extra cli --extra k8s --extra nats \
    --extra openshell --extra otel --extra provenance --extra switchyard \
    --no-install-package nemo-switchyard
COPY src/ /app/src/
# migrations/ is force-included into the wheel as cli/migrations, where
# Volundr and Ting run them at startup (cli.resources.migration_dir).
COPY migrations/ /app/migrations/
# vendor/envoy_authz_protos/ provides the envoy.service.auth.v3 ext_authz
# bindings identity/authz_main.py needs (see its README); vendored instead of
# depending on xds-protos, which bundled a copy of opentelemetry/proto/** that
# collided with the otel extra installed above.
COPY vendor/ /app/vendor/
RUN uv sync --frozen --no-dev --no-editable --extra cli --extra k8s --extra nats --extra openshell \
    --extra otel --extra provenance --extra switchyard --no-install-package nemo-switchyard \
    && mkdir -p /dist-root/data/mimir
# cli.resources.web_dist_dir() falls back to the installed package data.
COPY --from=web-build /app/apps/niuu/dist /opt/venv/lib/python3.14/site-packages/cli/web/dist
RUN --mount=type=bind,from=switchyard-wheel,source=/wheels,target=/wheels \
    uv pip install --python /opt/venv/bin/python --offline --no-deps --no-build /wheels/*.whl \
    && mkdir -p /opt/venv/share/niuu \
    && cp /wheels/switchyard-build.json /opt/venv/share/niuu/

FROM builder AS switchyard-tests
COPY examples/bifrost-pi.yaml /app/examples/
COPY tests/conftest.py tests/__init__.py /app/tests/
COPY tests/test_bifrost/ /app/tests/test_bifrost/
RUN --mount=type=bind,from=switchyard-wheel,source=/wheels,target=/wheels \
    uv sync --frozen --no-editable --extra dev --extra switchyard --no-install-package nemo-switchyard \
    && uv pip install --python /opt/venv/bin/python --offline --no-deps --no-build /wheels/*.whl \
    && /opt/venv/bin/python -m pytest tests/test_bifrost -q \
    && touch /switchyard-tests-passed

FROM python:3.14-slim-trixie@sha256:cd89c30eda26ee911a4611b18680086e0ea42e6f5f8cbe653d07fa7958c9cec8

RUN --mount=type=bind,from=python-security,source=/patched-stdlib,target=/patched-stdlib \
    --mount=type=bind,source=containers/python-security,target=/security \
    python /security/install.py /patched-stdlib && python /security/verify.py


# CI passes the UTC date, so the registry layer cache cannot keep this upgrade
# (and the security fixes it pulls) for more than a day.
ARG OS_PACKAGES_REFRESH=
# git: the platform clones session workspaces before a sandbox container starts.
# openssh-client: VM-backed sessions bootstrap and tunnel to their guest over SSH.
# curl: fetches the docker CLI below.
# openssh-server: the volundr chart's host connect endpoint (hostConnect) runs this image's
# sshd as a sidecar; registered hosts behind NAT log in as niuu-connect, which has no
# password and no shell ('*' rather than a locked '!', which sshd would refuse). No sshd
# runs otherwise, and the image carries no host keys: the endpoint mounts its own.
RUN apt-get update && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        git openssh-client openssh-server libnss-wrapper ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* /etc/ssh/ssh_host_*_key /etc/ssh/ssh_host_*_key.pub \
    && useradd --system --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin niuu-connect \
    && usermod -p '*' niuu-connect

# docker CLI + compose plugin: in docker mode this image is also the host-side
# CLI (`niuu up` runs from a container against the host's Docker socket, see
# scripts/install.sh), and `niuu up` drives `docker compose`.
ARG TARGETARCH
ARG DOCKER_CLI_VERSION=29.9.2
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) arch=x86_64 ;; \
      arm64) arch=aarch64 ;; \
      *) echo "unsupported TARGETARCH: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    curl -fsSL --retry 3 --retry-all-errors \
      -o /tmp/docker.tgz \
      "https://download.docker.com/linux/static/stable/${arch}/docker-${DOCKER_CLI_VERSION}.tgz"; \
    tar -xzf /tmp/docker.tgz -C /usr/local/bin --strip-components=1 docker/docker; \
    rm /tmp/docker.tgz; \
    docker --version
COPY --from=compose-build /out/docker-compose /usr/local/lib/docker/cli-plugins/docker-compose
RUN docker compose version

# GitHub CLI (gh): the platform's git workflow reads PR status with it.
COPY --from=gh-build /out/gh /usr/local/bin/gh
RUN gh --version

LABEL org.opencontainers.image.title="niuu"
LABEL org.opencontainers.image.description="Niuu — unified Python control plane services"
LABEL org.opencontainers.image.vendor="Niuu Labs"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.source="https://github.com/niuulabs/volundr"

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY --from=builder --chown=65532:65532 /opt/venv /opt/venv
COPY --from=builder --chown=65532:65532 /dist-root/data /data

RUN --network=none ssh -V && test -x /usr/sbin/sshd && id niuu-connect \
    && test -z "$(ls /etc/ssh/ssh_host_* 2>/dev/null)"

USER 65532:65532

WORKDIR /app

# Runs in the final filesystem as the runtime user, before CI can publish it.
RUN --network=none --mount=type=bind,source=tests/container/switchyard_runtime.py,target=/tmp/verify_switchyard.py \
    --mount=type=bind,from=switchyard-tests,source=/switchyard-tests-passed,target=/tmp/switchyard-tests-passed \
    test -f /tmp/switchyard-tests-passed && python /tmp/verify_switchyard.py
RUN --network=none --mount=type=bind,source=tests/container/observability_runtime.py,target=/tmp/verify_observability.py \
    python /tmp/verify_observability.py

EXPOSE 8082

ENTRYPOINT ["/opt/venv/bin/python", "-m", "uvicorn", "cli.shared_host:app", "--host", "0.0.0.0", "--port", "8082"]
