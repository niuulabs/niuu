#!/bin/sh
# Pinned artifacts from Meta's release manifest; never run the auto-updating launcher.
set -eu
case "${1:?target architecture required}" in
  amd64) artifact=muse-x86-linux; checksum=0809aac7cb58d18f5022b6d05e02aa1974683c0ec430e5f04d25b60ed17de2da ;;
  arm64) artifact=muse-aarch64-linux; checksum=3dc39b8fa26f9963cc2038f2966c0932ed5a39f0f9efb39cc0266a261cbbcf76 ;;
  *) echo "Unsupported Muse architecture: $1" >&2; exit 1 ;;
esac
curl --fail --show-error --location --retry 3 \
  "https://lookaside.facebook.com/lookaside/muse/download/?channel=muse&version=1.4.14-R7309.1&file=$artifact" \
  --output /usr/local/bin/muse
printf '%s  %s\n' "$checksum" /usr/local/bin/muse | sha256sum --check -
chmod 0755 /usr/local/bin/muse
muse --version
