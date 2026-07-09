FROM ghcr.io/nvidia/openshell-community/sandboxes/base@sha256:aeef1c63f00e2913ea002ccb3aaf925f338b5c5d70e63576f0d95c16a138044e

USER root
WORKDIR /app

ENV UV_PROJECT_ENVIRONMENT=/opt/niuu
COPY pyproject.toml uv.lock README.md /app/
COPY src/ /app/src/
RUN uv sync --frozen --no-dev --no-editable \
    && chown -R sandbox:sandbox /opt/niuu

ENV VIRTUAL_ENV=/opt/niuu
ENV PATH="/opt/niuu/bin:$PATH"

USER sandbox
WORKDIR /sandbox
