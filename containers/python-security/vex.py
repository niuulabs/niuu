"""Create digest-scoped fixed statements only from verified image backports."""

import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote


def document(image: str, evidence: dict, scan: dict, manifest: dict) -> dict:
    repository, separator, digest = image.partition("@")
    if not separator or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("Backport VEX requires an immutable image digest")
    expected = {name: hashes["patched"] for name, hashes in manifest["files"].items()}
    if (
        evidence["files"] != expected
        or evidence["python_version"] != manifest["python_version"]
        or evidence["cves"] != manifest["cves"]
        or evidence["image"] != image
        or evidence["patches"] != [item["commit"] for item in manifest["patches"]]
    ):
        raise ValueError("Runtime backport evidence does not match the reviewed manifest")
    if image not in scan["source"]["target"]["repoDigests"]:
        raise ValueError("Grype scanned a different image from the verified runtime")
    product = (
        f"pkg:oci/{repository.rsplit('/', 1)[-1]}@{digest}"
        f"?repository_url={quote(repository, safe='')}"
    )
    statements = []
    timestamp = datetime.now(UTC).isoformat()
    for cve in manifest["cves"]:
        purls = set()
        for match in scan["matches"]:
            artifact = match["artifact"]
            if (
                match["vulnerability"]["id"] != cve
                or artifact["name"] != "python"
                or artifact["type"] != "binary"
                or artifact["version"] != manifest["python_version"]
            ):
                continue
            # Syft records the interpreter as primary and libpython as supporting.
            if any(
                location.get("annotations", {}).get("evidence") not in {"primary", "supporting"}
                for location in artifact["locations"]
            ):
                raise ValueError("Grype reported unclassified Python runtime evidence")
            locations = {
                location["path"]
                for location in artifact["locations"]
                if location["annotations"]["evidence"] == "primary"
            }
            all_locations = {location["path"] for location in artifact["locations"]}
            if (
                evidence["executable"] not in locations
                or not all_locations <= {evidence["executable"], evidence["library"]}
            ):
                raise ValueError("Grype found a Python runtime that was not verified")
            if not artifact["purl"]:
                raise ValueError(
                    "Python artifact has no package identity for a scoped VEX statement"
                )
            purls.add(artifact["purl"])
        if purls:
            statements.append(
                {
                    "vulnerability": {"name": cve},
                    "products": [
                        {
                            "@id": product,
                            "subcomponents": [{"@id": purl} for purl in sorted(purls)],
                        }
                    ],
                    "status": "fixed",
                    "status_notes": (
                        "Upstream CPython security backports installed; all five module SHA-256 "
                        "hashes and exploit regressions verified inside this exact image. "
                        "See the retained python-security evidence and raw Grype report."
                    ),
                    "timestamp": timestamp,
                }
            )
    return {
        "@context": "https://openvex.dev/ns/v0.2.0",
        "@id": f"https://github.com/niuulabs/niuu/security/python-backports/{digest}",
        "author": "Niuu Labs",
        "timestamp": timestamp,
        "version": 1,
        "statements": statements,
    }


if __name__ == "__main__":
    image, evidence_path, scan_path = sys.argv[1:]
    manifest = json.loads(Path(__file__).with_name("manifest.json").read_text())
    print(
        json.dumps(
            document(
                image,
                json.loads(Path(evidence_path).read_text()),
                json.loads(Path(scan_path).read_text()),
                manifest,
            ),
            indent=2,
        )
    )
