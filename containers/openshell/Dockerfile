FROM ghcr.io/nvidia/openshell-community/sandboxes/base@sha256:aeef1c63f00e2913ea002ccb3aaf925f338b5c5d70e63576f0d95c16a138044e

USER root
WORKDIR /app

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

USER sandbox
WORKDIR /sandbox
