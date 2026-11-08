"""The Linux host runtime's guest programs, run for real where the machine allows.

``IMAGE`` runs against a fake registry (a ``curl`` on PATH serving files from a temporary
directory), so the fetch, the digest checks and the layer unpack are the shipped code's.
``PODS_START`` and ``PODS_STOP`` run with recording ``systemctl``, ``systemd-run`` and
``ip``. ``CONTAINER`` needs root and Linux namespaces, so its system calls are recorded
instead of made; the real thing is proven on a host.
"""

from __future__ import annotations

import ast
import fcntl
import gzip
import hashlib
import io
import json
import os
import platform
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

from volundr.adapters.outbound import linux_guest_programs as programs
from volundr.adapters.outbound import ssh_host_vm_runtime

ARCH = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}[
    platform.machine()
]
OTHER_ARCH = "arm64" if ARCH == "amd64" else "amd64"
GZIP = "application/vnd.oci.image.layer.v1.tar+gzip"
MANIFEST = "application/vnd.oci.image.manifest.v1+json"
INDEX = "application/vnd.oci.image.index.v1+json"
DOCKER_MANIFEST = "application/vnd.docker.distribution.manifest.v2+json"
DOCKER_INDEX = "application/vnd.docker.distribution.manifest.list.v2+json"
ENV = [
    "PATH=/opt/niuu/bin:/usr/local/bin:/usr/bin:/bin",
    "VIRTUAL_ENV=/opt/niuu",
    "UV_PYTHON_INSTALL_DIR=/sandbox/.uv/python",
]
SESSION = {
    "agents": ["claude", "codex", "grok"],
    "tools": ["node", "gh"],
    "view": "/opt/niuu-skuld",
}


def test_every_guest_program_parses():
    for name in ("IMAGE", "PRUNE", "CONTAINER", "PODS_START", "PODS_STOP"):
        ast.parse(getattr(programs, name))


def test_the_runtime_and_the_programs_agree_on_the_image_cache():
    assert (
        ssh_host_vm_runtime.HOST_IMAGE_CACHE == programs.IMAGE_CACHE == "/var/lib/niuu/host-images"
    )


# -- the fake registry -------------------------------------------------------------------------

# A ``curl`` that answers from routes.json (URL to file, status and headers) and logs what was
# asked, with the arguments the program passes: --output, --dump-header, -H, --write-out.
CURL = r"""
import json, pathlib, sys, os, time
args = sys.argv[1:]
registry = pathlib.Path(os.environ['FAKE_REGISTRY'])
def option(name):
    return args[args.index(name) + 1]
output, headers_file, url = option('--output'), option('--dump-header'), args[-1]
sent = [args[i + 1] for i, a in enumerate(args) if a == '-H']
bearer = ''.join(h.split('Bearer ', 1)[1] for h in sent if h.startswith('Authorization: Bearer '))
accept = ''.join(h.split(': ', 1)[1] for h in sent if h.startswith('Accept: '))
with open(str(registry / 'requests.jsonl'), 'a') as log:
    log.write(json.dumps({'url': url, 'bearer': bearer, 'accept': accept}) + '\n')
routes = json.loads((registry / 'routes.json').read_text())
route = routes.get(url, {'status': 404})
status, header_lines, body = route.get('status', 200), [], b''
if route.get('token') is not None and bearer != route['token']:
    status, header_lines = 401, route['challenge']
elif 'file' in route:
    body = pathlib.Path(route['file']).read_bytes()
if route.get('wait'):
    # Hold the fetch until the test lets it go: an unpack caught mid-download.
    pathlib.Path(route['wait'] + '.waiting').touch()
    deadline = time.monotonic() + 60
    while not os.path.exists(route['wait']):
        if time.monotonic() > deadline:
            sys.exit('the test never released ' + route['wait'])
        time.sleep(0.01)
for path, content in route.get('create', []):
    pathlib.Path(path).mkdir(parents=True, exist_ok=True)
    (pathlib.Path(path) / 'host.json').write_text(content) if content else None
pathlib.Path(output).write_bytes(body)
lines = ['HTTP/2 ' + str(status), *header_lines]
pathlib.Path(headers_file).write_text('\r\n'.join(lines) + '\r\n')
sys.stdout.write(str(status))
"""


