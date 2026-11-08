"""Create digest-scoped statements only from verified image backports and mitigations."""

import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote


def document(
    image: str, evidence: dict, scan: dict, manifest: dict, bundle_dir: str | None = None
) -> dict:
    repository, separator, digest = image.partition("@")
    if not separator or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("Backport VEX requires an immutable image digest")
    expected = {name: hashes["patched"] for name, hashes in manifest["files"].items()}
    if (
        evidence["files"] != expected
        or evidence["python_version"] != manifest["python_version"]
        or evidence["cves"] != manifest["cves"]
        or evidence["mitigations"] != [item["cve"] for item in manifest["mitigations"]]
        or evidence["image"] != image
        or evidence["patches"] != [item["commit"] for item in manifest["patches"]]
        or evidence["upstream_fixes"]
        != {item["cve"]: item["sha256"] for item in manifest["upstream_fixes"]}
    ):
        raise ValueError("Runtime backport evidence does not match the reviewed manifest")
    for item in manifest["mitigations"]:
        if item["file"] not in manifest["files"]:
            raise ValueError(f"Mitigation names an unverified module: {item['file']}")
    executable, library = evidence["executable"], evidence["library"]
    if bundle_dir is not None:
        root = Path(bundle_dir).resolve(strict=True)
        if scan["source"]["type"] != "directory" or (
            Path(scan["source"]["target"]).resolve(strict=True) != root
        ):
            raise ValueError("Grype scanned a different directory from the verified bundle")
        # Syft directory locations are rooted at '/', while the verifier sees host paths.
        executable, library = (
            "/" + Path(path).resolve(strict=True).relative_to(root).as_posix()
            for path in (executable, library)
        )
    elif image not in scan["source"]["target"]["repoDigests"]:
        raise ValueError("Grype scanned a different image from the verified runtime")
    product = (
        f"pkg:oci/{repository.rsplit('/', 1)[-1]}@{digest}"
        f"?repository_url={quote(repository, safe='')}"
    )
    statements = []
    timestamp = datetime.now(UTC).isoformat()
    backport_notes = (
        "Upstream CPython security backports installed; every backported module's "
        "SHA-256 hash and exploit regressions verified inside this exact artifact. "
        "See the retained python-security evidence and raw Grype report."
    )
    upstream_notes = (
        f"Fixed upstream in CPython {manifest['python_version']}, which the scanner's "
        "version ranges do not credit yet; the fixed module's SHA-256 hash and regression "
        "verified inside this exact artifact. See the retained python-security evidence."
    )
    mitigation_notes = (
        "Mitigated, not fixed: the vulnerable compiled code is still present. The module "
        "carrying the guard has its SHA-256 hash and regressions verified inside this exact "
        "image. See the retained python-security evidence and raw Grype report."
    )
    claims = [
        (cve, {"status": "fixed", "status_notes": backport_notes}) for cve in manifest["cves"]
    ]
    claims += [
        (item["cve"], {"status": "fixed", "status_notes": upstream_notes})
        for item in manifest["upstream_fixes"]
    ]
    claims += [
        (
            item["cve"],
            {
                "status": "not_affected",
                "justification": item["justification"],
                "impact_statement": item["impact_statement"],
                "status_notes": mitigation_notes,
            },
        )
        for item in manifest["mitigations"]
    ]
    for cve, assertion in claims:
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
            if executable not in locations or not all_locations <= {executable, library}:
                raise ValueError("Grype found a Python runtime that was not verified")
            if not artifact["purl"]:
                raise ValueError(
                    "Python artifact has no package identity for a scoped VEX statement"
                )
            purls.add(artifact["purl"])
        if purls:
            # Directory scans have no OCI parent for Grype to match. Generation above
            # binds these package claims to the verified bundle and its digest.
            products = (
                [{"@id": purl} for purl in sorted(purls)]
                if bundle_dir
                else [{"@id": product, "subcomponents": [{"@id": purl} for purl in sorted(purls)]}]
            )
            statements.append(
                {
                    "vulnerability": {"name": cve},
                    "products": products,
                    **assertion,
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
    image, evidence_path, scan_path = sys.argv[1:4]
    bundle_dir = sys.argv[4] if len(sys.argv) == 5 else None
    manifest = json.loads(Path(__file__).with_name("manifest.json").read_text())
    print(
        json.dumps(
            document(
                image,
                json.loads(Path(evidence_path).read_text()),
                json.loads(Path(scan_path).read_text()),
                manifest,
                bundle_dir,
            ),
            indent=2,
        )
    )
