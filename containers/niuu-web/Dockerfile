# Stage 1: Build the next-generation Niuu web UI
FROM node:24-alpine@sha256:06d7adf3e0ef61a13f1d28682726366f97a15aa1888c934d3ecec86c33918b53 AS build

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
FROM nginx:1.31.8-alpine@sha256:1ef7ca94d7879df82421428ce13bd1eaa412404953859cf7a5b6512c25e67a60

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
