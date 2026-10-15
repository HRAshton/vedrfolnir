# Automated maintenance images

The scheduled workflow runs on Tuesday at 06:17 UTC after the Monday Renovate
window. It checks out the protected `master` branch, runs real River tests, builds
the Linux/amd64 container, exercises demo and CSV startup/restart, and enforces
the existing vulnerability policy. Only the tested image archive crosses into
the narrowly scoped GHCR publication job.

The mutable `ghcr.io/hrashton/vedrfolnir:edge` tag is an **experimental preview**
for maintainers, not a versioned release. A successful run records the
immutable digest in its Actions summary and attests that digest. Consumers
should opt in explicitly and pin the digest; the workflow does not restart
any user's deployment, update PyPI, create tags or change `latest`.

When qualification fails, the existing `edge` image remains the last
successfully pushed preview. A failure in the provenance-attestation job means
the image has already been pushed but its attestation may be incomplete; do
not treat that run as a fully qualified release.

Schedule timing is best effort; GitHub may delay scheduled workflows. For an
urgent preview, use the explicit workflow dispatch on the protected default branch.
