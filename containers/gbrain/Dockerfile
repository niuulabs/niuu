FROM oven/bun:1.4.2@sha256:9114c058aeae42162ee16dd5084b95fe9473970bb6bcb5b232ab1630f0546895
ARG GBRAIN_REF=43597b19e50a3abf56409337f248f7966860293c
USER root
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/gbrain
RUN git init && git remote add origin https://github.com/garrytan/gbrain.git \
    && git fetch --depth 1 origin "$GBRAIN_REF" && git checkout FETCH_HEAD \
    && bun install --frozen-lockfile \
    && printf '#!/bin/sh\nexec bun /opt/gbrain/src/cli.ts "$@"\n' > /usr/local/bin/gbrain \
    && chmod +x /usr/local/bin/gbrain
RUN mkdir -p /data && chown 1000:1000 /data
ENV GBRAIN_HOME=/data
USER 1000:1000
ENTRYPOINT ["gbrain"]
CMD ["serve", "--http", "--bind", "0.0.0.0", "--port", "3131"]
