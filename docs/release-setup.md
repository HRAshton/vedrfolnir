# Trusted release setup and recovery

[← Documentation](README.md) · [Security setup](security-setup.md) · [Release contract](release-contract.md)

Complete the active branch/tag protections and green merged-master checks first.
Create GitHub environment `release` with required reviewers and permitted `v*`
tag deployments. The authorization guard reads actual GitHub rules/environment
settings and fails if protections/reviewers are absent or inaccessible. It also
requires the tag/version/source SHA to match a commit reachable from `master`.
No PR or arbitrary branch push publishes.

**Release ruleset verification requires a protected credential.** GitHub
returns `bypass_actors` only to callers with write access to the ruleset.
Create a separate GitHub Actions environment named `release-ruleset-audit`
with required reviewers and deployments restricted to `v*` tags. Create a
short-lived fine-grained PAT limited to repository `HRAshton/vedrfolnir`
with repository `Administration: write` access, and store it solely as
the environment secret `RELEASE_RULESET_AUDIT_TOKEN`. Rotate on expiry.
Never put this credential in a repository-wide secret.

The read-only `authorize` job validates the tag, source commit and
structural branch, tag, check and environment protections. A separate job
`verify-tag-rules` has approval-gated access to the stronger token.
It runs only reviewed inline Python (no checkout, dependencies or external
actions), verifies live tag rulesets including inherited ones, and refuses
missing, redacted, malformed or nonempty bypass lists. It does not print the
credential or the bypass list. `qualify` requires both jobs to pass;
all publication depends on `qualify`. Missing/expired/unauthorized
credentials fail closed. The strong credential is never supplied to a build or
publish job. Review release-workflow changes and restrict environment reviewers,
because workflow code is part of this trust boundary.

Confirm/control the PyPI name immediately before configuring Trusted Publishing
for owner `HRAshton`, repository `vedrfolnir`, workflow `release.yml`, environment
`release`. No stored PyPI token is used. A pending publisher does not reserve a
name. GHCR package must permit public anonymous pulls before pulled-image smoke;
verify package visibility and repository linking with the owner. Establish the
namespace before the first protected release if missing-tag inspection cannot
distinguish authentication failures from absence.

The GHCR namespace was bootstrapped before the first RC. Keep the existing
package publicly readable and the repository's Actions access scoped to the
publication jobs. The one-time `:bootstrap` workflow has been retired; it is
not part of routine publication. Validate published version and digest identity
through the release workflow and the consumer rehearsal.

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
workflow is not a claim that an RC has been published. The release workflow now
generates an image SBOM and attests exact package and image identities before
PyPI/GitHub publication. Follow [verification](verification.md) and complete
the consumer rehearsal before declaring the RC qualified.
