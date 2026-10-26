# Security fixes awaiting stable CPython releases; no interpreter ABI changes.
FROM --platform=$BUILDPLATFORM python:3.14-slim-bookworm@sha256:945c2676dc2b18e0c1f16879244122c57c0ffede60006bf3df159c507a31bcae AS python-security
COPY containers/python-security/ /security/
RUN apt-get update && apt-get install -y --no-install-recommends patch \
    && python /security/build.py /patched-stdlib

FROM python:3.14-slim-bookworm@sha256:945c2676dc2b18e0c1f16879244122c57c0ffede60006bf3df159c507a31bcae AS builder

ARG TARGETARCH

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

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

WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
COPY pyproject.toml uv.lock README.md /app/
COPY src/ /app/src/
# migrations/ is force-included into the wheel as cli/migrations (see
# pyproject.toml); the build fails without it.
COPY migrations/ /app/migrations/
# vendor/envoy_authz_protos/ is one of the wheel's registered package roots
# (see pyproject.toml); the build fails without it even though this image
# does not run identity.authz_main.
COPY vendor/ /app/vendor/
RUN uv sync --frozen --no-dev --no-editable --extra k8s --extra nats

FROM python:3.14-slim-bookworm@sha256:945c2676dc2b18e0c1f16879244122c57c0ffede60006bf3df159c507a31bcae

RUN --mount=type=bind,from=python-security,source=/patched-stdlib,target=/patched-stdlib \
    --mount=type=bind,source=containers/python-security,target=/security \
    python /security/install.py /patched-stdlib && python /security/verify.py

# CI passes the UTC date, so the registry layer cache cannot keep this upgrade
# (and the security fixes it pulls) for more than a day.
ARG OS_PACKAGES_REFRESH=
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

LABEL org.opencontainers.image.title="agent"
LABEL org.opencontainers.image.description="Niuu Agent - Ravn CLI and daemon runtime"
LABEL org.opencontainers.image.vendor="Niuu Labs"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.source="https://github.com/niuulabs/volundr"

RUN existing=$(getent passwd 1000 | cut -d: -f1) && \
    [ -n "$existing" ] && userdel -r "$existing" || true && \
    useradd -m -s /usr/sbin/nologin -u 1000 agent

ENV VIRTUAL_ENV=/opt/venv
COPY --from=builder /opt/venv /opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY containers/agent/entrypoint.py /app/entrypoint.py
RUN mkdir -p /workspace /etc/ravn /home/agent/.ravn \
    && chown -R agent:agent /workspace /etc/ravn /home/agent/.ravn

WORKDIR /workspace
USER agent

ENTRYPOINT ["python", "/app/entrypoint.py"]
