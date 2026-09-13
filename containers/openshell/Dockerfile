FROM ghcr.io/astral-sh/uv:0.12.13@sha256:b485bd65cc2cf1c9a93b3554012c9c3778cf7b1b5fd3d3096ce9e1226c97e1e6 AS uv

FROM ghcr.io/nvidia/openshell-community/sandboxes/base@sha256:aeef1c63f00e2913ea002ccb3aaf925f338b5c5d70e63576f0d95c16a138044e AS base

USER root
WORKDIR /app

COPY --from=uv /uv /uvx /usr/local/bin/
# Refresh inherited runtime packages and the Python managed by uv.
RUN apt-get update && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/* \
    && UV_PYTHON_INSTALL_DIR=/sandbox/.uv/python uv python upgrade 3.14 \
    && PYTHON_BIN=$(UV_PYTHON_INSTALL_DIR=/sandbox/.uv/python uv python find 3.14.7) \
    && ln -sf "$PYTHON_BIN" /usr/local/bin/python \
    && ln -sf "$PYTHON_BIN" /usr/local/bin/python3 \
    && ln -sf "$PYTHON_BIN" /sandbox/.venv/bin/python \
    && sed -i 's/3.14.3/3.14.7/g' /sandbox/.venv/pyvenv.cfg \
    && UV_PYTHON_INSTALL_DIR=/sandbox/.uv/python uv python uninstall 3.14.3 \
    && uv pip install --python /sandbox/.venv/bin/python pip==26.2.1

COPY containers/node/npm-bootstrap/package.json containers/node/npm-bootstrap/package-lock.json /opt/npm-bootstrap/
RUN cd /opt/npm-bootstrap && GROK_HOME=/tmp/grok-install npm ci --omit=dev --libc=glibc --cache /tmp/npm-cache \
    && rm -rf /tmp/npm-cache /tmp/grok-install \
    && rm -rf /usr/lib/node_modules/npm \
    && ln -s /opt/npm-bootstrap/node_modules/npm /usr/lib/node_modules/npm \
    && ln -sf /usr/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

COPY containers/openshell/npm-tools/package.json containers/openshell/npm-tools/package-lock.json /opt/openshell-tools/
RUN cd /opt/openshell-tools && GROK_HOME=/tmp/grok-install npm ci --omit=dev --libc=glibc --cache /tmp/npm-cache \
    && rm -rf /tmp/npm-cache /tmp/grok-install \
    && rm -rf /usr/lib/node_modules/@github /usr/lib/node_modules/@hono /usr/lib/node_modules/tar \
    && ln -s /opt/openshell-tools/node_modules/@github /usr/lib/node_modules/@github \
    && ln -s /opt/openshell-tools/node_modules/@hono /usr/lib/node_modules/@hono \
    && ln -s /opt/openshell-tools/node_modules/tar /usr/lib/node_modules/tar \
    && ln -sf /opt/openshell-tools/node_modules/.bin/copilot /usr/local/bin/copilot

# Remove superseded upstream agents and installation caches before exporting
# the base filesystem. Deleting them in the final image would retain their layers.
RUN rm -rf /usr/lib/node_modules/opencode-ai /usr/lib/node_modules/@openai \
        /root/.local/share/claude /root/.local/bin/claude \
        /root/.npm /root/.cache /var/cache/apt/archives/* \
    && rm -f /usr/local/bin/claude /usr/local/bin/codex /usr/local/bin/opencode \
        /usr/bin/codex /usr/bin/opencode

FROM scratch
COPY --from=base / /
# COPY preserves the filesystem, but not the upstream image configuration.
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_INSTALL_DIR=/sandbox/.uv/python \
    PATH=/sandbox/.venv/bin:/usr/local/bin:/usr/bin:/bin \
    VIRTUAL_ENV=/sandbox/.venv
USER root
WORKDIR /app
ENTRYPOINT ["/bin/bash"]

# OpenShell's base image includes agent CLIs, but their versions are outside
# Niuu's release lifecycle. Install the same locked tools used by Skuld.
COPY containers/skuld/npm-tools/package.json containers/skuld/npm-tools/package-lock.json /opt/skuld-tools/
RUN cd /opt/skuld-tools \
    && GROK_HOME=/tmp/grok-install npm ci --omit=dev --libc=glibc --cache /tmp/npm-cache \
    && rm -rf /tmp/npm-cache /tmp/grok-install \
    && rm -rf /usr/lib/node_modules/@openai \
    && mkdir -p /usr/lib/node_modules \
    && mv /opt/skuld-tools/node_modules/@openai /usr/lib/node_modules/@openai \
    && ln -s /usr/lib/node_modules/@openai /opt/skuld-tools/node_modules/@openai \
    && ln -sf /opt/skuld-tools/node_modules/.bin/claude /usr/local/bin/claude \
    && ln -sf /usr/lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex \
    && ln -sf /opt/skuld-tools/node_modules/.bin/opencode /usr/local/bin/opencode \
    && ln -sf /opt/skuld-tools/node_modules/.bin/grok /usr/local/bin/grok

ENV UV_PROJECT_ENVIRONMENT=/opt/niuu
COPY pyproject.toml uv.lock README.md /app/
COPY src/ /app/src/
COPY scripts/openshell-run-installed-skuld.sh /usr/local/bin/openshell-run-installed-skuld
RUN uv sync --frozen --no-dev --no-editable --no-cache --extra nats --extra dsh \
    && mkdir -p /run/secrets \
    && chmod 0755 /usr/local/bin/openshell-run-installed-skuld \
    && chown -R sandbox:sandbox /opt/niuu /run/secrets

ENV VIRTUAL_ENV=/opt/niuu
ENV PATH="/opt/niuu/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends file \
    && rm -rf /var/lib/apt/lists/*

# Fail the image build if an advertised CLI is missing or cannot execute.
RUN test ! -e /usr/lib/node_modules/opencode-ai \
    && test ! -e /root/.local/share/claude \
    && test ! -e /root/.npm \
    && claude --version && codex --version && opencode --version && grok --version

USER sandbox
WORKDIR /sandbox