def _executable(path: Path, body: str) -> Path:
    path.write_text(f"#!{sys.executable}\n{body}")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def layer(*entries, gz: bool = True) -> bytes:
    """A layer: ("file", name, content[, mode]), ("dir", name), ("link", name, target),
    ("hard", name, target), ("fifo", name) or ("char", name)."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for kind, name, *rest in entries:
            info = tarfile.TarInfo(name)
            info.mode = 0o755 if kind == "dir" else 0o644
            content = b""
            match kind:
                case "file":
                    content = rest[0]
                    info.size = len(content)
                    info.mode = rest[1] if len(rest) > 1 else 0o644
                case "dir":
                    info.type = tarfile.DIRTYPE
                case "link":
                    info.type, info.linkname = tarfile.SYMTYPE, rest[0]
                case "hard":
                    info.type, info.linkname = tarfile.LNKTYPE, rest[0]
                case "fifo":
                    info.type = tarfile.FIFOTYPE
                case "char":
                    info.type = tarfile.CHRTYPE
            archive.addfile(info, io.BytesIO(content) if info.isreg() else None)
    return gzip.compress(buffer.getvalue()) if gz else buffer.getvalue()


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class Registry:
    """One repository on a fake registry host, and the ``curl`` that reaches it."""

    def __init__(self, tmp_path: Path, *, host="ghcr.io", repository="niuulabs/openshell"):
        self.directory = tmp_path / "registry"
        self.directory.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        _executable(self.bin / "curl", CURL)
        self.base = f"https://{host}/v2/{repository}"
        self.routes: dict[str, dict] = {}
        self.token: tuple[str, list[str]] | None = None
        self.files = 0
        self.cache = tmp_path / "cache"

    def serve(self, url: str, data: bytes, **route) -> str:
        self.files += 1
        path = self.directory / f"file-{self.files}"
        path.write_bytes(data)
        self.routes[url] = {"file": str(path), **route}
        return sha256(data)

    def blob(self, data: bytes) -> str:
        digest = sha256(data)
        self.serve(f"{self.base}/blobs/{digest}", data)
        return digest

    def document(self, document: dict, *selectors: str) -> str:
        data = json.dumps(document).encode()
        for selector in (*selectors, sha256(data)):
            self.serve(f"{self.base}/manifests/{selector}", data)
        return sha256(data)

    def image(
        self,
        layers: list[bytes],
        config: dict | None = None,
        *,
        tags=("latest",),
        media=GZIP,
        manifest_media=MANIFEST,
    ) -> str:
        """Serve an image; returns its manifest digest."""
        body = json.dumps({"config": config} if config is not None else {}).encode()
        document = {
            "schemaVersion": 2,
            "mediaType": manifest_media,
            "config": {"mediaType": "application/json", "digest": self.blob(body)},
            "layers": [{"mediaType": media, "digest": self.blob(data)} for data in layers],
        }
        return self.document(document, *tags)

    def index(self, linux: str, *, tags=("latest",), media=INDEX) -> str:
        """Serve an index of a foreign-architecture entry and ``linux`` for this host."""
        entries = [
            {
                "digest": "sha256:" + "9" * 64,
                "platform": {"os": "linux", "architecture": OTHER_ARCH},
            },
            {"digest": linux, "platform": {"os": "linux", "architecture": ARCH}},
        ]
        return self.document({"mediaType": media, "manifests": entries}, *tags)

    def run(self, reference, session=None, *, prelude="", cache=None, claim=None):
        return subprocess.run(
            self._command(reference, session, prelude, cache, claim),
            capture_output=True,
            text=True,
            env=self._environment(),
        )

    def start(self, reference, session=None, *, claim=None) -> subprocess.Popen:
        """The image program, running in the background (a slot starting beside others)."""
        return subprocess.Popen(
            self._command(reference, session, "", None, claim),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self._environment(),
        )

    def _environment(self) -> dict[str, str]:
        return {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "FAKE_REGISTRY": str(self.directory),
        }

    def _command(self, reference, session, prelude, cache, claim) -> list[str]:
        (self.directory / "routes.json").write_text(
            json.dumps(
                {
                    url: (
                        {**route, "token": self.token[0], "challenge": self.token[1]}
                        if self.token and url.startswith(self.base)
                        else route
                    )
                    for url, route in self.routes.items()
                }
            )
        )
        return [
            sys.executable,
            "-c",
            prelude + programs.IMAGE,
            reference,
            str(cache or self.cache),
            json.dumps(session),
            json.dumps(claim),
        ]

    def requests(self) -> list[dict]:
        log = self.directory / "requests.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def urls(self) -> list[str]:
        return [request["url"] for request in self.requests()]


@pytest.fixture
def registry(tmp_path):
    return Registry(tmp_path)


def session_layer() -> bytes:
    """An image with a relocatable Python, a native CLI and a node script CLI."""
    return layer(
        ("file", "sandbox/.uv/python/cpython-3.14.10-linux-x86_64-gnu/bin/python3.14", b"\x7fELF"),
        (
            "link",
            "sandbox/.uv/python/cpython-3.14-linux-x86_64-gnu",
            "cpython-3.14.10-linux-x86_64-gnu",
        ),
        (
            "link",
            "opt/niuu/bin/python",
            "/sandbox/.uv/python/cpython-3.14-linux-x86_64-gnu/bin/python3.14",
        ),
        ("file", "opt/tools/claude.exe", b"\x7fELF", 0o755),
        ("link", "usr/local/bin/claude", "/opt/tools/claude.exe"),
        ("file", "usr/bin/node", b"\x7fELF", 0o755),
        ("file", "usr/lib/codex/codex.js", b"#!/usr/bin/env node\nconsole.log(1)\n", 0o755),
        ("link", "usr/bin/codex", "../lib/codex/codex.js"),
        ("file", "dev/null", b""),
        ("file", "proc/1", b""),
    )


def loaded(result: subprocess.CompletedProcess) -> dict:
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def only_cache(registry: Registry) -> list[str]:
    """What the cache holds besides its lock."""
    return sorted(p.name for p in registry.cache.iterdir() if p.name != ".lock")


def installed(registry: Registry) -> list[str]:
    """The images in the cache, whatever scratch a failed fetch left beside them."""
    return [name for name in only_cache(registry) if not name.startswith(".")]


# -- fetching and unpacking an image -----------------------------------------------------------


def test_an_image_pulled_by_digest_is_unpacked_once_and_described(registry):
    digest = registry.image(
        [layer(("file", "etc/hello", b"hi"), ("dir", "var/lib"))],
        {"Env": ["A=1"], "User": "app", "WorkingDir": "/srv", "Entrypoint": ["/run"], "Cmd": ["x"]},
    )
    reference = f"ghcr.io/niuulabs/openshell@{digest}"

    first = registry.run(reference)

    assert loaded(first) == {
        "digest": digest,
        "reference": reference,
        "env": ["A=1"],
        "user": "app",
        "workdir": "/srv",
        "entrypoint": ["/run"],
        "cmd": ["x"],
    }
    root = registry.cache / digest.split(":")[1]
    assert (root / "rootfs/etc/hello").read_bytes() == b"hi"
    assert json.loads((root / "host.json").read_text()) == loaded(first)
    assert only_cache(registry) == [digest.split(":")[1]]
    assert (registry.cache.stat().st_mode & 0o777) == 0o755 and (
        root.stat().st_mode & 0o777
    ) == 0o755
    # A layer is deleted once unpacked: only the rootfs and its description stay.
    assert sorted(p.name for p in root.iterdir()) == ["host.json", "rootfs"]

    before = len(registry.requests())
    second = registry.run(reference)

    assert loaded(second) == loaded(first)
    # The second run asks the registry for the manifest only; nothing is fetched or unpacked.
    assert registry.urls()[before:] == [f"{registry.base}/manifests/{digest}"]


def test_a_configless_image_gets_empty_defaults(registry):
    digest = registry.image([layer(("dir", "x"))], config=None)

    description = loaded(registry.run(f"ghcr.io/niuulabs/openshell@{digest}"))

    assert (description["env"], description["user"], description["workdir"]) == ([], "", "")
    assert (description["entrypoint"], description["cmd"]) == ([], [])


@pytest.mark.parametrize("media", [INDEX, DOCKER_INDEX])
def test_an_index_selects_the_hosts_own_platform(registry, media):
    image = registry.image([layer(("file", "marker", b"mine"))], {})
    index = registry.index(image, media=media)

    result = registry.run(f"ghcr.io/niuulabs/openshell@{index}")

    # The cache is named after the selected image's digest, not the index's.
    assert loaded(result)["digest"] == image
    assert (registry.cache / image.split(":")[1] / "rootfs/marker").read_bytes() == b"mine"
    assert f"{registry.base}/manifests/{'sha256:' + '9' * 64}" not in registry.urls()
    accepted = registry.requests()[0]["accept"]
    assert INDEX in accepted and DOCKER_INDEX in accepted and MANIFEST in accepted


def test_an_index_with_no_image_for_this_platform_is_refused_naming_it(registry):
    index = registry.document(
        {
            "mediaType": INDEX,
            "manifests": [
                {
                    "digest": "sha256:" + "9" * 64,
                    "platform": {"os": "linux", "architecture": "s390x"},
                },
                {
                    "digest": "sha256:" + "8" * 64,
                    "platform": {"os": "windows", "architecture": ARCH},
                },
            ],
        }
    )

    result = registry.run(f"ghcr.io/niuulabs/openshell@{index}")

    assert result.returncode != 0 and f"has no linux/{ARCH} image" in result.stderr
    assert not registry.cache.exists() or only_cache(registry) == []


@pytest.mark.parametrize(
    ("reference", "manifest_url"),
    [
        ("ghcr.io/niuulabs/openshell:dev", "https://ghcr.io/v2/niuulabs/openshell/manifests/dev"),
        ("ghcr.io/niuulabs/openshell", "https://ghcr.io/v2/niuulabs/openshell/manifests/latest"),
        ("alpine", "https://registry-1.docker.io/v2/library/alpine/manifests/latest"),
        ("alpine:3.20", "https://registry-1.docker.io/v2/library/alpine/manifests/3.20"),
        ("docker.io/alpine:3", "https://registry-1.docker.io/v2/alpine/manifests/3"),
        (
            "docker.io/library/alpine:3",
            "https://registry-1.docker.io/v2/library/alpine/manifests/3",
        ),
        (
            "grafana/grafana:10.1",
            "https://registry-1.docker.io/v2/grafana/grafana/manifests/10.1",
        ),
        ("localhost/team/app", "https://localhost/v2/team/app/manifests/latest"),
        (
            "localhost:5000/team/app:v2",
            "https://localhost:5000/v2/team/app/manifests/v2",
        ),
        (
            "registry.example.com:8443/a/b/c:t",
            "https://registry.example.com:8443/v2/a/b/c/manifests/t",
        ),
    ],
)
def test_a_reference_names_its_registry_repository_and_tag(registry, reference, manifest_url):
    base, _, _ = manifest_url.partition("/manifests/")
    registry.base = base
    registry.image([layer(("dir", "x"))], {}, tags=(manifest_url.rsplit("/", 1)[1],))

    result = registry.run(reference)

    assert result.returncode == 0, result.stderr
    assert registry.urls()[0] == manifest_url


def test_a_digest_reference_is_fetched_by_digest_whatever_its_tag(registry):
    digest = registry.image([layer(("dir", "x"))], {})

    result = registry.run(f"ghcr.io/niuulabs/openshell:dev@{digest}")

    assert result.returncode == 0, result.stderr
    assert registry.urls()[0] == f"{registry.base}/manifests/{digest}"


@pytest.mark.parametrize("pinned", ["sha256:abc", "sha1:" + "a" * 40, "sha256:" + "A" * 64])
def test_a_digest_that_is_not_sha256_hex_is_refused_before_any_request(registry, pinned):
    result = registry.run(f"ghcr.io/niuulabs/openshell@{pinned}")

    assert result.returncode != 0 and "Not an image digest" in result.stderr
    assert registry.urls() == []


def test_a_registry_that_answers_with_another_manifest_than_the_pinned_digest_is_refused(registry):
    wanted = "sha256:" + "7" * 64
    registry.image([layer(("dir", "x"))], {}, tags=())
    # The manifest the pinned digest names is served with different content.
    registry.serve(f"{registry.base}/manifests/{wanted}", b'{"layers": []}')

    result = registry.run(f"ghcr.io/niuulabs/openshell@{wanted}")

    assert result.returncode != 0 and "served" in result.stderr and "with digest" in result.stderr
    assert installed(registry) == []


def test_a_blob_that_does_not_match_its_digest_is_refused_and_nothing_is_kept(registry):
    digest = registry.image([layer(("file", "etc/x", b"1"))], {})
    document = json.loads(
        Path(registry.routes[f"{registry.base}/manifests/{digest}"]["file"]).read_text()
    )
    wanted = document["layers"][0]["digest"]
    registry.serve(f"{registry.base}/blobs/{wanted}", b"tampered")

    result = registry.run(f"ghcr.io/niuulabs/openshell@{digest}")

    assert result.returncode != 0
    assert f"The registry served blob {wanted} with digest {sha256(b'tampered')}" in result.stderr
    assert only_cache(registry) == []


def test_a_registry_that_does_not_have_the_manifest_is_a_loud_failure(registry):
    result = registry.run("ghcr.io/niuulabs/openshell:gone")

    assert result.returncode != 0
    assert "manifests/gone answered HTTP 404" in result.stderr


def test_zstd_layers_are_refused_with_the_remedy(registry):
    digest = registry.image(
        [layer(("dir", "x"))], {}, media="application/vnd.oci.image.layer.v1.tar+zstd"
    )

    result = registry.run(f"ghcr.io/niuulabs/openshell@{digest}")

    assert result.returncode != 0
    assert (
        "has zstd layers" in result.stderr and "publish the image with gzip layers" in result.stderr
    )
    assert only_cache(registry) == []


def test_docker_schema_manifests_and_uncompressed_layers_are_read(registry):
    digest = registry.image(
        [layer(("file", "f", b"plain"), gz=False)],
        {},
        media="application/vnd.docker.image.rootfs.diff.tar",
        manifest_media=DOCKER_MANIFEST,
    )

    loaded(registry.run(f"ghcr.io/niuulabs/openshell@{digest}"))

    assert (registry.cache / digest.split(":")[1] / "rootfs/f").read_bytes() == b"plain"


# -- anonymous pull tokens ---------------------------------------------------------------------

CHALLENGE = [
    'WWW-Authenticate: Bearer realm="https://auth.example/token",service="ghcr.io",'
    'scope="repository:niuulabs/openshell:pull"'
]
TOKEN_URL = "https://auth.example/token?service=ghcr.io&scope=repository:niuulabs/openshell:pull"


@pytest.mark.parametrize("field", ["token", "access_token"])
def test_a_bearer_challenge_is_answered_with_an_anonymous_pull_token(registry, field):
    digest = registry.image([layer(("file", "etc/x", b"1"))], {})
    registry.token = ("granted", CHALLENGE)
    registry.serve(TOKEN_URL, json.dumps({field: "granted"}).encode())

    result = registry.run(f"ghcr.io/niuulabs/openshell@{digest}")

    assert result.returncode == 0, result.stderr
    requests = registry.requests()
    # Unauthenticated first, then the token endpoint, then every call carries the token.
    assert [r["bearer"] for r in requests[:2]] == ["", ""]
    assert requests[1]["url"] == TOKEN_URL
    assert requests[2]["url"] == requests[0]["url"] and requests[2]["bearer"] == "granted"
    assert all(r["bearer"] == "granted" for r in requests[2:])
    assert (registry.cache / digest.split(":")[1] / "rootfs/etc/x").exists()


def test_a_token_endpoint_that_refuses_means_the_image_must_be_public(registry):
    digest = registry.image([layer(("dir", "x"))], {})
    registry.token = ("granted", CHALLENGE)
    registry.routes[TOKEN_URL] = {"status": 403}

    result = registry.run(f"ghcr.io/niuulabs/openshell@{digest}")

    assert result.returncode != 0
    assert "refused an anonymous pull token (HTTP 403)" in result.stderr
    assert "must be public" in result.stderr


def test_a_registry_that_asks_for_something_other_than_a_bearer_token_is_refused(registry):
    digest = registry.image([layer(("dir", "x"))], {})
    registry.token = ("granted", ['WWW-Authenticate: Basic realm="registry"'])

    result = registry.run(f"ghcr.io/niuulabs/openshell@{digest}")

    assert result.returncode != 0 and "asks for no bearer token" in result.stderr


def test_a_challenge_without_service_or_scope_still_asks_the_realm(registry):
    digest = registry.image([layer(("dir", "x"))], {})
    registry.token = ("granted", ['www-authenticate: Bearer realm="https://auth.example/token"'])
    registry.serve("https://auth.example/token", b'{"token": "granted"}')

    result = registry.run(f"ghcr.io/niuulabs/openshell@{digest}")

    assert result.returncode == 0, result.stderr
    assert registry.urls()[1] == "https://auth.example/token"


# -- the layer unpack --------------------------------------------------------------------------


def unpacked(registry, *layers, config=None) -> Path:
    digest = registry.image(list(layers), config if config is not None else {})
    result = registry.run(f"ghcr.io/niuulabs/openshell@{digest}")
    assert result.returncode == 0, result.stderr
    return registry.cache / digest.split(":")[1] / "rootfs"


def tree(root: Path) -> list[str]:
    return sorted(
        str(p.relative_to(root)) + ("@" if p.is_symlink() else "/" if p.is_dir() else "")
        for p in root.rglob("*")
    )


def test_a_whiteout_removes_the_file_and_a_later_layer_may_bring_it_back(registry):
    root = unpacked(
        registry,
        layer(
            ("file", "etc/a", b"1"),
            ("file", "etc/b", b"2"),
            ("dir", "etc/d"),
            ("file", "etc/d/x", b"3"),
        ),
        layer(
            ("file", "etc/.wh.a", b""), ("file", "etc/.wh.d", b""), ("file", "etc/.wh.nothing", b"")
        ),
        layer(("file", "etc/d", b"now a file")),
    )

    assert tree(root) == ["etc/", "etc/b", "etc/d"]
    assert (root / "etc/d").read_bytes() == b"now a file"


def test_an_opaque_whiteout_empties_the_directory_but_keeps_what_its_layer_adds(registry):
    root = unpacked(
        registry,
        layer(
            ("file", "data/x", b"1"),
            ("link", "data/l", "/elsewhere"),
            ("dir", "data/sub"),
            ("file", "data/sub/y", b"2"),
            ("file", "keep", b"k"),
        ),
        layer(("file", "data/.wh..wh..opq", b""), ("file", "data/z", b"3")),
    )

    assert tree(root) == ["data/", "data/z", "keep"]


def test_an_opaque_whiteout_on_a_directory_the_image_does_not_have_is_harmless(registry):
    root = unpacked(registry, layer(("file", "a", b"1"), ("file", "missing/.wh..wh..opq", b"")))

    assert tree(root) == ["a"]


def test_a_later_layer_replaces_files_directories_and_links_by_name(registry):
    root = unpacked(
        registry,
        layer(
            ("file", "f", b"old"),
            ("link", "l", "target-old"),
            ("dir", "d"),
            ("file", "d/inside", b"x"),
            ("file", "becomes-dir", b"x"),
        ),
        layer(
            ("file", "f", b"new"),
            ("file", "l", b"was a link"),
            ("link", "d", "f"),
            ("dir", "becomes-dir"),
            ("file", "becomes-dir/y", b"y"),
        ),
    )

    assert (root / "f").read_bytes() == b"new"
    assert not (root / "l").is_symlink() and (root / "l").read_bytes() == b"was a link"
    assert (root / "d").is_symlink() and os.readlink(root / "d") == "f"
    assert (root / "becomes-dir/y").read_bytes() == b"y"


def test_a_directory_listed_again_keeps_what_an_earlier_layer_put_in_it(registry):
    root = unpacked(
        registry,
        layer(("dir", "d"), ("file", "d/a", b"1")),
        layer(("dir", "d"), ("file", "d/b", b"2")),
    )

    assert tree(root) == ["d/", "d/a", "d/b"]


def test_devices_fifos_and_the_kernels_directories_are_not_unpacked(registry):
    root = unpacked(
        registry,
        layer(
            ("char", "nodes/null"),
            ("fifo", "nodes/pipe"),
            ("file", "dev/zero", b""),
            ("file", "proc/1", b""),
            ("file", "sys/x", b""),
            ("file", "etc/real", b"1"),
        ),
    )

    assert tree(root) == ["etc/", "etc/real"]


def test_hard_links_inside_the_root_are_kept(registry):
    root = unpacked(
        registry, layer(("file", "bin/real", b"one"), ("hard", "bin/alias", "bin/real"))
    )

    assert (root / "bin/alias").read_bytes() == b"one"
    assert (root / "bin/alias").samefile(root / "bin/real")


@pytest.mark.parametrize("target", ["../../outside", "../../../../../../etc/passwd"])
def test_a_hard_link_to_outside_the_root_is_refused_and_nothing_is_kept(registry, target):
    digest = registry.image([layer(("file", "ok", b"1"), ("hard", "alias", target))], {})

    result = registry.run(f"ghcr.io/niuulabs/openshell@{digest}")

    assert result.returncode != 0 and "hard link outside the root: alias" in result.stderr
    assert only_cache(registry) == []


def test_an_absolute_hard_link_is_refused_never_linking_a_file_of_the_host(registry, tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("host")
    digest = registry.image([layer(("hard", "alias", str(victim)))], {})

    result = registry.run(f"ghcr.io/niuulabs/openshell@{digest}")

    # tarfile joins a hard link's name to the root, so an absolute one names the host's file.
    assert result.returncode != 0 and "hard link outside the root: alias" in result.stderr
    assert victim.read_text() == "host" and victim.stat().st_nlink == 1
    assert only_cache(registry) == []


def test_a_whiteout_cannot_reach_outside_the_root(registry, tmp_path):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "f").write_text("keep")
    digest = registry.image(
        [layer(("file", "ok", b"1"), ("file", "../../../victim/.wh.f", b""))], {}
    )

    result = registry.run(f"ghcr.io/niuulabs/openshell@{digest}")

    assert result.returncode != 0 and "entry outside the root" in result.stderr
    assert (victim / "f").read_text() == "keep"
    assert only_cache(registry) == []


def test_an_opaque_whiteout_cannot_empty_a_directory_outside_the_root(registry, tmp_path):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "f").write_text("keep")
    digest = registry.image([layer(("file", "../../../victim/.wh..wh..opq", b""))], {})

    result = registry.run(f"ghcr.io/niuulabs/openshell@{digest}")

    assert result.returncode != 0 and "entry outside the root" in result.stderr
    assert (victim / "f").read_text() == "keep"


def test_nothing_is_written_through_a_link_the_image_planted_to_outside_the_root(
    registry, tmp_path
):
    outside = tmp_path / "outside"
    outside.mkdir()
    digest = registry.image(
        [layer(("link", "escape", str(outside)), ("file", "escape/pwned", b"gotcha"))], {}
    )

    result = registry.run(f"ghcr.io/niuulabs/openshell@{digest}")

    assert result.returncode != 0
    assert list(outside.iterdir()) == []
    assert only_cache(registry) == []


def test_an_entry_with_a_leading_slash_or_dotdot_cannot_land_outside(registry, tmp_path):
    digest = registry.image(
        [layer(("file", "/abs/inside", b"1"), ("file", "../../../escaped", b"2"))], {}
    )

    result = registry.run(f"ghcr.io/niuulabs/openshell@{digest}")

    assert result.returncode != 0
    assert not (tmp_path / "escaped").exists()
    assert only_cache(registry) == []


def test_an_unpack_that_finds_its_digest_already_unpacked_by_a_concurrent_one_keeps_that_one(
    registry,
):
    digest = registry.image([layer(("file", "etc/x", b"1"))], {})
    hex_digest = digest.split(":")[1]
    # Another unpack finishes its rename while this one is still fetching its blobs.
    other = registry.cache / hex_digest
    for url in registry.routes:
        if "/blobs/" in url:
            registry.routes[url]["create"] = [[str(other), '{"digest": "raced"}']]

    result = registry.run(f"ghcr.io/niuulabs/openshell@{digest}")

    assert loaded(result) == {"digest": "raced"}
    assert only_cache(registry) == [hex_digest]


def test_an_unpack_that_cannot_take_its_place_and_finds_no_other_fails(registry):
    digest = registry.image([layer(("file", "etc/x", b"1"))], {})
    hex_digest = digest.split(":")[1]
    for url in registry.routes:
        if "/blobs/" in url:
            # A non-empty directory without a description: the rename fails and nothing finished.
            registry.routes[url]["create"] = [[str(registry.cache / hex_digest / "stuck"), ""]]

    result = registry.run(f"ghcr.io/niuulabs/openshell@{digest}")

    assert result.returncode != 0
    assert [name for name in only_cache(registry) if name.startswith(".")] == []


def test_a_host_python_without_extraction_filters_is_refused_before_any_request(registry):
    result = registry.run(
        "ghcr.io/niuulabs/openshell:dev", prelude="import tarfile\ndel tarfile.tar_filter\n"
    )

    assert result.returncode != 0
    assert "no tarfile extraction filters" in result.stderr
    assert registry.urls() == []


def test_a_host_architecture_with_no_image_platform_is_refused_before_any_request(registry):
    result = registry.run(
        "ghcr.io/niuulabs/openshell:dev",
        prelude="import platform\nplatform.machine = lambda: 'riscv64'\n",
    )

    assert result.returncode != 0
    assert "No image platform for this host architecture riscv64" in result.stderr
    assert registry.urls() == []


# -- the session image -------------------------------------------------------------------------


def test_the_session_image_gets_its_relocatable_python_and_wrapped_commands(registry):
    digest = registry.image([session_layer()], {"Env": ENV})

    description = loaded(registry.run(f"ghcr.io/niuulabs/openshell@{digest}", SESSION))

    assert description["venv"] == "/opt/niuu"
    assert description["python_root"] == "/sandbox/.uv/python"
    root = registry.cache / digest.split(":")[1]
    assert (root / "rootfs/opt/niuu/bin/python").is_symlink()
    assert not (root / "rootfs/dev/null").exists() and not (root / "rootfs/proc").exists()
    claude = (root / "agents/claude").read_text()
    assert claude == '#!/bin/sh\nexec /opt/niuu-skuld/rootfs/opt/tools/claude.exe "$@"\n'
    assert (root / "agents/claude").stat().st_mode & 0o777 == 0o755
    # A node script runs on the image's node, whatever node the host has.
    assert (root / "agents/codex").read_text() == (
        "#!/bin/sh\nexec /opt/niuu-skuld/rootfs/usr/bin/node "
        '/opt/niuu-skuld/rootfs/usr/lib/codex/codex.js "$@"\n'
    )
    # What the image does not have gets no wrapper.
    assert not (root / "agents/grok").exists() and not (root / "tools/gh").exists()
    assert (root / "tools/node").exists()
    assert sorted(p.name for p in root.iterdir()) == ["agents", "host.json", "rootfs", "tools"]


def test_a_pod_image_gets_no_wrappers_and_no_python_check(registry):
    digest = registry.image([session_layer()], {"Env": []})

    description = loaded(registry.run(f"ghcr.io/niuulabs/openshell@{digest}"))

    assert "venv" not in description
    assert sorted(p.name for p in (registry.cache / digest.split(":")[1]).iterdir()) == [
        "host.json",
        "rootfs",
    ]


def test_an_image_on_the_distributions_python_is_refused(registry):
    digest = registry.image(
        [session_layer()], {"Env": ["PATH=/opt/venv/bin:/usr/bin", "VIRTUAL_ENV=/opt/venv"]}
    )

    result = registry.run(f"ghcr.io/niuulabs/openshell@{digest}", SESSION)

    assert result.returncode != 0 and "relocatable Python" in result.stderr
    # The image itself is unpacked (a sidecar may run it); no session parts are added.
    root = registry.cache / digest.split(":")[1]
    assert only_cache(registry) == [root.name]
    assert sorted(p.name for p in root.iterdir()) == ["host.json", "rootfs"]
    assert "venv" not in json.loads((root / "host.json").read_text())


def test_a_session_image_a_sidecar_unpacked_first_gets_its_session_parts(registry):
    """The guest agent runs the session image too, and its pods start first."""
    digest = registry.image([session_layer()], {"Env": ENV})
    reference = f"ghcr.io/niuulabs/openshell@{digest}"

    assert "venv" not in loaded(registry.run(reference))
    description = loaded(registry.run(reference, SESSION))

    assert description["venv"] == "/opt/niuu"
    root = registry.cache / digest.split(":")[1]
    assert sorted(p.name for p in root.iterdir()) == ["agents", "host.json", "rootfs", "tools"]
    assert json.loads((root / "host.json").read_text())["venv"] == "/opt/niuu"
    assert only_cache(registry) == [root.name]


def test_a_venv_whose_python_is_outside_its_python_root_is_refused(registry):
    digest = registry.image(
        [session_layer()], {"Env": [*ENV[:2], "UV_PYTHON_INSTALL_DIR=/usr/lib/python"]}
    )

    result = registry.run(f"ghcr.io/niuulabs/openshell@{digest}", SESSION)

    assert result.returncode != 0 and "does not run on its own Python" in result.stderr


def test_a_symbolic_link_loop_in_the_session_image_is_refused(registry):
    looped = layer(("link", "opt/niuu", "/opt/niuu"))
    digest = registry.image([looped], {"Env": ENV})

    result = registry.run(f"ghcr.io/niuulabs/openshell@{digest}", SESSION)

    assert result.returncode != 0 and "Symbolic link loop" in result.stderr


def test_a_script_whose_interpreter_the_image_lacks_is_refused(registry):
    digest = registry.image(
        [
            session_layer(),
            layer(("file", "usr/lib/codex/codex.js", b"#!/usr/bin/env deno\nx\n", 0o755)),
        ],
        {"Env": ENV},
    )

    result = registry.run(f"ghcr.io/niuulabs/openshell@{digest}", SESSION)

    assert result.returncode != 0 and "has no interpreter for codex" in result.stderr


# -- pruning the cache -------------------------------------------------------------------------


class Proc:
    """A ``/proc`` of fake processes: each a mount namespace link and a mount table."""

    def __init__(self, path: Path):
        self.path = path
        self.path.mkdir()
        self.pids = 0

    def process(self, namespace: int, mountinfo: str = "") -> Path:
        self.pids += 1
        directory = self.path / str(self.pids)
        (directory / "ns").mkdir(parents=True)
        (directory / "ns" / "mnt").symlink_to(f"mnt:[{namespace}]")
        (directory / "mountinfo").write_text(mountinfo)
        return directory


@pytest.fixture
def proc(tmp_path):
    fake = Proc(tmp_path / "proc")
    fake.process(1, "22 1 8:1 / / rw,relatime shared:1 - ext4 /dev/sda1 rw\n")
    return fake


def prune(cache: Path, proc: Proc, keep: list[str] = ()) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", programs.PRUNE, str(cache), json.dumps(list(keep)), str(proc.path)],
        capture_output=True,
        text=True,
    )


def pruned(cache: Path, proc: Proc, keep: list[str] = ()) -> dict:
    return loaded(prune(cache, proc, keep))


def cached(registry: Registry, content: bytes, claim=None, *, tags=("latest",)) -> str:
    """An image served and unpacked; returns its root's name (the manifest digest)."""
    digest = registry.image([layer(("file", "etc/release", content))], {}, tags=tags)
    loaded(registry.run(f"ghcr.io/niuulabs/openshell@{digest}", claim=claim))
    return digest.split(":")[1]


