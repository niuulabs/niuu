FROM python:3.14-slim-bookworm@sha256:86f975aca15cf04a40b399eebede9aea7c82eae084d1f1a0a6ef6bcaae871a30

ARG TARGETARCH


LABEL org.opencontainers.image.title="devrunner"
LABEL org.opencontainers.image.description="Devrunner — sandboxed development runtime"
LABEL org.opencontainers.image.vendor="Niuu Labs"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.source="https://github.com/niuulabs/volundr"

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies: Node.js, PostgreSQL server, shells, inotify-tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    lsb-release \
    inotify-tools \
    git \
    tmux \
    vim \
    zsh \
    fish \
    zsh-autosuggestions \
    zsh-syntax-highlighting \
    && rm -rf /var/lib/apt/lists/*

# Install oh-my-zsh system-wide (users get a copy via init-home.sh)
# Pinned to a specific commit for reproducibility
RUN git clone https://github.com/ohmyzsh/ohmyzsh.git /usr/share/oh-my-zsh \
    && cd /usr/share/oh-my-zsh && git checkout 8df5c1b18b1393dc5046c729094f897bd3636a9b \
    && rm -rf .git

# Install Node.js 24 LTS (pinned keyring + apt source instead of curl|bash)
RUN curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
    -o /usr/share/keyrings/nodesource.asc \
    && echo "deb [signed-by=/usr/share/keyrings/nodesource.asc] https://deb.nodesource.com/node_24.x nodistro main" \
    > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install uv from a pinned release archive.
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
    install -m 0755 "/tmp/${UV_ARCHIVE%.tar.gz}/uvx" /usr/local/bin/uvx; \
    rm -rf "/tmp/${UV_ARCHIVE}" "/tmp/${UV_ARCHIVE%.tar.gz}"

# Upgrade npm and install the CLI tools from checked-in lockfiles.
COPY containers/node/npm-bootstrap/package.json containers/node/npm-bootstrap/package-lock.json /opt/npm-bootstrap/
COPY containers/devrunner/npm-tools/package.json containers/devrunner/npm-tools/package-lock.json /opt/devrunner-tools/
RUN cd /opt/npm-bootstrap \
    && npm ci --omit=dev \
    && rm -rf /usr/lib/node_modules/npm \
    && ln -s /opt/npm-bootstrap/node_modules/npm /usr/lib/node_modules/npm \
    && ln -sf /usr/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && cd /opt/devrunner-tools \
    && npm ci --omit=dev \
    && ln -sf /opt/devrunner-tools/node_modules/.bin/claude /usr/local/bin/claude \
    && ln -sf /opt/devrunner-tools/node_modules/.bin/codex /usr/local/bin/codex \
    && ln -sf /opt/devrunner-tools/node_modules/.bin/opencode /usr/local/bin/opencode

# Install PostgreSQL 16
RUN echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
    > /etc/apt/sources.list.d/pgdg.list \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor \
    -o /etc/apt/trusted.gpg.d/postgresql.gpg \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16 \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip to fix GHSA-4xh5-x5gv-qwph / GHSA-6vgw-5pg2-w6jp
RUN uv pip install --system --break-system-packages --upgrade pip==26.1.1

# Common Python packages for web dev
RUN uv pip install --system --break-system-packages \
    uvicorn==0.41.0 \
    fastapi==0.136.3 \
    starlette==1.1.0 \
    flask==3.1.3 \
    gunicorn==25.1.0 \
    psycopg2-binary==2.9.11 \
    pyyaml==6.0.3 \
    aiohttp==3.13.3

# Install Homebrew dependencies (so users can brew install without root)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    procps \
    file \
    && rm -rf /var/lib/apt/lists/*

# Create devrunner user (UID 1000 to match skuld)
RUN useradd -m -s /bin/bash -u 1000 devrunner

# Allow devrunner user to run PostgreSQL
RUN mkdir -p /run/postgresql && chown devrunner:devrunner /run/postgresql

# Copy default dotfiles into /etc/skel for first-launch seeding
COPY containers/devrunner/configs/.bashrc /etc/skel/.bashrc
COPY containers/devrunner/configs/.zshrc /etc/skel/.zshrc
COPY containers/devrunner/configs/.config/fish/config.fish /etc/skel/.config/fish/config.fish
COPY containers/devrunner/configs/.config/fish/functions/fish_prompt.fish /etc/skel/.config/fish/functions/fish_prompt.fish

# Copy process manager, terminal server, and init script
COPY containers/devrunner/devrunner.py /usr/local/bin/devrunner
COPY containers/devrunner/terminal.py /usr/local/bin/terminal.py
COPY containers/devrunner/restricted-shell /usr/local/bin/restricted-shell
COPY containers/devrunner/configs/init-home.sh /usr/local/bin/init-home.sh
RUN chmod +x /usr/local/bin/devrunner /usr/local/bin/restricted-shell /usr/local/bin/init-home.sh

# Switch to non-root user
USER devrunner

ENTRYPOINT ["python", "/usr/local/bin/devrunner"]
