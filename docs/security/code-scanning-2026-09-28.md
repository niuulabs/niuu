# Code scanning remediation — 2026-09-28

The authenticated GitHub inventory contained 23 open alerts, all on `dev`.
No alerts were dismissed or scanning rules disabled.

## Implemented fixes

| Alerts | Cause | Change |
| --- | --- | --- |
| 4281 | Mímir returns identity adapter exception text | Return a stable 401 authentication error without internal exception details. |
| 4283 | Skuld returns room-role adapter exception text | Return a stable 503 response; retain diagnostic information in server logs. |
| 4259, 4260 | Copilot 1.0.83 bundles vulnerable adm-zip 0.6.0 | Update locked Copilot packages to 1.0.88; the Linux glibc and musl packages no longer contain the affected foundry dependency tree. |
| 3221 | glab 1.117.0 embeds goldmark 1.7.13 | Upgrade to glab 1.119.0, whose release binary embeds fixed goldmark 1.7.17. |
| 3220, 4296 | Compose 5.5.1 embeds containerd 2.3.4 | Build the checksum-pinned Compose release with containerd 2.3.6 using a digest-pinned Go toolchain. Keep the compiler and sources out of the runtime image. |

GitHub must rebuild and scan the merged images before the container alerts can
be confirmed closed. The local environment has no running Docker daemon.

## Python stdlib backports

The following 16 version-based alerts represent four CVEs across four images.
A stable release upgrade could not resolve them, so the follow-up remediation
backports the upstream fixes into Python 3.14.7 without changing its ABI or
reported version.

| CVE | niuu | agent | devrunner | openshell |
| --- | --- | --- | --- | --- |
| CVE-2026-17084 | 2486 | 2542 | 2582 | 2650 |
| CVE-2026-15806 | 2487 | 2543 | 2595 | 2651 |
| CVE-2025-15367 | 2488 | 2544 | 2602 | 2652 |
| CVE-2026-15310 | 2489 | 2545 | 2629 | 2653 |

The implementation in `containers/python-security/` checks the original and
patched module hashes, applies eight exact upstream patches (including the ZIP
compatibility follow-up), runs the six affected CPython test suites, and
installs only five patched stdlib modules in niuu, agent, devrunner and openshell.
The patch inventory and behavioral compatibility details are documented there.

Local proof: all patches apply cleanly to the checksum-pinned 3.14.7 source;
1,651 upstream tests pass (29 skipped). The runtime exploit regressions fail on
the unpatched interpreter and pass after installation. Image builds run the
same tests before publication.

Grype still sees the real 3.14.7 version. Scan jobs verify hashes and exploit
regressions inside the exact image digest, without networking, before issuing
OpenVEX `fixed` statements limited to the six verified CVEs and that verified Python
component. Raw and filtered scan reports, verification evidence and VEX are
retained together. No finding is waived based merely on its version or assumed
lack of exposure; no failed verification can produce a fixed statement.

Upstream records:

- [CVE-2026-17084: StringPrep Unicode attributes](https://github.com/advisories/GHSA-w246-x8qv-r8f5)
- [CVE-2026-15806: HTTPPasswordMgr credential scope](https://github.com/advisories/GHSA-2v69-2w5x-455j)
- [CVE-2025-15367: POP3 command injection](https://github.com/advisories/GHSA-g82h-mgfp-jx8g)
- [CVE-2026-15310: unbounded ZIP decompression](https://github.com/advisories/GHSA-xj79-6hh5-9w6q)

Two further medium-severity tarfile flaws were discovered in unfiltered Grype
output: CVE-2026-19672 (empty directories outside an extraction destination,
CVSS 6.3) and CVE-2026-87910 (link fallback ignores a filter rejection, CVSS 5.7).
The same backport bundle fixes both, with their upstream tarfile regression
suite and installed-runtime regression checks. Raw scanner output is now retained
without `--only-fixed`; actionable SARIF retains that pre-existing policy.
The OpenVEX document records all six verified fixes. These image changes do not
update Python on external SSH VM hosts.