def slot(tmp_path: Path, name: str) -> dict:
    """A slot's claim, alive while its (stand-in) namespace and cgroup paths exist."""
    alive = [tmp_path / "netns" / name, tmp_path / "cgroup" / f"{name}.slice"]
    for path in alive:
        path.mkdir(parents=True)
    return {"slot": name, "alive": [str(path) for path in alive]}


def gone(claim: dict) -> None:
    for path in claim["alive"]:
        os.rmdir(path)


def held(path: Path, mode=None):
    """Hold ``path`` as a running unpack does (a shared lock on the directory)."""
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(fd, mode if mode is not None else fcntl.LOCK_SH)
    return fd


def test_a_prune_of_a_cache_never_made_removes_nothing(tmp_path, proc):
    assert pruned(tmp_path / "cache", proc) == {
        "removed": [],
        "in_use": [],
        "kept": [],
        "unknown": [],
    }
    assert not (tmp_path / "cache").exists()


def test_old_releases_go_and_the_kept_ones_stay_by_digest_or_by_reference(registry, proc):
    old = cached(registry, b"1", tags=("v1",))
    by_digest = cached(registry, b"2", tags=("v2",))
    by_tag = registry.image([layer(("file", "etc/release", b"3"))], {}, tags=("v3",))
    loaded(registry.run("ghcr.io/niuulabs/openshell:v3"))
    tagged = by_tag.split(":")[1]

    result = pruned(
        registry.cache,
        proc,
        [f"ghcr.io/niuulabs/openshell@sha256:{by_digest}", "ghcr.io/niuulabs/openshell:v3"],
    )

    assert result["removed"] == [old]
    assert sorted(result["kept"]) == sorted([by_digest, tagged])
    assert only_cache(registry) == sorted([by_digest, tagged])


