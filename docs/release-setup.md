# Trusted release setup and recovery

[← Documentation](README.md) · [Security setup](security-setup.md) · [Release contract](release-contract.md)

Complete the active branch/tag protections and green merged-master checks first.
Create GitHub environment `release` with required reviewers and permitted `v*`
tag deployments. The authorization guard reads actual GitHub rules/environment
settings and fails if protections/reviewers are absent or inaccessible. It also
requires the tag/version/source SHA to match a commit reachable from `master`.
No PR or arbitrary branch push publishes.

**Bypass verification is fail-closed.** GitHub returns the `bypass_actors`
field only to API callers with write access to the ruleset. The authorization
guard requires that field to be present and exactly `[]` for the matching
immutable `v*` tag ruleset. A normal read-only `GITHUB_TOKEN` can receive a
redacted response and will therefore **block release**, even when the ruleset
has no bypass actors. A secure, independently authorized verification mechanism
must be arranged before publishing; do not treat missing data as an empty list
or grant repository administration write access to untrusted release code.

Confirm/control the PyPI name immediately before configuring Trusted Publishing
for owner `HRAshton`, repository `vedrfolnir`, workflow `release.yml`, environment
`release`. No stored PyPI token is used. A pending publisher does not reserve a
name. GHCR package must permit public anonymous pulls before pulled-image smoke;
verify package visibility and repository linking with the owner. Establish the
namespace before the first protected release if missing-tag inspection cannot
distinguish authentication failures from absence.

The single release workflow builds/tests with read-only rights, transfers exact
qualified package/image artifacts, and separates approved jobs for OCI upload,
pulled-digest smoke, PyPI OIDC and GitHub Release publication. Privileged package
jobs do not execute project code. Immutable OCI tags are rejected if already
present; network/auth failures never count as absence. RCs never promote `latest`.

Partial publication is explicit: do not overwrite a package/image version. Check
existing release/package/image digests against the qualified artifacts; if any
channel is consumed or conflicts, review and create a new `rcN` on an approved
commit. The initial pipeline deliberately fails on an existing image tag rather
than attempting unsafe recovery. Test retries on disposable rehearsal artifacts.

Owner settings, PyPI publisher and actual publication are pending gates. This
workflow is not a claim that an RC has been published. Supply-chain attestations,
SBOM and consumer verification are added by the next PRs before any public RC.
