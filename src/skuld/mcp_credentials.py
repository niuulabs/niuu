"""Noninteractive runtime header helper; reads an access token projected by the injector."""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path


def read_headers(path: str, header: str, prefix: str, oauth: bool = False) -> dict[str, str]:
    token = Path(path).read_text().strip()
    if oauth:
        document = json.loads(token)
        token = document["access_token"]
        if document.get("expires_at"):
            expiry = datetime.fromisoformat(document["expires_at"])
            if expiry.tzinfo is None or expiry <= datetime.now(UTC):
                raise ValueError("MCP credential expired; reconnect the integration")
    if not isinstance(token, str):
        raise ValueError("MCP credential is malformed")
    if not token or any(char in token + header + prefix for char in "\r\n"):
        raise ValueError("MCP credential is missing or malformed")
    return {header: prefix + token}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    parser.add_argument("--header", default="Authorization")
    parser.add_argument("--prefix", default="Bearer ")
    parser.add_argument("--oauth", action="store_true")
    args = parser.parse_args()
    try:
        headers = read_headers(args.file, args.header, args.prefix, args.oauth)
    except (OSError, ValueError, KeyError, TypeError):
        print(
            "MCP credential unavailable; check injection or reconnect the integration",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    print(json.dumps(headers))


if __name__ == "__main__":
    main()
