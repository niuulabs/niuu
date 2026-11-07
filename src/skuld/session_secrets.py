"""Turn a session's delivered secrets into its environment and files, before Skuld runs.

Forge's secret delivery renders a session's secrets into one directory (``/run/secrets``
in a container or a Linux slot, the slot's own directory on a Mac): ``user/`` holds one
JSON file per credential, and ``manifest.json`` says which environment variables and
which files the session gets from them. This is the one program every session runtime
runs at boot (the Skuld image's entrypoint, Linux host mode and macOS host mode):

    exports=$(python -m skuld.session_secrets) && eval "$exports"

It prints ``export NAME=<quoted value>`` lines for the environment and links each
declared file at its target. Environment it reads:

- ``NIUU_SECRETS_DIR``: the rendered directory (default ``/run/secrets``).
- ``NIUU_SESSION_PLACES`` (JSON object, optional): where container paths are on a
  machine without mount namespaces (a Mac): ``{"/home/skuld": "<slot home>"}``. A file
  target outside every place there is refused, since nothing would see it.
- ``NIUU_SESSION_LINKS`` (JSON list, optional): ``{"target", "source"}`` links the
  session expects where a container would have a mount (a Mac again).

Anything it cannot do raises and stops the boot: a session that started without its
credentials would look healthy and fail on first use.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from collections.abc import Mapping

DEFAULT_SECRETS_DIR = "/run/secrets"


class SessionSecretsError(RuntimeError):
    """The delivered secrets cannot become the session's environment and files."""


def _place(target: str, places: Mapping[str, str] | None) -> str:
    if places is None:
        return target
    for container, here in sorted(places.items(), key=lambda item: -len(item[0])):
        if target == container or target.startswith(container + "/"):
            return here + target[len(container) :]
    raise SessionSecretsError(
        f"niuu: secret file {target} lies outside the session home and workspace, where "
        "this session has no mounts"
    )


def _link(target: str, source: str) -> None:
    if os.path.islink(target) and os.readlink(target) == source:
        return
    if os.path.lexists(target):
        raise SessionSecretsError(f"niuu: {target} is in the way of the session's {source}")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    os.symlink(source, target)


def exports(environ: Mapping[str, str]) -> list[str]:
    """The ``export`` lines for the session's environment; links its files on the way."""
    secrets = environ.get("NIUU_SECRETS_DIR") or DEFAULT_SECRETS_DIR
    places_text = environ.get("NIUU_SESSION_PLACES")
    places = json.loads(places_text) if places_text else None
    lines: list[str] = []
    manifest_path = os.path.join(secrets, "manifest.json")
    user = os.path.join(secrets, "user")
    if os.path.isfile(manifest_path) and os.path.isdir(user):
        with open(manifest_path) as stream:
            manifest = json.load(stream)
        for name, spec in manifest.get("env", {}).items():
            if not name.isidentifier():
                raise SessionSecretsError(f"niuu: {name!r} is not an environment variable name")
            path = os.path.join(user, spec["file"])
            if not os.path.exists(path):
                continue
            with open(path) as stream:
                value = json.load(stream).get(spec.get("key", ""), "")
            lines.append(f"export {name}={shlex.quote(str(value))}")
        for target, spec in manifest.get("files", {}).items():
            source = os.path.join(user, spec["file"])
            if os.path.exists(source):
                _link(_place(target, places), source)
    for item in json.loads(environ.get("NIUU_SESSION_LINKS") or "[]"):
        _link(item["target"], item["source"])
    return lines


def main() -> int:
    try:
        lines = exports(os.environ)
    except (OSError, ValueError, KeyError, SessionSecretsError) as exc:
        print(f"niuu: the session's secrets could not be delivered: {exc}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
