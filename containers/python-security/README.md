# CPython 3.14.10 security backports

These build inputs backport published fixes without changing the Python
interpreter, ABI, reported version, or installed application dependencies.
Remove a patch after a stable upstream runtime includes its fix; a different
base version or module checksum deliberately fails the build until reviewed.

| CVE | Upstream commit | Runtime file | Coverage |
| --- | --- | --- | --- |
| CVE-2025-15367 | [b234a2b](https://github.com/python/cpython/commit/b234a2b67539f787e191d2ef19a7cbdce32874e7) | `poplib.py` | Fixed: the unmodified upstream patch |
| CVE-2026-103995 | [2eb2af8](https://github.com/python/cpython/commit/2eb2af805c156ca52b316bb4ced5e0ab3971d0ae), [9f52b44](https://github.com/python/cpython/commit/9f52b443c002cccfb54bda0706a9276b6b4a109d) | `http/client.py` | Fixed: both unmodified upstream patches |
| CVE-2026-116566 | [131a3a6](https://github.com/python/cpython/commit/131a3a66c5f400b588f9fd2732a5ed4e89623ee3), adapted | `socket.py` | Mitigated, not fixed: see below |

CPython 3.14.10 (CVE-2026-115557) carries `poplib.py` and `tempfile.py`
unchanged from 3.14.9, so the backport and the hashes below did not move.
CPython 3.14.9 ships the fixes for CVE-2026-12345 (`tempfile.py`) and
CVE-2026-87910 (`tarfile.py`, previously backported here), and 3.14.8 shipped
CVE-2026-17084 (`stringprep.py`), CVE-2026-15806 (`urllib/request.py`),
CVE-2026-15310 (`zipfile/__init__.py`) and CVE-2026-19672 (`tarfile.py`), so
those backports were removed. `verify.py` keeps their exploit regressions as
guards on the runtime.

Grype's data still credits CVE-2026-12345 only to 3.15.0rc3, although CPython
3.14.9 fixes it (gh-157579; the CVE record lists `< 3.14.9`). `manifest.json`
names it under `upstream_fixes` with the exact upstream `tempfile.py` hash:
`verify.py` checks that hash and the fix's regression inside the image, and
only then does `vex.py` issue a `fixed` statement for it, worded as an upstream
fix rather than a backport. Drop the entry once the scanner data catches up.

The POP3 fix is deliberately taken from the newer upstream line: it rejects
C0/DEL characters in commands, including credentials. Older Python branches
have not adopted that behavior because it can reject previously accepted
inputs. Niuu has no direct POP3 integration; bundled Python now enforces that
security boundary for callers too.

## HTTP framing (CVE-2026-103995)

gh-150751 is on CPython main only, in two commits that must be applied in this
order (the second edits the line the first adds). Both apply to 3.14.10 with no
fuzz and no adaptation. A `Content-Length` or chunk size outside the RFC 9112
grammar (`+5`, `5_0`, `0x5`, `-5`) no longer frames a response body, so Niuu
cannot frame a response differently from a strict peer.

## socket.getaddrinfo null bytes (CVE-2026-116566)

gh-160898 fixes this in `Modules/socketmodule.c`: `_socket.getaddrinfo()`
truncated the host and port at an embedded null byte, so `127.0.0.1\0evil`
resolved as `127.0.0.1`. The module-replacement mechanism ships pure-Python
files, so the fix cannot be applied as published. Two ways to reach it:

- **Rebuild `_socket` (rejected).** In the official `python` image `_socket` is
  a shared module under `lib-dynload` (not checked inside an image). In the
  uv-managed Python that OpenShell uses (python-build-standalone) it is built
  into the interpreter: `_socket` is in `sys.builtin_module_names` on the macOS
  python-build-standalone 3.13.11, and the Linux 3.14.10 build was assumed to
  match, not inspected. Nothing could be replaced there short of the
  interpreter binary. Elsewhere it would still need a compiler and a build for
  each target architecture (the `python-security` stage runs on
  `$BUILDPLATFORM` and exports architecture-independent files) and a binary
  whose hash the manifest cannot pin reproducibly.
- **Guard in `socket.py` (adopted).** `socket.getaddrinfo()` raises the same
  `ValueError` messages as upstream (`getaddrinfo() host must not contain null
  characters`, and the same for `port`) before calling `_socket`. It checks
  `str` and `bytes` hosts and ports with the unbound `str`/`bytes` methods, so
  a subclass cannot hide a null byte. This covers everything that resolves
  through the `socket` module: asyncio, `socket.create_connection`, `urllib`,
  `http.client`.

What the guard does not cover: a direct call to the private
`_socket.getaddrinfo()`, and code that resolves names without the `socket`
module. The other resolver entry points (`gethostbyname`, `gethostbyname_ex`,
`gethostbyaddr`, `getservbyname`, `getnameinfo`, `inet_aton`, and
`connect`/`bind`/`sendto` on a host tuple) already reject a null byte;
`verify.py` asserts that inside the image. The Niuu source tree has no direct
`_socket` call; installed third-party packages were not audited for one.

Because the compiled code is still vulnerable, no `fixed` statement is issued
for this CVE. `vex.py` issues `not_affected` with the justification
`inline_mitigations_already_exist` and an impact statement that names the
uncovered paths (`manifest.json`, `mitigations`). A scan therefore shows this CVE
as ignored by VEX, with the reason and the limits recorded, not as fixed.

`131a3a66c5f400b588f9fd2732a5ed4e89623ee3-python.patch` is not the upstream patch. It keeps the upstream
`Doc`, `Lib/test/test_socket.py` and `Misc/NEWS.d` sections byte for byte, drops
`Modules/socketmodule.c`, and adds the `Lib/socket.py` guard. The upstream patch
is kept beside it. `manifest.json` pins both hashes, and `build.py` fails
unless the adapted patch differs from upstream only by the declared dropped and
added files, then runs the upstream `test_getaddrinfo_null_bytes` against the
guard. Upstream backported the C fix to the 3.14 branch (python/cpython#160926), so
remove this adaptation, its manifest entries and the `socket.py` row when a stable
3.14 release ships it. The HTTP patches stay until a stable release includes
gh-150751, which is not on the 3.14 branch.

## Build and install

`build.py` verifies the upstream 3.14.10 source archive and every upstream
patch (and the adapted patch) against `manifest.json`, applies them with no
fuzz, runs the CPython tests listed under `tests` in the manifest, and exports
only the patched modules. The build tools and source tree do not enter the final
images.

`install.py` checks every original and replacement module before writing any
file, copies the patched module into the interpreter's actual stdlib directory,
and removes their obsolete bytecode caches. This covers both `/usr/local`
Python and OpenShell's managed Python. `verify.py` checks installed hashes and
runs exploit regressions for the backported and mitigated CVEs and the ones fixed upstream.

## Scanner evidence

Version-based scanners still recognize the real Python 3.14.10 version.
The container scan therefore resolves an immutable image digest and runs the
same verifier inside that image with networking disabled and a read-only
filesystem. A failed hash or regression check stops scanning before any fixed
statement can be issued.

Only after verification, `vex.py` creates OpenVEX `fixed` statements for the
backported CVEs, and the `not_affected` mitigation statement above, for the exact
Python package/runtime path in that same image digest.
It rejects unrelated image scans, module hashes, versions and interpreter
locations. Syft catalogs each image once (`scan.sh`); both Grype passes match
that SBOM, with Syft and Grype pinned by checksum in `install-scanners.sh`.
Every scan retains the Syft SBOM, the raw Grype JSON, runtime hash/test evidence,
VEX document and filtered Grype JSON as CI artifacts for 30 days. The filtered
JSON retains the original findings in `ignoredMatches` with the VEX reason;
SARIF reports the remaining actionable findings. No global ignore rule or
version spoofing is used.

The upstream patches and derived modules are covered by `LICENSE.cpython`.

## Runtime boundary

These backports cover Python in the four shipped images. The SSH VM restore
script uses Python installed on the remote VM; that separate operating-system
runtime is not modified by these image builds and must receive its own vendor
security updates. No remote host was patched as part of this remediation.
