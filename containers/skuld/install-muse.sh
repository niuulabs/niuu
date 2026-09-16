#!/bin/sh
# Pinned artifacts from Meta's release manifest; never run the auto-updating launcher.
set -eu
case "${1:?target architecture required}" in
  amd64) artifact=muse-x86-linux; checksum=dfc52dc7d37e23d7618ed37a7ec3dd8d0f4c83c647b0f1cc2c2791274acabe78 ;;
  arm64) artifact=muse-aarch64-linux; checksum=4637d398809159af148bc0ad4cb510e85d10a66fc7aeb47559be0db0e424bacd ;;
  *) echo "Unsupported Muse architecture: $1" >&2; exit 1 ;;
esac
curl --fail --show-error --location --retry 3 \
  "https://lookaside.facebook.com/lookaside/muse/download/?channel=muse&version=1.3.0-R3233.1&file=$artifact" \
  --output /usr/local/bin/muse
printf '%s  %s\n' "$checksum" /usr/local/bin/muse | sha256sum --check -
chmod 0755 /usr/local/bin/muse
muse --version
