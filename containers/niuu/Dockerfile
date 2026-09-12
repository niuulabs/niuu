# BuildKit keeps the native wheel in its existing per-platform CI cache.
# Only the installed Python environment reaches the final image.
FROM rust:1.96.1-slim-trixie@sha256:31ee7fc65186be7e0e0ccb3f2ca305f14e4739e7642a1ae65753aa5d7b874523 AS rust-toolchain

FROM python:3.14-slim-trixie@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6 AS python-build

ARG TARGETARCH

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

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
FROM node:24-alpine@sha256:a0b9bf06e4e6193cf7a0f58816cc935ff8c2a908f81e6f1a95432d679c54fbfd AS web-build
WORKDIR /app
RUN corepack enable
COPY web-next/package.json web-next/pnpm-lock.yaml web-next/pnpm-workspace.yaml ./
COPY web-next/tsconfig.base.json web-next/tsconfig.json ./
COPY web-next/build ./build
COPY web-next/apps ./apps
COPY web-next/packages ./packages
ARG APP_VERSION=dev
ENV NODE_OPTIONS="--max-old-space-size=4096"
ENV VITE_APP_VERSION=${APP_VERSION}
RUN pnpm install --frozen-lockfile
RUN pnpm build

FROM python-build AS builder
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
COPY pyproject.toml uv.lock README.md /app/
COPY src/ /app/src/
RUN uv sync --frozen --no-dev --no-editable --extra k8s --extra nats --extra openshell --extra otel \
    --extra switchyard --no-install-package nemo-switchyard \
    && mkdir -p /dist-root/data/mimir
# cli.resources.web_dist_dir() falls back to the installed package data.
COPY --from=web-build /app/apps/niuu/dist /opt/venv/lib/python3.14/site-packages/cli/web/dist
RUN --mount=type=bind,from=switchyard-wheel,source=/wheels,target=/wheels \
    uv pip install --python /opt/venv/bin/python --offline --no-deps --no-build /wheels/*.whl \
    && mkdir -p /opt/venv/share/niuu \
    && cp /wheels/switchyard-build.json /opt/venv/share/niuu/

FROM builder AS switchyard-tests
COPY bifrost.pi.example.yaml /app/
COPY tests/conftest.py tests/__init__.py /app/tests/
COPY tests/test_bifrost/ /app/tests/test_bifrost/
RUN --mount=type=bind,from=switchyard-wheel,source=/wheels,target=/wheels \
    uv sync --frozen --no-editable --extra dev --extra switchyard --no-install-package nemo-switchyard \
    && uv pip install --python /opt/venv/bin/python --offline --no-deps --no-build /wheels/*.whl \
    && /opt/venv/bin/python -m pytest tests/test_bifrost -q \
    && touch /switchyard-tests-passed

FROM python:3.14-slim-trixie@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6


# git: the platform clones session workspaces before a sandbox container starts.
RUN apt-get update && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

LABEL org.opencontainers.image.title="niuu"
LABEL org.opencontainers.image.description="Niuu — unified Python control plane services"
LABEL org.opencontainers.image.vendor="Niuu Labs"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.source="https://github.com/niuulabs/volundr"

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY --from=builder --chown=65532:65532 /opt/venv /opt/venv
COPY --from=builder --chown=65532:65532 /dist-root/data /data

USER 65532:65532

WORKDIR /app

# Runs in the final filesystem as the runtime user, before CI can publish it.
RUN --network=none --mount=type=bind,source=tests/container/switchyard_runtime.py,target=/tmp/verify_switchyard.py \
    --mount=type=bind,from=switchyard-tests,source=/switchyard-tests-passed,target=/tmp/switchyard-tests-passed \
    test -f /tmp/switchyard-tests-passed && python /tmp/verify_switchyard.py

EXPOSE 8082

ENTRYPOINT ["/opt/venv/bin/python", "-m", "uvicorn", "cli.shared_host:app", "--host", "0.0.0.0", "--port", "8082"]
