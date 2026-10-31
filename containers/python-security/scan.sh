#!/usr/bin/env bash
# Preserve raw findings; apply digest-scoped VEX only after in-image verification.
# Syft catalogs the image once; every Grype pass matches that SBOM instead of
# re-reading the image. Pinned tools: install-scanners.sh.
set -euo pipefail
image_reference=$1
container_name=$2
security_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
sbom="sbom-$container_name.syft.json"

# Catalog the way Grype does when it reads an image itself: capture Go symbols
# (its gosymbols qualifier drops stdlib false positives with them) and drop
# packages without a version (cmd/grype/cli/commands/root.go, getProviderConfig).
catalog() {
  SYFT_GOLANG_CAPTURE_SYMBOLS=all SYFT_COMPLIANCE_MISSING_VERSION=drop \
    syft scan "$1" -o "syft-json=$sbom"
}

case "$container_name" in
  niuu|agent|devrunner|openshell)
    docker pull "$image_reference"
    image_digest=$(docker image inspect --format '{{index .RepoDigests 0}}' "$image_reference")
    docker run --rm --network none --read-only --entrypoint python \
      -v "$security_dir:/security:ro" "$image_digest" \
      -B /security/verify.py "$image_digest" > "python-security-$container_name.json"
    catalog "$image_digest"
    grype "sbom:$sbom" -o "json=grype-$container_name-raw.json"
    python3 "$security_dir/vex.py" "$image_digest" \
      "python-security-$container_name.json" "grype-$container_name-raw.json" \
      > "python-security-$container_name.vex.json"
    grype "sbom:$sbom" --only-fixed --fail-on critical \
      --vex "python-security-$container_name.vex.json" \
      -o "json=grype-$container_name-filtered.json" -o "sarif=grype-$container_name.sarif"
    ;;
  *)
    catalog "$image_reference"
    grype "sbom:$sbom" -o "json=grype-$container_name-raw.json"
    grype "sbom:$sbom" --only-fixed --fail-on critical \
      -o "json=grype-$container_name-filtered.json" -o "sarif=grype-$container_name.sarif"
    ;;
esac
