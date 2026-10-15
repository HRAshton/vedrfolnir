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

Qualification publishes a uniquely named `edge-candidate-<run>-<attempt>`
image first, attests its exact digest, then promotes that verified digest to
`edge`. Failed qualification or signing therefore never overwrites the
previous `edge`. A failed promotion preserves the previously available
`edge` unless registry writes partially completed. Every completed promotion
checks that the registry digest matches the signed candidate; investigate
conflicting or indeterminate writes rather than retrying with guessed state.
Candidate tags remain as an audit trail and can be expired separately.

Schedule timing is best effort; GitHub may delay scheduled workflows. For an
urgent preview, use the explicit workflow dispatch on the protected default branch.
