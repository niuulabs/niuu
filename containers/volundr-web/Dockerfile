# Stage 1: Build the Volundr web UI
FROM node:24-alpine@sha256:7fddd9ddeae8196abf4a3ef2de34e11f7b1a722119f91f28ddf1e99dcafdf114 AS build

WORKDIR /app
RUN corepack enable

# Copy workspace manifests for dependency caching
COPY web-next/package.json ./
COPY web-next/pnpm-lock.yaml ./
COPY web-next/pnpm-workspace.yaml ./
COPY web-next/tsconfig.base.json ./
COPY web-next/tsconfig.json ./
COPY web-next/build ./build

# Copy the app and workspace packages
COPY web-next/apps ./apps
COPY web-next/packages ./packages
ARG APP_VERSION=dev
ENV NODE_OPTIONS="--max-old-space-size=4096"
ENV VITE_APP_VERSION=${APP_VERSION}
RUN pnpm install --frozen-lockfile
RUN pnpm build

# Stage 2: Serve with nginx
FROM nginx:1.31.0-alpine@sha256:2f07d83bf561b506400dc183b1b2003803e39efbd22451f848adaba14d28c7c7

# Patch base image vulnerabilities (CVE-2026-22184: zlib).
# Fix CVE-2026-22184: zlib critical vulnerability.
RUN apk upgrade --no-cache zlib

LABEL org.opencontainers.image.title="volundr-web"
LABEL org.opencontainers.image.description="Volundr Web — AI-native development platform UI"
LABEL org.opencontainers.image.vendor="Niuu Labs"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.source="https://github.com/niuulabs/volundr"

# Create non-root user
RUN addgroup -g 1001 -S volundr && \
    adduser -u 1001 -S volundr -G volundr && \
    chown -R volundr:volundr /var/cache/nginx /var/log/nginx /etc/nginx/conf.d && \
    touch /var/run/nginx.pid && \
    chown volundr:volundr /var/run/nginx.pid

# Copy nginx config
COPY containers/volundr-web/nginx.conf /etc/nginx/conf.d/default.conf

# Copy built app
COPY --from=build /app/apps/niuu/dist /usr/share/nginx/html

# Health check endpoint
RUN echo "OK" > /usr/share/nginx/html/health

USER 1001

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
