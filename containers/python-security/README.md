# CPython 3.14.9 security backports

These build inputs backport one published fix without changing the Python
interpreter, ABI, reported version, or installed application dependencies.
Remove the patch after a stable upstream runtime includes its fix; a different
base version or module checksum deliberately fails the build until reviewed.

| CVE | Upstream commit | Runtime file |
| --- | --- | --- |
| CVE-2025-15367 | [b234a2b](https://github.com/python/cpython/commit/b234a2b67539f787e191d2ef19a7cbdce32874e7) | `poplib.py` |

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

`build.py` verifies the upstream 3.14.9 source archive and the complete,
unmodified upstream patch against `manifest.json`, applies it with no fuzz,
runs CPython's affected test suite, and exports only the patched module.
The build tools and source tree do not enter the final images.

`install.py` checks every original and replacement module before writing any
file, copies the patched module into the interpreter's actual stdlib directory,
and removes their obsolete bytecode caches. This covers both `/usr/local`
Python and OpenShell's managed Python. `verify.py` checks installed hashes and
runs exploit regressions for the backported CVE and the ones fixed upstream.

## Scanner evidence

Version-based scanners still recognize the real Python 3.14.9 version.
The container scan therefore resolves an immutable image digest and runs the
same verifier inside that image with networking disabled and a read-only
filesystem. A failed hash or regression check stops scanning before any fixed
statement can be issued.

Only after verification, `vex.py` creates OpenVEX `fixed` statements for the
backported CVEs and the exact Python package/runtime path in that same image digest.
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
