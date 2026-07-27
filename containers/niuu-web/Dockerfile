# Stage 1: Build the next-generation Niuu web UI
FROM node:24-alpine@sha256:a0b9bf06e4e6193cf7a0f58816cc935ff8c2a908f81e6f1a95432d679c54fbfd AS build

WORKDIR /app

RUN corepack enable

# Copy workspace manifests for dependency resolution
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
FROM nginx:1.31.3-alpine@sha256:4a73073bd557c65b759505da037898b61f1be6cbcc3c2c3aeac22d2a470c1752

# Patch base image vulnerabilities (CVE-2026-22184: zlib).
RUN apk upgrade --no-cache zlib

LABEL org.opencontainers.image.title="niuu-web"
LABEL org.opencontainers.image.description="Niuu Web — composable plugin-based platform UI"
LABEL org.opencontainers.image.vendor="Niuu Labs"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.source="https://github.com/niuulabs/volundr"

RUN addgroup -g 1001 -S volundr && \
    adduser -u 1001 -S volundr -G volundr && \
    chown -R volundr:volundr /var/cache/nginx /var/log/nginx /etc/nginx/conf.d && \
    touch /var/run/nginx.pid && \
    chown volundr:volundr /var/run/nginx.pid

COPY containers/volundr-web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/apps/niuu/dist /usr/share/nginx/html

RUN echo "OK" > /usr/share/nginx/html/health

USER 1001

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
