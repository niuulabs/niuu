#!/usr/bin/env bash
# Preserve raw findings; apply digest-scoped VEX only after in-image verification.
set -euo pipefail
image_reference=$1
container_name=$2
security_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
case "$container_name" in
  niuu|agent|devrunner|openshell)
    docker pull "$image_reference"
    image_digest=$(docker image inspect --format '{{index .RepoDigests 0}}' "$image_reference")
    docker run --rm --network none --read-only --entrypoint python \
      -v "$security_dir:/security:ro" "$image_digest" \
      -B /security/verify.py "$image_digest" > "python-security-$container_name.json"
    grype "$image_digest" -o "json=grype-$container_name-raw.json"
    python3 "$security_dir/vex.py" "$image_digest" \
      "python-security-$container_name.json" "grype-$container_name-raw.json" \
      > "python-security-$container_name.vex.json"
    grype "$image_digest" --only-fixed --fail-on critical \
      --vex "python-security-$container_name.vex.json" \
      -o "json=grype-$container_name-filtered.json" -o "sarif=grype-$container_name.sarif"
    ;;
  *)
    grype "$image_reference" -o "json=grype-$container_name-raw.json"
    grype "$image_reference" --only-fixed --fail-on critical \
      -o "json=grype-$container_name-filtered.json" -o "sarif=grype-$container_name.sarif"
    ;;
esac
