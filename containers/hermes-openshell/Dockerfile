FROM nousresearch/hermes-agent@sha256:0ddf22d30943d39cbab35525f0a4423f9a3c936ad4e00acda331de7022e9a8e8

LABEL org.opencontainers.image.description="Hermes Agent runtime adapted for OpenShell sandboxes"

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system sandbox \
    && useradd --system --create-home --gid sandbox --shell /bin/bash sandbox \
    && mkdir -p /sandbox/workspace \
    && chown -R sandbox:sandbox /sandbox

ENV HERMES_HOME=/sandbox/workspace/.hermes
ENV HERMES_WRITE_SAFE_ROOT=/sandbox/workspace

USER sandbox
WORKDIR /sandbox/workspace
