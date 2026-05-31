# Stage 1: Build the next-generation Niuu web UI
FROM node:24-alpine@sha256:2bdb65ed1dab192432bc31c95f94155ca5ad7fc1392fb7eb7526ab682fa5bf14 AS build

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
FROM nginx:1.31.1-alpine@sha256:8b1e78743a03dbb2c95171cc58639fef29abc8816598e27fb910ed2e621e589a

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