def test_a_root_a_live_slot_claims_stays_until_the_slot_is_gone(registry, proc, tmp_path):
    claim = slot(tmp_path, "niuu-slot-1")
    root = cached(registry, b"1", claim)
    record = json.loads((registry.cache / ".claims" / "niuu-slot-1.json").read_text())
    assert record == {"digests": [root], "alive": claim["alive"]}

    assert pruned(registry.cache, proc)["in_use"] == [root]
    assert only_cache(registry) == [".claims", root]

    # Torn down: its namespace went, its cgroup stays until nothing of the slot runs.
    os.rmdir(claim["alive"][0])
    assert pruned(registry.cache, proc)["in_use"] == [root]
    os.rmdir(claim["alive"][1])
    result = pruned(registry.cache, proc)

    assert result["removed"] == [root]
    assert only_cache(registry) == [".claims"]
    assert list((registry.cache / ".claims").iterdir()) == []


def test_a_slot_claims_every_image_it_runs_from_and_one_unpacked_before(registry, proc, tmp_path):
    warm = cached(registry, b"session")  # a warm unpack: no slot, no claim
    claim = slot(tmp_path, "niuu-slot-0")
    agent = cached(registry, b"agent", claim, tags=("agent",))
    loaded(registry.run(f"ghcr.io/niuulabs/openshell@sha256:{warm}", claim=claim))

    record = json.loads((registry.cache / ".claims" / "niuu-slot-0.json").read_text())
    assert record["digests"] == [agent, warm]
    assert sorted(pruned(registry.cache, proc)["in_use"]) == sorted([agent, warm])


