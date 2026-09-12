FROM ghcr.io/astral-sh/uv:0.12.13@sha256:b485bd65cc2cf1c9a93b3554012c9c3778cf7b1b5fd3d3096ce9e1226c97e1e6 AS uv

FROM ghcr.io/nvidia/openshell-community/sandboxes/base@sha256:aeef1c63f00e2913ea002ccb3aaf925f338b5c5d70e63576f0d95c16a138044e

USER root
WORKDIR /app

COPY --from=uv /uv /uvx /usr/local/bin/
# Refresh inherited runtime packages and the Python managed by uv.
RUN apt-get update && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/* \
    && UV_PYTHON_INSTALL_DIR=/sandbox/.uv/python uv python upgrade 3.14

COPY containers/node/npm-bootstrap/package.json containers/node/npm-bootstrap/package-lock.json /opt/npm-bootstrap/
RUN cd /opt/npm-bootstrap && npm ci --omit=dev \
    && rm -rf /usr/lib/node_modules/npm \
    && ln -s /opt/npm-bootstrap/node_modules/npm /usr/lib/node_modules/npm \
    && ln -sf /usr/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# OpenShell's base image includes agent CLIs, but their versions are outside
# Niuu's release lifecycle. Install the same locked tools used by Skuld.
COPY containers/skuld/npm-tools/package.json containers/skuld/npm-tools/package-lock.json /opt/skuld-tools/
RUN cd /opt/skuld-tools \
    && npm ci --omit=dev \
    && rm -rf /usr/lib/node_modules/@openai \
    && mkdir -p /usr/lib/node_modules \
    && cp -a /opt/skuld-tools/node_modules/@openai /usr/lib/node_modules/@openai \
    && ln -sf /opt/skuld-tools/node_modules/.bin/claude /usr/local/bin/claude \
    && ln -sf /usr/lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex \
    && ln -sf /opt/skuld-tools/node_modules/.bin/opencode /usr/local/bin/opencode

ENV UV_PROJECT_ENVIRONMENT=/opt/niuu
COPY pyproject.toml uv.lock README.md /app/
COPY src/ /app/src/
COPY scripts/openshell-run-installed-skuld.sh /usr/local/bin/openshell-run-installed-skuld
RUN uv sync --frozen --no-dev --no-editable --extra nats \
    && mkdir -p /run/secrets \
    && chmod 0755 /usr/local/bin/openshell-run-installed-skuld \
    && chown -R sandbox:sandbox /opt/niuu /run/secrets

ENV VIRTUAL_ENV=/opt/niuu
ENV PATH="/opt/niuu/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends file \
    && rm -rf /var/lib/apt/lists/*

USER sandbox
WORKDIR /sandbox
