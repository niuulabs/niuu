# Image build provenance

A container image built by Niuu's CI carries signed GitHub build provenance
(a SLSA v1 attestation). Residents ask the platform whether an image digest is
provenance-bound with `volundr_git` `verify_image_provenance`; the platform
fetches the attestation and verifies it itself, so a resident's tools need no
network path to GitHub's attestation storage.

## What produces attestations

- `.github/workflows/reusable-container-build.yaml` has an `attest` job that runs
  after the multi-arch manifests are published (feature builds and pull-request
  builds). It attests the manifest list digest the first `manifest-tags` entry
  points at.
- `.github/workflows/dev.yaml` has its own `attest` job for the `dev-<sha>`
  manifest it publishes itself. A promoted run re-tags digests a pull request
  already attested, so it makes none.
- Both use `.github/actions/attest-container-image`, which calls
  `actions/attest-build-provenance` with `push-to-registry: true`.
- Release images (`release.yaml`) are signed with cosign, not attested; this
  feature does not cover them.

Every caller of the reusable workflow must grant `id-token: write` and
`attestations: write` besides `packages: write`.

## What the platform checks

The route is `GET /api/v1/niuu/repos/provenance?repo_url=…&digest=sha256:…`.
For each attestation GitHub holds for the digest in that repository, Sigstore
verification checks the certificate chain, the transparency-log inclusion, the
DSSE signature and that the digest is one of the signed subjects, against the
Sigstore public-good trust root (TUF, refreshed on every verification). The
signing certificate must satisfy all of:

| Check | Value |
|---|---|
| OIDC issuer | `https://token.actions.githubusercontent.com` |
| Source repository | the repository being asked about |
| Signer identity (SAN, equal to Build Signer URI) | `https://github.com/<owner>/<repo>/<path>@<ref>` with `<path>` one of the signing workflows |

The identity of a job in a reusable workflow is the **reusable workflow's own
file** (the OIDC `job_workflow_ref`), whichever workflow called it, so one path
covers every caller. The `<ref>` is not pinned; it is returned as a verified fact
(`ref`, with `commit_sha`, `workflow` and `run_url`) for the caller to judge.

Outcomes: `200` verified; `404` no attestation exists; `422` attestations exist
but none verified (the detail lists why) or the digest is malformed; `501` the
Git host has no attestation API (GitLab); `503` verification is disabled or the
trust root could not be loaded; `502` the Git host or its bundle storage failed.

## Configuration

`git.provenance` (Helm: `git.provenance` in the volundr and niuu-shared charts):

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | `false` answers the endpoint with 503. `true` without the `provenance` extra installed is fatal at startup. |
| `kwargs.issuer` | GitHub Actions issuer | OIDC issuer the certificate must name. |
| `kwargs.workflow_paths` | `reusable-container-build.yaml`, `dev.yaml` | Workflow files allowed to sign. Renaming either file means changing this in the same change. |
| `kwargs.trusted_root_file` | empty | A Sigstore `trusted_root.json` for clusters that cannot reach `tuf-repo-cdn.sigstore.dev`. |
| `bundle_fetch_timeout_seconds` | `30` | Timeout for downloading a bundle from GitHub's storage. |

Setting `kwargs` replaces all of its defaults. The platform needs egress to
`tuf-repo-cdn.sigstore.dev` (unless `trusted_root_file` is set) and to the host
named by an attestation's `bundle_url`; the process user needs a writable
`XDG_DATA_HOME` and `XDG_CACHE_HOME` (the charts point them at `/tmp`).

## Limits

- Public repositories only: attestations of private repositories are signed by
  GitHub's private Sigstore instance, which the public-good trust root rejects.
- GitHub only; GitLab answers `501`.
- Images built before attestations were enabled have none (`404`).
