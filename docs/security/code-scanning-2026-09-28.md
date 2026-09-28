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

## Upstream Python blockers

These 16 alerts remain unresolved; no prerelease runtime migration or local
CPython fork was introduced to hide them.

| CVE | niuu | agent | devrunner | openshell |
| --- | --- | --- | --- | --- |
| CVE-2026-17084 | 2486 | 2542 | 2582 | 2650 |
| CVE-2026-15806 | 2487 | 2543 | 2595 | 2651 |
| CVE-2025-15367 | 2488 | 2544 | 2602 | 2652 |
| CVE-2026-15310 | 2489 | 2545 | 2629 | 2653 |

The images contain Python 3.14.7. The published vulnerability records name
3.15.0rc2 as the fixed version for the three 2026 CVEs, and 3.15.0a6 for
CVE-2025-15367. Python 3.14.8 is not released as of this inventory. The 3.14
branch has upstream patches for the three 2026 issues, but those are not yet
in the stable runtime being distributed.

Switching to an older Debian runtime does not eliminate all four findings:
[Debian's CVE-2025-15367 tracker](https://security-tracker.debian.org/tracker/CVE-2025-15367)
explicitly records that the POP3 command-validation change was not backported
to older Python releases because of compatibility concerns. Both maintained
3.13 and 3.14 packages are still marked vulnerable. A production migration
to a release candidate requires a separate runtime compatibility decision;
it is not an appropriate automatic dependency patch.

Upstream records:

- [CVE-2026-17084: StringPrep Unicode attributes](https://github.com/advisories/GHSA-w246-x8qv-r8f5)
- [CVE-2026-15806: HTTPPasswordMgr credential scope](https://github.com/advisories/GHSA-2v69-2w5x-455j)
- [CVE-2025-15367: POP3 command injection](https://github.com/advisories/GHSA-g82h-mgfp-jx8g)
- [CVE-2026-15310: unbounded ZIP decompression](https://github.com/advisories/GHSA-xj79-6hh5-9w6q)