def test_one_slots_teardown_leaves_the_roots_another_slot_runs_from(registry, proc, tmp_path):
    first, second = slot(tmp_path, "niuu-slot-0"), slot(tmp_path, "niuu-slot-1")
    shared = cached(registry, b"shared", first, tags=("shared",))
    loaded(registry.run(f"ghcr.io/niuulabs/openshell@sha256:{shared}", claim=second))
    own = cached(registry, b"own", first, tags=("own",))

    gone(first)
    result = pruned(registry.cache, proc)

    assert result == {"removed": [own], "in_use": [shared], "kept": [], "unknown": []}
    assert [path.name for path in (registry.cache / ".claims").iterdir()] == ["niuu-slot-1.json"]


def test_a_root_any_mount_table_names_stays(registry, proc):
    skuld = cached(registry, b"skuld", tags=("skuld",))
    agent = cached(registry, b"agent", tags=("agent",))
    unused = cached(registry, b"unused", tags=("unused",))
    cache = registry.cache
    # A session that started before claims existed: Skuld's view is a bind mount of its root,
    # an agent an overlay on its rootfs, each seen from its own mount namespace.
    skuld_table = (
        f"301 22 8:1 {cache}/{skuld} /opt/niuu-skuld ro,relatime master:1 - ext4 /dev/sda1 rw\n"
    )
    proc.process(7, skuld_table)
    proc.process(7, skuld_table)  # the same namespace again, read once
    proc.process(
        8,
        f"402 22 0:51 / /run/niuu/host-pods/x/root rw - overlay overlay "
        f"rw,lowerdir={cache}/{agent}/rootfs,upperdir=/run/u,workdir=/run/w\n",
    )
    ended = proc.process(9)
    os.unlink(ended / "mountinfo")  # a process that ended mid-scan

    result = pruned(cache, proc)

    assert sorted(result["in_use"]) == sorted([skuld, agent])
    assert result["removed"] == [unused]


def test_unpack_leftovers_go_unless_a_running_program_holds_them(registry, proc):
    cached(registry, b"1")
    cache = registry.cache
    leftovers = [cache / name for name in (".fetch-dead", ".session-dead", ".trash-dead")]
    for path in leftovers:
        (path / "rootfs").mkdir(parents=True)
    running = cache / ".fetch-running"
    running.mkdir()
    fd = held(running)
    try:
        result = pruned(cache, proc)
    finally:
        os.close(fd)

    assert result["in_use"] == [".fetch-running"]
    assert not [path for path in leftovers if path.exists()]
    assert running.exists()


def test_entries_forge_did_not_make_are_left_and_named(registry, proc):
    registry.cache.mkdir()
    (registry.cache / "notes.txt").write_text("mine")
    (registry.cache / ("f" * 64)).write_text("a file with a root's name")

    result = pruned(registry.cache, proc)

    assert sorted(result["unknown"]) == sorted(["f" * 64, "notes.txt"])
    assert (registry.cache / "notes.txt").read_text() == "mine"


def test_a_prune_that_cannot_read_a_mount_table_fails_naming_it(registry, proc):
    if os.geteuid() == 0:
        pytest.skip("root reads every mount table")
    old = cached(registry, b"1")
    hidden = proc.process(5, "")
    (hidden / "mountinfo").chmod(0)

    result = prune(registry.cache, proc)

    assert result.returncode != 0 and "PermissionError" in result.stderr
    assert old in only_cache(registry)


def test_a_claim_that_is_not_a_slots_is_refused_before_any_request(registry):
    for claim in (
        {"slot": "../escape", "alive": ["/run/netns/x"]},
        {"slot": "niuu-slot-0", "alive": []},
        {"slot": "niuu-slot-0", "alive": ["relative"]},
    ):
        result = registry.run("ghcr.io/niuulabs/openshell:dev", claim=claim)
        assert result.returncode != 0 and "Not a slot claim" in result.stderr
    assert registry.urls() == []


def test_a_prune_beside_a_slot_mid_unpack_removes_nothing_that_slot_needs(registry, proc, tmp_path):
    """Slot 1 starts while slot 0's session stops and prunes: the race this cache must win."""
    old = cached(registry, b"old", tags=("old",))
    digest = registry.image([layer(("file", "etc/release", b"new"))], {}, tags=("new",))
    root = digest.split(":")[1]
    release = tmp_path / "release-blobs"
    for url in registry.routes:
        if "/blobs/" in url:
            registry.routes[url]["wait"] = str(release)
    claim = slot(tmp_path, "niuu-slot-1")
    starting = registry.start(f"ghcr.io/niuulabs/openshell@{digest}", claim=claim)
    try:
        waiting = Path(str(release) + ".waiting")
        deadline = time.monotonic() + 60
        while not waiting.exists() and starting.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert waiting.exists(), starting.communicate()

        # Mid-download: the slot has claimed the digest and holds its work directory.
        during = pruned(registry.cache, proc)
        assert during["removed"] == [old]
        assert [name for name in during["in_use"] if name.startswith(".fetch-")]
        assert (
            root
            in json.loads((registry.cache / ".claims" / "niuu-slot-1.json").read_text())["digests"]
        )
    finally:
        release.touch()
        out, err = starting.communicate(timeout=60)
    assert starting.returncode == 0, err
    assert json.loads(out)["digest"] == digest

    # Unpacked and running: the claim keeps it; torn down, it goes.
    assert pruned(registry.cache, proc)["in_use"] == [root]
    assert only_cache(registry) == [".claims", root]
    gone(claim)
    assert pruned(registry.cache, proc)["removed"] == [root]


def test_an_unpack_claims_its_root_only_under_the_caches_lock(registry, proc, tmp_path):
    """While a prune holds the lock, a starting slot can neither claim nor read a root."""
    root = cached(registry, b"1")
    claim = slot(tmp_path, "niuu-slot-2")
    lock = os.open(str(registry.cache / ".lock"), os.O_RDWR)
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        starting = registry.start(f"ghcr.io/niuulabs/openshell@sha256:{root}", claim=claim)
        try:
            starting.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        assert starting.poll() is None
        assert not (registry.cache / ".claims").exists()
    finally:
        os.close(lock)
    out, err = starting.communicate(timeout=60)

    assert starting.returncode == 0, err
    assert pruned(registry.cache, proc)["in_use"] == [root]


def test_a_root_an_unpack_holds_is_not_removed_even_unclaimed(registry, proc):
    """A warm unpack claims nothing; while it runs, the lock it holds on the root keeps it."""
    root = cached(registry, b"1")
    fd = held(registry.cache / root)
    try:
        result = pruned(registry.cache, proc)
    finally:
        os.close(fd)

    assert result["in_use"] == [root]
    assert pruned(registry.cache, proc)["removed"] == [root]


# -- the pod containers' units -----------------------------------------------------------------

# One script stands in for systemctl, systemd-run and ip: it logs its argv and keeps the set of
# active units in a directory, so the programs see the units they started.
TOOL = r"""
import json, os, pathlib, sys
state = pathlib.Path(os.environ['FAKE_UNITS'])
tool, args = os.path.basename(sys.argv[0]), sys.argv[1:]
with open(str(state / 'calls.jsonl'), 'a') as log:
    log.write(json.dumps({'tool': tool, 'argv': args}) + '\n')
def read(name, default):
    path = state / name
    return json.loads(path.read_text()) if path.exists() else default
active = read('active.json', [])
def save():
    (state / 'active.json').write_text(json.dumps(active))
if tool == 'systemctl':
    verb = args[0]
    if verb == 'list-units':
        prefix = args[-1].rstrip('*')
        for unit in active:
            if unit.startswith(prefix):
                print(unit, 'loaded active running Pod')
    elif verb == 'is-active':
        sys.exit(0 if args[-1] in active else 3)
    elif verb == 'stop':
        if args[1] in read('stubborn.json', []):
            sys.exit(5)
        active[:] = [unit for unit in active if unit != args[1]]
        save()
elif tool == 'systemd-run':
    unit = args[args.index('--unit') + 1]
    if '--wait' in args:
        sys.exit(read('init_status.json', {}).get(unit, 0))
    if unit not in read('never_active.json', []):
        active.append(unit)
        save()
elif tool == 'ip' and 'show' in args:
    sys.stdout.write(read('addresses.json', ''))
"""


