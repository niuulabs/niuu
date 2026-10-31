FROM oven/bun:1.4.4@sha256:1d22548f177f4ff38e0c15baa3c2c46c6c2060ba2d1bc573bb4720b407548f1d
ARG GBRAIN_REF=f8d1e3936d908935ce96e18c14249aecd85fb5b2
USER root
# CI passes the UTC date, so the registry layer cache cannot keep this upgrade
# (and the security fixes it pulls) for more than a day.
ARG OS_PACKAGES_REFRESH=
# The pinned base carries fixed-upstream CVEs in glibc, pcre2, sqlite and gzip
# (the scan gate fails on the glibc one); take Debian's security updates.
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/gbrain
# Security dependency fixes on top of the pinned ref, until GBRAIN_REF moves past
# them: proxy-addr 2.0.8 (GHSA-jqcg-44mw-7w3h, upstream bafdd269),
# @modelcontextprotocol/sdk 1.32.0 (GHSA-6qxp-vccf-f47h, GHSA-22jm-h49p-29qw,
# GHSA-46jr-4wx8-v453, GHSA-6prh-2h8m-c8cw, GHSA-rvq5-wwqv-78pq), and overrides for
# body-parser 2.4.0, zod 4.6.7, fast-uri 3.1.9, hono 4.13.13 and ip-address 10.7.4.
# The frozen install below rejects the result if the patched lockfile is inconsistent.
COPY containers/gbrain/security-deps.patch /tmp/security-deps.patch
RUN git init && git remote add origin https://github.com/garrytan/gbrain.git \
    && git fetch --depth 1 origin "$GBRAIN_REF" && git checkout FETCH_HEAD \
    && git apply /tmp/security-deps.patch && rm /tmp/security-deps.patch \
    && bun install --frozen-lockfile \
    && bun pm cache rm \
    && printf '#!/bin/sh\nexec bun /opt/gbrain/src/cli.ts "$@"\n' > /usr/local/bin/gbrain \
    && chmod +x /usr/local/bin/gbrain
RUN mkdir -p /data && chown 1000:1000 /data
ENV GBRAIN_HOME=/data
USER 1000:1000
ENTRYPOINT ["gbrain"]
CMD ["serve", "--http", "--bind", "0.0.0.0", "--port", "3131"]
