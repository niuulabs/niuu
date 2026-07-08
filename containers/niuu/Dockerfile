FROM python:3.11-slim-bookworm@sha256:e2d3af735aff6eeee600b1933bedd99da6645fedf572cc12ef4cc1331f2ceebe AS builder

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

WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
COPY pyproject.toml uv.lock README.md /app/
COPY src/ /app/src/
RUN uv sync --frozen --no-dev --no-editable --extra k8s --extra nats \
    && mkdir -p /dist-root/data/mimir /dist-root/usr/local/bin \
    && ln -s /usr/bin/python3 /dist-root/usr/local/bin/python3 \
    && ln -s /usr/bin/python3 /dist-root/usr/local/bin/python

FROM gcr.io/distroless/python3-debian12:nonroot@sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5


LABEL org.opencontainers.image.title="niuu"
LABEL org.opencontainers.image.description="Niuu — unified Python control plane services"
LABEL org.opencontainers.image.vendor="Niuu Labs"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.source="https://github.com/niuulabs/volundr"

ENV VIRTUAL_ENV=/opt/venv
ENV PYTHONPATH=/opt/venv/lib/python3.11/site-packages
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY --from=builder --chown=65532:65532 /opt/venv /opt/venv
COPY --from=builder --chown=65532:65532 /dist-root/data /data
COPY --from=builder /dist-root/usr/local/bin /usr/local/bin

WORKDIR /app

EXPOSE 8082

ENTRYPOINT ["/usr/bin/python3", "-m", "uvicorn", "niuu.main:app", "--host", "0.0.0.0", "--port", "8082"]