class Guest:
    """A directory of fake systemctl, systemd-run and ip, and the paths a slot's pods use."""

    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.bin = tmp_path / "tools"
        self.bin.mkdir()
        self.state = tmp_path / "fake-units"
        self.state.mkdir()
        for name in ("systemctl", "systemd-run", "ip"):
            _executable(self.bin / name, TOOL)
        self.images = tmp_path / "images"
        self.pods = {
            "prefix": "niuu-slot-2-",
            "slice": "niuu_slot_2.slice",
            "state": str(tmp_path / "run/pods"),
            "logs": str(tmp_path / "log/pods"),
            "work": str(tmp_path / "work"),
            "resolv_conf": "/run/systemd/resolve/resolv.conf",
            "program": "print('container program')\n",
            "images": {},
            "stop_timeout": 30,
            "poll": 0.02,
            "start_timeout": 0.5,
        }

    def image(self, name="agent", *, passwd="", group="", **config) -> str:
        """A pod image's unpacked root (just its accounts) and its description."""
        rootfs = self.images / name / "rootfs"
        (rootfs / "etc").mkdir(parents=True)
        if passwd:
            (rootfs / "etc/passwd").write_text(passwd)
        if group:
            (rootfs / "etc/group").write_text(group)
        reference = f"registry.example/{name}@sha256:{'1' * 64}"
        self.pods["images"][reference] = {
            "rootfs": str(rootfs),
            "env": config.get("env", []),
            "user": config.get("user", ""),
            "workdir": config.get("workdir", ""),
            "entrypoint": config.get("entrypoint", ["/agent"]),
            "cmd": config.get("cmd", []),
        }
        return reference

    def container(self, image: str, name="agent", **fields) -> dict:
        return {
            "name": name,
            "image": image,
            "command": [],
            "args": [],
            "environment": {},
            "mounts": [],
            **fields,
        }

    def run(self, program: str, payload: dict | None = None, *args: str):
        return subprocess.run(
            [sys.executable, "-c", program, *args],
            input=json.dumps(payload) if payload is not None else None,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": f"{self.bin}:{os.environ['PATH']}",
                "FAKE_UNITS": str(self.state),
            },
        )

    def start(self, **payload):
        body = {"slot": {"netns": "niuu-slot-2"}, "pods": self.pods, **payload}
        return self.run(programs.PODS_START, body)

    def stop(self, data_root: Path, marker: str = "key-slot-2", prefix: str = "niuu-slot-2-"):
        spec = {"prefix": prefix, "data_root": str(data_root), "marker": marker}
        return self.run(programs.PODS_STOP, None, json.dumps(spec))

    def set(self, name: str, value) -> None:
        (self.state / name).write_text(json.dumps(value))

    def calls(self) -> list[tuple[str, list[str]]]:
        log = self.state / "calls.jsonl"
        lines = log.read_text().splitlines() if log.exists() else []
        return [(c["tool"], c["argv"]) for c in map(json.loads, lines)]

    def spec(self, kind: str, name: str = "agent") -> dict:
        return json.loads((Path(self.pods["state"]) / f"{kind}-{name}.json").read_text())


@pytest.fixture
def guest(tmp_path):
    return Guest(tmp_path)


def run_command(guest: Guest, unit: str, kind: str, name: str, *extra: str) -> list[str]:
    """The systemd-run argv the program is expected to build for a container's unit."""
    log = f"{guest.pods['logs']}/{kind}-{name}.log"
    return [
        "--quiet",
        "--unit",
        unit,
        "--slice",
        "niuu_slot_2.slice",
        "--property",
        "KillMode=mixed",
        "--property",
        "TimeoutStopSec=30",
        "--property",
        f"StandardOutput=append:{log}",
        "--property",
        f"StandardError=append:{log}",
        *extra,
        sys.executable,
        "-I",
        f"{guest.pods['state']}/container.py",
        f"{guest.pods['state']}/{kind}-{name}.json",
    ]


def test_a_sidecar_is_a_restarting_unit_in_the_slots_slice_started_once_it_is_active(guest):
    image = guest.image(passwd="root:x:0:0::/root:/bin/sh\n")

    result = guest.start(sidecars=[guest.container(image, "agent")])

    assert result.returncode == 0, result.stderr
    unit = "niuu-slot-2-sidecar-agent"
    assert guest.calls() == [
        ("systemctl", ["is-active", "--quiet", unit]),
        ("systemctl", ["reset-failed", unit]),
        (
            "systemd-run",
            run_command(
                guest,
                unit,
                "sidecar",
                "agent",
                "--property",
                "Restart=always",
                "--property",
                "RestartSec=1",
            ),
        ),
        # Waited on, then checked once more before the start is called good.
        ("systemctl", ["is-active", "--quiet", unit]),
        ("systemctl", ["is-active", "--quiet", unit]),
    ]


def test_the_program_that_runs_a_container_is_written_for_root_alone(guest):
    image = guest.image()

    guest.start(sidecars=[guest.container(image)])

    state = Path(guest.pods["state"])
    assert (state / "container.py").read_text() == "print('container program')\n"
    assert (state / "container.py").stat().st_mode & 0o777 == 0o600
    assert state.stat().st_mode & 0o777 == 0o700
    assert Path(guest.pods["logs"]).stat().st_mode & 0o777 == 0o700
    assert (state / "sidecar-agent.json").stat().st_mode & 0o777 == 0o600


def test_init_containers_run_to_completion_before_any_sidecar_starts(guest):
    image = guest.image()

    result = guest.start(
        init_containers=[guest.container(image, "first"), guest.container(image, "second")],
        sidecars=[guest.container(image, "agent")],
    )

    assert result.returncode == 0, result.stderr
    runs = [(tool, argv) for tool, argv in guest.calls() if tool == "systemd-run"]
    assert [argv[argv.index("--unit") + 1] for _, argv in runs] == [
        "niuu-slot-2-init-first",
        "niuu-slot-2-init-second",
        "niuu-slot-2-sidecar-agent",
    ]
    first = run_command(guest, "niuu-slot-2-init-first", "init", "first")
    # An init container is waited for, and its unit collected when it ends.
    assert runs[0][1] == [
        *first[: first.index(sys.executable)],
        "--wait",
        "--collect",
        *first[first.index(sys.executable) :],
    ]
    assert "--wait" not in runs[2][1] and "Restart=always" in runs[2][1]
    assert guest.calls()[0] == ("systemctl", ["reset-failed", "niuu-slot-2-init-first"])


def test_an_init_container_that_fails_stops_the_start_naming_its_log(guest):
    image = guest.image()
    guest.set("init_status.json", {"niuu-slot-2-init-first": 3})

    result = guest.start(
        init_containers=[guest.container(image, "first"), guest.container(image, "second")],
        sidecars=[guest.container(image, "agent")],
    )

    assert result.returncode != 0
    assert "Init container first exited with status 3" in result.stderr
    assert f"see {guest.pods['logs']}/init-first.log on the host" in result.stderr
    # Nothing after the failed init runs.
    assert len([c for c in guest.calls() if c[0] == "systemd-run"]) == 1


def test_a_sidecar_that_is_already_running_is_left_alone(guest):
    image = guest.image()
    guest.set("active.json", ["niuu-slot-2-sidecar-agent"])

    result = guest.start(sidecars=[guest.container(image, "agent")])

    assert result.returncode == 0, result.stderr
    assert guest.calls() == [("systemctl", ["is-active", "--quiet", "niuu-slot-2-sidecar-agent"])]


def test_a_sidecar_that_never_becomes_active_fails_the_start_naming_its_log(guest):
    image = guest.image()
    guest.set("never_active.json", ["niuu-slot-2-sidecar-agent"])

    result = guest.start(sidecars=[guest.container(image, "agent")])

    assert result.returncode != 0
    assert "Sidecar agent did not start" in result.stderr
    assert f"see {guest.pods['logs']}/sidecar-agent.log on the host" in result.stderr


@pytest.mark.parametrize(
    ("image_user", "container_user", "expected"),
    [
        ("", None, (0, 0)),
        ("1000", None, (1000, 1000)),
        ("app", None, (1001, 1002)),
        ("app:wheel", None, (1001, 10)),
        ("1000:20", None, (1000, 20)),
        ("1000:staff", None, (1000, 50)),
        ("app:20", None, (1001, 20)),
        ("app", "0", (0, 0)),
        ("app", "1000:staff", (1000, 50)),
        ("", "app", (1001, 1002)),
    ],
)
def test_the_containers_user_is_its_own_else_the_images_else_root(
    guest, image_user, container_user, expected
):
    image = guest.image(
        user=image_user,
        passwd="root:x:0:0::/root:/bin/sh\napp:x:1001:1002::/home/app:/bin/sh\n",
        group="wheel:x:10:\nstaff:x:50:\n",
    )
    fields = {} if container_user is None else {"user": container_user}

    result = guest.start(sidecars=[guest.container(image, **fields)])

    assert result.returncode == 0, result.stderr
    spec = guest.spec("sidecar")
    assert (spec["uid"], spec["gid"]) == expected


@pytest.mark.parametrize(
    ("user", "passwd", "group", "message"),
    [
        ("nosuch", "root:x:0:0::/root:/bin/sh\n", "", "The image has no passwd entry nosuch"),
        ("nosuch", None, "", "The image has no passwd entry nosuch"),
        ("0:nosuch", "", "root:x:0:\n", "The image has no group entry nosuch"),
        ("0:nosuch", "", None, "The image has no group entry nosuch"),
    ],
)
def test_an_account_the_image_does_not_have_is_refused_before_anything_starts(
    guest, user, passwd, group, message
):
    image = guest.image(user=user, passwd=passwd or "", group=group or "")

    result = guest.start(sidecars=[guest.container(image)])

    assert result.returncode != 0 and message in result.stderr
    assert not [c for c in guest.calls() if c[0] == "systemd-run"]


