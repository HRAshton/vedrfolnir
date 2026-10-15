# Automated maintenance images

Every Tuesday, the scheduled workflow compares the **tracked image build inputs**
(Dockerfile, packaged Python source/metadata, runtime constraints, bundled
examples and SQL queries) with the last successfully promoted GHCR `edge` image.

- **Unchanged inputs:** skip building, publishing, attesting and promoting, but
  still run the container vulnerability scan against the existing `edge` image.
- **Changed inputs:** run tests, build and smoke-test a candidate, enforce
  vulnerability policy, attest its image digest, then promote to `edge`.
- **Absent or legacy unlabelled `edge`:** build once to establish the
  `org.vedrfolnir.image-input-sha256` label.
- **Registry or authentication failure:** fail closed; never interpret an
  unknown error as permission to publish.

The fingerprint is SHA-256 of deterministic tracked Git tree entries, **not**
the latest commit SHA or the last scheduled workflow execution. A prior failed
attempt therefore cannot cause the next run to skip. Unshipped docs, CI and
dev-only changes do not cause unnecessary container publication.

The fingerprint does not cover drifting Debian apt repository content. For
urgent OS security rebuilds or deliberate image refreshes, manually dispatch
this workflow on protected `master` with `force=true`. The weekly unchanged
image scan still detects new vulnerability findings without republishing.

The mutable `ghcr.io/hrashton/vedrfolnir:edge` remains an opt-in experimental
preview: pin digests where possible. The workflow does not modify `v*` release
tags, `latest`, PyPI or users' running installations. The candidate → attest
→ promote sequence remains unchanged; failed qualification/attestation leaves
the previous `edge` untouched. Indeterminate registry writes require review.

The separate read-only release assessment reports `no-distribution`,
`image-only`, or `package-and-image` changes since the latest reachable tag.
This **does not authorize publication**. The existing conservative release
eligibility gate and reviewed versioned release pipeline remain intact.
