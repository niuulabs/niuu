FROM python:3.14-slim@sha256:bc389f7dfcb21413e72a28f491985326994795e34d2b86c8ae2f417b4e7818aa


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

# Upgrade bundled npm to fix GHSA-qffp-2rhf-9h96 (tar) and GHSA-*-minimatch CVEs
RUN npm install -g npm@11.3.0

# Install Claude Code CLI globally via npm
RUN npm install -g @anthropic-ai/claude-code@2.1.121

# Install OpenAI Codex CLI globally via npm
RUN npm install -g @openai/codex@0.125.0

# Install OpenCode (opencode.ai) globally via npm
RUN npm install -g opencode-ai@1.14.29

# Install PostgreSQL 16
RUN echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
    > /etc/apt/sources.list.d/pgdg.list \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor \
    -o /etc/apt/trusted.gpg.d/postgresql.gpg \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16 \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip to fix GHSA-4xh5-x5gv-qwph / GHSA-6vgw-5pg2-w6jp
RUN pip install --no-cache-dir --upgrade pip==25.0.1

# Common Python packages for web dev
RUN pip install --no-cache-dir \
    uvicorn==0.41.0 \
    fastapi==0.135.1 \
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