@pytest.mark.parametrize(
    ("command", "args", "entrypoint", "cmd", "expected"),
    [
        ([], [], ["/ep"], ["c"], ["/ep", "c"]),
        ([], ["a"], ["/ep"], ["c"], ["/ep", "a"]),
        (["run"], [], ["/ep"], ["c"], ["run"]),
        (["run"], ["x", "y"], ["/ep"], ["c"], ["run", "x", "y"]),
        ([], [], [], ["c", "d"], ["c", "d"]),
        ([], ["a"], [], ["c"], ["a"]),
        ([], [], ["/ep"], [], ["/ep"]),
    ],
)
def test_command_and_args_follow_kubernetes_over_the_images_entrypoint_and_cmd(
    guest, command, args, entrypoint, cmd, expected
):
    image = guest.image(entrypoint=entrypoint, cmd=cmd)

    result = guest.start(sidecars=[guest.container(image, command=command, args=args)])

    assert result.returncode == 0, result.stderr
    assert guest.spec("sidecar")["argv"] == expected


def test_a_container_and_an_image_that_name_no_command_are_refused(guest):
    image = guest.image(entrypoint=[])

    result = guest.start(sidecars=[guest.container(image, "silent")])

    assert result.returncode != 0
    assert "Container silent names no command and its image none" in result.stderr


def test_the_containers_environment_is_the_images_overridden_by_its_own(guest):
    image = guest.image(env=["A=1", "PATH=/img/bin", "FLAGS=x=y"], entrypoint=["/ep"], user="5")

    guest.start(sidecars=[guest.container(image, environment={"A": "2", "B": "3"})])

    spec = guest.spec("sidecar")
    assert spec["environment"] == {
        "A": "2",
        "B": "3",
        "PATH": "/img/bin",
        "FLAGS": "x=y",
        "HOME": "/",
    }


def test_a_container_gets_a_default_path_and_a_home_that_fits_its_user(guest):
    image = guest.image(entrypoint=["/ep"])
    other = guest.image("other", env=["HOME=/home/img"], entrypoint=["/ep"], user="7")

    guest.start(sidecars=[guest.container(image, "root"), guest.container(other, "user")])

    root, user = guest.spec("sidecar", "root"), guest.spec("sidecar", "user")
    assert root["environment"] == {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": "/root",
    }
    assert user["environment"]["HOME"] == "/home/img"


def test_a_containers_spec_carries_its_root_namespace_mounts_and_working_directory(guest):
    image = guest.image(entrypoint=["/ep"], workdir="/srv")
    mounts = [
        {"source": "/etc/niuu/slots/2/guest-agent", "target": "/etc/niuu/guest-agent"},
        {"source": "/run/niuu/slots/2/secrets", "target": "/run/secrets", "readonly": False},
    ]

    guest.start(sidecars=[guest.container(image, mounts=mounts)])

    spec = guest.spec("sidecar")
    assert spec["netns"] == "niuu-slot-2"
    assert spec["rootfs"] == guest.pods["images"][image]["rootfs"]
    assert spec["work"] == f"{guest.pods['work']}/sidecar-agent"
    assert spec["resolv_conf"] == "/run/systemd/resolve/resolv.conf"
    assert spec["workdir"] == "/srv"
    # A mount is read-only unless the pod says otherwise.
    assert spec["mounts"] == [
        {
            "source": "/etc/niuu/slots/2/guest-agent",
            "target": "/etc/niuu/guest-agent",
            "readonly": True,
        },
        {"source": "/run/niuu/slots/2/secrets", "target": "/run/secrets", "readonly": False},
    ]


def test_the_launchs_directories_are_made_with_their_modes(guest, tmp_path):
    image = guest.image(entrypoint=["/ep"])
    directories = [
        {"path": str(tmp_path / "a/b"), "mode": "0750"},
        {"path": str(tmp_path / "c"), "mode": "0700"},
    ]

    result = guest.start(directories=directories, sidecars=[guest.container(image)])

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "a/b").stat().st_mode & 0o777 == 0o750
    assert (tmp_path / "c").stat().st_mode & 0o777 == 0o700


def test_the_credential_address_is_added_to_the_slots_loopback_once(guest):
    image = guest.image(entrypoint=["/ep"])

    guest.start(metadata_address="169.254.169.254", sidecars=[guest.container(image)])

    ip = [argv for tool, argv in guest.calls() if tool == "ip"]
    prefix = ["netns", "exec", "niuu-slot-2", "ip"]
    assert ip == [
        [*prefix, "-o", "addr", "show", "dev", "lo"],
        [*prefix, "addr", "add", "169.254.169.254/32", "dev", "lo"],
    ]


def test_an_address_the_loopback_already_has_is_not_added_again(guest):
    image = guest.image(entrypoint=["/ep"])
    guest.set("addresses.json", "1: lo    inet 169.254.169.254/32 scope global lo\n")

    result = guest.start(metadata_address="169.254.169.254", sidecars=[guest.container(image)])

    assert result.returncode == 0, result.stderr
    assert [argv[-1] for tool, argv in guest.calls() if tool == "ip"] == ["lo"]


def test_a_launch_without_a_credential_address_touches_no_namespace(guest):
    image = guest.image(entrypoint=["/ep"])

    guest.start(sidecars=[guest.container(image)])

    assert not [call for call in guest.calls() if call[0] == "ip"]


# -- stopping them -----------------------------------------------------------------------------


def test_stopping_a_prepared_slot_stops_every_unit_of_the_slot_and_resets_them(guest, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / ".allocation").write_text("key-slot-2")
    guest.set("active.json", ["niuu-slot-2-sidecar-a", "niuu-slot-2-sidecar-b", "niuu-slot-3-x"])

    result = guest.stop(data)

    assert result.returncode == 0, result.stderr
    assert guest.calls() == [
        (
            "systemctl",
            ["list-units", "--all", "--plain", "--no-legend", "--type=service", "niuu-slot-2-*"],
        ),
        ("systemctl", ["stop", "niuu-slot-2-sidecar-a"]),
        ("systemctl", ["reset-failed", "niuu-slot-2-sidecar-a"]),
        ("systemctl", ["stop", "niuu-slot-2-sidecar-b"]),
        ("systemctl", ["reset-failed", "niuu-slot-2-sidecar-b"]),
    ]
    # Another slot's units are not this slot's to stop.
    assert json.loads((guest.state / "active.json").read_text()) == ["niuu-slot-3-x"]


def test_a_slot_cancelled_before_its_storage_was_prepared_stops_its_units_and_exits_42(
    guest, tmp_path
):
    data = tmp_path / "data"
    data.mkdir()
    guest.set("active.json", ["niuu-slot-2-sidecar-a"])

    result = guest.stop(data)

    assert result.returncode == 42, result.stderr
    assert json.loads((guest.state / "active.json").read_text()) == []
    assert ("systemctl", ["reset-failed", "niuu-slot-2-sidecar-a"]) in guest.calls()


def test_a_slot_whose_storage_belongs_to_another_allocation_is_not_touched(guest, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / ".allocation").write_text("someone-elses")
    guest.set("active.json", ["niuu-slot-2-sidecar-a"])

    result = guest.stop(data)

    assert result.returncode != 0 and "Workspace allocation mismatch" in result.stderr
    assert not [call for call in guest.calls() if call[1][0] == "stop"]


def test_a_unit_that_will_not_stop_fails_the_stop_loudly(guest, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / ".allocation").write_text("key-slot-2")
    guest.set("active.json", ["niuu-slot-2-sidecar-a"])
    guest.set("stubborn.json", ["niuu-slot-2-sidecar-a"])

    result = guest.stop(data)

    assert result.returncode != 0
    assert ("systemctl", ["reset-failed", "niuu-slot-2-sidecar-a"]) not in guest.calls()


def test_a_slot_with_no_units_stops_cleanly(guest, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / ".allocation").write_text("key-slot-2")

    assert guest.stop(data).returncode == 0


# -- one container's namespace, with its system calls recorded ---------------------------------

# The container program needs root and Linux namespaces. Run under this prelude it makes the
# same calls in the same order, each recorded instead of made: forks answer from FORKS,
# waitpid from WAITS ("interrupt", "term" or [pid, status]), and PLANT symlinks stand in for
# what the image's own root holds once the overlay is "mounted".
HARNESS = r"""
import atexit, json, os, signal, subprocess
calls = []
atexit.register(lambda: open(os.environ['CALLS'], 'w').write(json.dumps(calls, default=str)))
def record(*call):
    calls.append(list(call))
real_open = os.open
def fake_open(path, flags, *rest):
    if str(path).startswith('/run/netns/'):
        record('open', str(path))
        return real_open('/dev/null', os.O_RDONLY)
    return real_open(path, flags, *rest)
os.open = fake_open
os.CLONE_NEWNET, os.CLONE_NEWNS, os.CLONE_NEWPID = 'NEWNET', 'NEWNS', 'NEWPID'
os.setns = lambda fd, flags: record('setns', flags)
os.unshare = lambda flags: record('unshare', flags)
def fake_run(argv, check=False, **kwargs):
    record(*argv)
    assert check is True and argv[0] == 'mount'
    if argv[:4] == ['mount', '-t', 'overlay', 'overlay']:
        for path, target in json.loads(os.environ['PLANT']):
            os.symlink(target, argv[-1] + path)
subprocess.run = fake_run
os.chroot = lambda path: record('chroot', path)
os.chdir = lambda path: record('chdir', path)
os.setgroups = lambda groups: record('setgroups', groups)
os.setgid = lambda gid: record('setgid', gid)
os.setuid = lambda uid: record('setuid', uid)
os.kill = lambda pid, signum: record('kill', pid, int(signum))
def fake_exec(file, argv, env):
    record('exec', file, argv, env)
    raise SystemExit(0)
os.execvpe = fake_exec
forks = iter(json.loads(os.environ['FORKS']))
os.fork = lambda: next(forks)
waits = iter(json.loads(os.environ['WAITS']))
def fake_waitpid(pid, flags):
    record('waitpid', pid)
    for item in waits:
        if item == 'interrupt':
            raise InterruptedError()
        if item == 'term':
            signal.raise_signal(signal.SIGTERM)
            continue
        return tuple(item)
os.waitpid = fake_waitpid
def fake_exit(code):
    record('_exit', code)
    raise SystemExit(code)
os._exit = fake_exit
"""


