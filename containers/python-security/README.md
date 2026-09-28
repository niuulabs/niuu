# CPython 3.14.7 security backports

These build inputs backport six published fixes without changing the Python
interpreter, ABI, reported version, or installed application dependencies.
Remove each patch after a stable upstream runtime includes its fix; a different
base version or module checksum deliberately fails the build until reviewed.

| CVE | Upstream commit | Runtime file |
| --- | --- | --- |
| CVE-2025-15367 | [b234a2b](https://github.com/python/cpython/commit/b234a2b67539f787e191d2ef19a7cbdce32874e7) | `poplib.py` |
| CVE-2026-17084 | [1e54caa](https://github.com/python/cpython/commit/1e54caa096678a38afcabecabb1ff72400dd6bae) | `stringprep.py` |
| CVE-2026-15806 | [a0d023f](https://github.com/python/cpython/commit/a0d023fbd23773e24b35d8368789470e22cda5d8) | `urllib/request.py` |
| CVE-2026-15310 | [31980e8](https://github.com/python/cpython/commit/31980e84b9a708424a0a1dfecde3fc991e313f89) and [9d16799](https://github.com/python/cpython/commit/9d167992b59cf5e23c66b9ed742b13f5925f7d70) | `zipfile/__init__.py` |
| CVE-2026-19672 | [16dea1e](https://github.com/python/cpython/commit/16dea1e887ec7dfbed735beedd476b21dcc91a79) | `tarfile.py` |
| CVE-2026-87910 | [fb2f0bb](https://github.com/python/cpython/commit/fb2f0bbc3b35264f09cc2cb2934b7987527a6bc2) and [d9565e5](https://github.com/python/cpython/commit/d9565e54b1fc6d63c5be9afd58114499128fa57b) (test correction) | `tarfile.py` |

The ZIP follow-up preserves upstream compatibility for custom decompressors.
The POP3 fix is deliberately taken from the newer upstream line: it rejects
C0/DEL characters in commands, including credentials. Older Python branches
have not adopted that behavior because it can reject previously accepted
inputs. Niuu has no direct POP3 integration; bundled Python now enforces that
security boundary for callers too.

`build.py` verifies the upstream 3.14.7 source archive and all eight complete,
unmodified upstream patches against `manifest.json`, applies them with no
fuzz, runs CPython's six affected test suites, and exports only five modules.
The build tools and source tree do not enter the final images.

`install.py` checks every original and replacement module before writing any
file, copies those five modules into the interpreter's actual stdlib directory,
and removes their obsolete bytecode caches. This covers both `/usr/local`
Python and OpenShell's managed Python. `verify.py` checks installed hashes and
runs exploit regressions for all six CVEs, including ZIP content preservation.

## Scanner evidence

Version-based scanners still recognize the real Python 3.14.7 version.
The container scan therefore resolves an immutable image digest and runs the
same verifier inside that image with networking disabled and a read-only
filesystem. A failed hash or regression check stops scanning before any fixed
statement can be issued.

Only after verification, `vex.py` creates OpenVEX `fixed` statements for these
six CVEs and the exact Python package/runtime path in that same image digest.
It rejects unrelated image scans, module hashes, versions and interpreter
locations. Every scan retains the raw Grype JSON, runtime hash/test evidence,
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
