# Stage 1: Build the next-generation Niuu web UI
FROM node:24-alpine@sha256:a34ab6d7f6f4813f4d0c5469d74685604b962fbdbf5793a7bc7b4998ecb867c8 AS build

WORKDIR /app

RUN corepack enable

ENV NODE_OPTIONS="--max-old-space-size=4096"

# Fetch needs only the lockfile, so the package store survives source edits.
COPY web-next/package.json web-next/pnpm-lock.yaml web-next/pnpm-workspace.yaml ./
RUN pnpm fetch

COPY web-next/tsconfig.base.json web-next/tsconfig.json ./
COPY web-next/build ./build
COPY web-next/apps ./apps
COPY web-next/packages ./packages
RUN pnpm install --frozen-lockfile --offline

# Declared last: callers pass the commit sha, which must not invalidate the install.
ARG APP_VERSION=dev
ENV VITE_APP_VERSION=${APP_VERSION}
RUN pnpm build

# Stage 2: Serve with nginx
FROM nginx:1.31.8-alpine@sha256:713fc2ebfd92e8217db451477182ee91944b123d2b1ee9fd5a8c6e91ca6c3a11

# CI passes the UTC date, so the registry layer cache cannot keep this upgrade
# (and the security fixes it pulls) for more than a day.
ARG OS_PACKAGES_REFRESH=
# Apply security updates to all packages inherited from the pinned base.
RUN apk upgrade --no-cache

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

COPY containers/niuu-web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/apps/niuu/dist /usr/share/nginx/html

RUN echo "OK" > /usr/share/nginx/html/health

USER 1001

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
