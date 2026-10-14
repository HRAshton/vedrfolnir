# Verify distributed artifacts

[← Documentation](README.md) · [Release contract](release-contract.md)

After a qualified GitHub Release exists, download its wheel/sdist,
`SHA256SUMS`, `release-manifest.json` and `container.spdx.json` from that exact
release. Verify every downloaded asset from the same directory:

~~~bash
sha256sum -c SHA256SUMS
gh attestation verify ./actual-package.whl --repo HRAshton/vedrfolnir
gh attestation verify ./actual-source.tar.gz --repo HRAshton/vedrfolnir
~~~

Replace example names with the actual release assets. The manifest records
package version, reviewed Git SHA, file sizes/hashes and immutable GHCR digest.
Verify these agree with the tag and GitHub Release body; then pull by digest:

~~~bash
docker pull ghcr.io/hrashton/vedrfolnir@sha256:ACTUAL_DIGEST
gh attestation verify oci://ghcr.io/hrashton/vedrfolnir@sha256:ACTUAL_DIGEST --repo HRAshton/vedrfolnir
~~~

The read-only SBOM job scans the **published** digest, including the shipped OS,
Python runtime and resolved dependencies. Its SPDX JSON and provenance are
attested against the same digest. Privileged signing jobs consume qualified
files and do not execute repository build code. Packages copied to GitHub get
GitHub attestations; PyPI Trusted Publishing provides PyPI digital attestations
by default. An attestation identifies origin, not correctness or freedom from
vulnerabilities. Review advisories separately.

These commands are post-publication verification examples, not evidence that
an RC or authenticated provenance exists yet. Owner setup and end-to-end consumer
rehearsal remain release gates. Do not add a provenance badge before verification.