def run_container(tmp_path: Path, forks, waits=(), *, plant=(), **overrides):
    """Run CONTAINER under the harness; returns (process, the recorded calls, the spec)."""
    source, secrets = tmp_path / "secrets", tmp_path / "token"
    source.mkdir()
    secrets.write_text("t")
    spec = {
        "netns": "niuu-slot-2",
        "rootfs": "/var/lib/niuu/host-images/abc/rootfs",
        "work": str(tmp_path / "work/sidecar-agent"),
        "resolv_conf": "/run/systemd/resolve/resolv.conf",
        "mounts": [
            {"source": str(source), "target": "/run/secrets", "readonly": True},
            {"source": str(secrets), "target": "/etc/niuu/token", "readonly": False},
        ],
        "workdir": "/srv",
        "argv": ["/agent", "serve"],
        "environment": {"A": "1"},
        "uid": 1000,
        "gid": 1002,
        **overrides,
    }
    (tmp_path / "spec.json").write_text(json.dumps(spec))
    result = subprocess.run(
        [sys.executable, "-c", HARNESS + programs.CONTAINER, str(tmp_path / "spec.json")],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "CALLS": str(tmp_path / "calls.json"),
            "FORKS": json.dumps(list(forks)),
            "WAITS": json.dumps(list(waits)),
            "PLANT": json.dumps(list(plant)),
        },
    )
    calls = json.loads((tmp_path / "calls.json").read_text())
    return result, calls, spec


def mounts(calls: list) -> list[list[str]]:
    return [call for call in calls if call[0] == "mount"]


def test_the_container_joins_the_slots_namespace_then_gets_mount_and_pid_namespaces(tmp_path):
    result, calls, spec = run_container(tmp_path, [0, 0])

    assert result.returncode == 0, result.stderr
    names = [call[0] for call in calls]
    assert calls[:4] == [
        ["open", "/run/netns/niuu-slot-2"],
        ["setns", "NEWNET"],
        ["unshare", "NEWNS"],
        ["mount", "--make-rprivate", "/"],
    ]
    # The pod's mounts are in place before the PID namespace opens, and /proc after it.
    assert names.index("unshare", 3) > max(
        i for i, call in enumerate(calls) if call[:2] == ["mount", "--bind"]
    )
    assert calls[names.index("unshare", 3)] == ["unshare", "NEWPID"]
    after = calls[names.index("unshare", 3) :]
    root = f"{spec['work']}/root"
    assert ["mount", "-t", "proc", "proc", f"{root}/proc"] in after
    assert ["mount", "-t", "sysfs", "-o", "ro,nosuid,nodev,noexec", "sysfs", f"{root}/sys"] in after


def test_the_writable_layer_is_a_fresh_tmpfs_under_an_overlay_of_the_images_root(tmp_path):
    result, calls, spec = run_container(tmp_path, [0, 0])

    work = spec["work"]
    assert result.returncode == 0, result.stderr
    assert (
        oct(Path(work).parent.stat().st_mode & 0o777) and Path(work).stat().st_mode & 0o777 == 0o700
    )
    assert mounts(calls)[1] == ["mount", "-t", "tmpfs", "-o", "mode=0700", "tmpfs", work]
    assert mounts(calls)[2] == [
        "mount",
        "-t",
        "overlay",
        "overlay",
        "-o",
        f"lowerdir={spec['rootfs']},upperdir={work}/upper,workdir={work}/work",
        f"{work}/root",
    ]
    for name in ("upper", "work", "root"):
        assert (Path(work) / name).is_dir()


def test_a_container_gets_its_own_dev_tmp_resolver_and_hosts(tmp_path):
    result, calls, spec = run_container(tmp_path, [0, 0])

    root, work = f"{spec['work']}/root", spec["work"]
    assert result.returncode == 0, result.stderr
    got = mounts(calls)
    assert ["mount", "-t", "tmpfs", "-o", "mode=0755,nosuid", "tmpfs", f"{root}/dev"] in got
    for device in ("null", "zero", "full", "random", "urandom", "tty"):
        assert ["mount", "--bind", f"/dev/{device}", f"{root}/dev/{device}"] in got
    shm = ["mount", "-t", "tmpfs", "-o", "mode=1777,nosuid,nodev", "tmpfs", f"{root}/dev/shm"]
    assert shm in got
    assert [*shm[:-1], f"{root}/tmp"] in got
    # The machine's upstream resolvers, read-only, and a hosts file of the container's own.
    assert ["mount", "-o", "ro", "--bind", spec["resolv_conf"], f"{root}/etc/resolv.conf"] in got
    assert ["mount", "-o", "ro", "--bind", f"{work}/hosts", f"{root}/etc/hosts"] in got
    assert (Path(work) / "hosts").read_text() == "127.0.0.1 localhost\n::1 localhost\n"
    # /dev is mounted before anything is bound into it.
    assert got.index(["mount", "-t", "tmpfs", "-o", "mode=0755,nosuid", "tmpfs", f"{root}/dev"]) < (
        got.index(["mount", "--bind", "/dev/null", f"{root}/dev/null"])
    )


def test_the_pods_mounts_are_bound_at_their_targets_and_only_read_only_ones_remounted(tmp_path):
    result, calls, spec = run_container(tmp_path, [0, 0])

    root = f"{spec['work']}/root"
    assert result.returncode == 0, result.stderr
    got = mounts(calls)
    secrets, token = spec["mounts"]
    assert got.index(["mount", "--bind", secrets["source"], f"{root}/run/secrets"]) + 1 == (
        got.index(["mount", "-o", "remount,bind,ro", f"{root}/run/secrets"])
    )
    assert ["mount", "--bind", token["source"], f"{root}/etc/niuu/token"] in got
    assert ["mount", "-o", "remount,bind,ro", f"{root}/etc/niuu/token"] not in got
    # A directory source gets a directory mount point, a file a file.
    assert Path(f"{root}/run/secrets").is_dir() and Path(f"{root}/etc/niuu/token").is_file()


def test_the_command_runs_chrooted_in_its_workdir_as_its_user_with_exactly_its_environment(
    tmp_path,
):
    result, calls, spec = run_container(tmp_path, [0, 0])

    assert result.returncode == 0, result.stderr
    assert calls[-6:-1] == [
        ["chroot", f"{spec['work']}/root"],
        ["chdir", "/srv"],
        ["setgroups", [1002]],
        ["setgid", 1002],
        ["setuid", 1000],
    ]
    assert calls[-1] == ["exec", "/agent", ["/agent", "serve"], {"A": "1"}]


def test_a_container_without_a_working_directory_starts_at_the_root(tmp_path):
    result, calls, _ = run_container(tmp_path, [0, 0], workdir="")

    assert result.returncode == 0, result.stderr
    assert ["chdir", "/"] in calls


def test_a_mount_target_that_is_a_link_in_the_image_is_refused(tmp_path):
    mount = {"source": str(tmp_path), "target": "/evil", "readonly": True}

    result, calls, _ = run_container(tmp_path, [0, 0], plant=[["/evil", "/etc"]], mounts=[mount])

    assert result.returncode != 0
    assert "Mount target /evil is a link in the image" in result.stderr
    assert not [call for call in calls if call[0] == "exec"]


def test_the_outer_process_passes_stop_signals_on_and_exits_with_the_containers_status(tmp_path):
    result, calls, _ = run_container(tmp_path, [321], ["interrupt", "term", [321, 7 << 8]])

    assert result.returncode == 7, result.stderr
    assert ["kill", 321, 15] in calls
    assert calls[-1] == ["_exit", 7]
    # It never goes on to mount a proc or run the command.
    assert not [call for call in calls if call[:3] == ["mount", "-t", "proc"] or call[0] == "exec"]


def test_a_container_killed_by_a_signal_exits_with_that_signals_number_in_the_low_byte(tmp_path):
    result, calls, _ = run_container(tmp_path, [321], [[321, 9]])

    # waitstatus_to_exitcode reports a signal death as -signal; & 0xFF keeps one byte.
    assert calls[-1] == ["_exit", (-9) & 0xFF] and result.returncode == (-9) & 0xFF


def test_pid_one_of_the_container_reaps_orphans_and_ends_with_the_commands_status(tmp_path):
    result, calls, _ = run_container(
        tmp_path, [0, 654], ["interrupt", "term", [999, 0], [654, 3 << 8]]
    )

    assert result.returncode == 3, result.stderr
    assert ["kill", 654, 15] in calls
    waited = [call for call in calls if call[0] == "waitpid"]
    assert all(call == ["waitpid", -1] for call in waited) and len(waited) >= 3
    assert calls[-1] == ["_exit", 3]
    # PID 1 never runs the command itself.
    assert not [call for call in calls if call[0] in ("chroot", "exec")]
