FROM node:22-slim@sha256:80fdb3f57c815e1b638d221f30a826823467c4a56c8f6a8d7aa091cd9b1675ea AS fetch

LABEL org.opencontainers.image.title="vscode-reh"
LABEL org.opencontainers.image.description="VS Code Remote Extension Host server for Skuld session pods"
LABEL org.opencontainers.image.vendor="Niuu Labs"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.source="https://github.com/niuulabs/volundr"

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Detect architecture: map Docker TARGETARCH to VS Code REH arch names
ARG TARGETARCH
RUN echo "Building for TARGETARCH=${TARGETARCH}" && \
    case "${TARGETARCH}" in \
      amd64) echo "linux-x64" > /tmp/reh-arch ;; \
      arm64) echo "linux-arm64" > /tmp/reh-arch ;; \
      *)     echo "Unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac

WORKDIR /build

# Install only the @codingame/monaco-vscode-api package to read the VS Code
# commit hash — no frontend source tree is required for this image build.
COPY containers/vscode-reh/package.json containers/vscode-reh/package-lock.json ./
RUN npm ci --ignore-scripts --no-fund --no-audit

# Download the REH binary matching the npm package commit
COPY containers/skuld/download-reh.sh ./download-reh.sh
RUN chmod +x ./download-reh.sh && \
    ARCH=$(cat /tmp/reh-arch) ./download-reh.sh /opt/vscode-reh

# ---------------------------------------------------------------------------
# Runtime — minimal image with just the REH binary
# ---------------------------------------------------------------------------
FROM ubuntu:24.04@sha256:186072bba1b2f436cbb91ef2567abca677337cfc786c86e107d25b7072feef0c


RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    libatomic1 \
    && rm -rf /var/lib/apt/lists/*

# Reuse the existing ubuntu user (UID 1000) that ships with ubuntu:24.04,
# renaming it to 'coder' for clarity in ps/top output.
RUN usermod -l coder -d /home/coder -m ubuntu && groupmod -n coder ubuntu

COPY --from=fetch /opt/vscode-reh /opt/vscode-reh

# Ensure all binaries are executable and create a stable entrypoint
# symlink. The binary name varies across VS Code versions
# (code-server-oss, code-server, server.sh).
RUN chmod -R +x /opt/vscode-reh/bin/ && \
    if [ -f /opt/vscode-reh/bin/code-server-oss ]; then \
      ln -sf /opt/vscode-reh/bin/code-server-oss /opt/vscode-reh/bin/reh-server; \
    elif [ -f /opt/vscode-reh/bin/code-server ]; then \
      ln -sf /opt/vscode-reh/bin/code-server /opt/vscode-reh/bin/reh-server; \
    elif [ -f /opt/vscode-reh/server.sh ]; then \
      ln -sf /opt/vscode-reh/server.sh /opt/vscode-reh/bin/reh-server; \
    else \
      echo "ERROR: No known REH server binary found in /opt/vscode-reh/bin/" >&2; \
      ls -la /opt/vscode-reh/bin/ >&2; \
      exit 1; \
    fi

COPY containers/vscode-reh/entrypoint.sh /opt/vscode-reh/entrypoint.sh
RUN chmod +x /opt/vscode-reh/entrypoint.sh

USER coder
WORKDIR /home/coder

EXPOSE 8445

ENTRYPOINT ["/opt/vscode-reh/entrypoint.sh"]
CMD ["--host=0.0.0.0", "--port=8445", "--without-connection-token", "--accept-server-license-terms"]
